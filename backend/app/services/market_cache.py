"""公开行情接口的进程内缓存与限速。

- 同一 key 并发只回源一次；
- 回源失败但有旧值时返回旧值并标记 stale，不把上游抖动放大成整页报错；
- 所有回源经同一把锁串行、两次之间至少间隔 `min_interval` 秒，避免触发上游频控。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

import httpx

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.utils.time import utc_now

T = TypeVar("T")


@dataclass(frozen=True)
class Cached[V]:
    value: V
    fetched_at: datetime
    stale: bool
    stale_reason: str | None = None


@dataclass
class _Entry[V]:
    value: V
    fetched_at: datetime
    expires_at: float


class TtlCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, _Entry[Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(
        self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[T]]
    ) -> Cached[T]:
        entry: _Entry[T] | None = self._entries.get(key)
        if entry is not None and entry.expires_at > self._clock():
            return Cached(value=entry.value, fetched_at=entry.fetched_at, stale=False)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > self._clock():
                return Cached(value=entry.value, fetched_at=entry.fetched_at, stale=False)
            try:
                value = await loader()
            except ProviderError as error:
                if entry is None:
                    raise
                return Cached(
                    value=entry.value,
                    fetched_at=entry.fetched_at,
                    stale=True,
                    stale_reason=error.message,
                )
            fetched_at = utc_now()
            self._entries[key] = _Entry(
                value=value, fetched_at=fetched_at, expires_at=self._clock() + ttl_seconds
            )
            return Cached(value=value, fetched_at=fetched_at, stale=False)

    def peek(self, key: str) -> Cached[Any] | None:
        """不触发回源，直接看缓存里有没有值；已过期的按 stale 返回。"""

        entry: _Entry[Any] | None = self._entries.get(key)
        if entry is None:
            return None
        expired = entry.expires_at <= self._clock()
        return Cached(
            value=entry.value,
            fetched_at=entry.fetched_at,
            stale=expired,
            stale_reason="正在后台刷新" if expired else None,
        )

    def seed(self, key: str, value: Any, fetched_at: datetime) -> None:
        """用持久化的旧值填充还没有的 key；立即视为过期，下一次读取仍会尝试回源。"""

        if key not in self._entries:
            self._entries[key] = _Entry(value=value, fetched_at=fetched_at, expires_at=0.0)

    def clear(self) -> None:
        self._entries.clear()


class PacedGateway:
    """限速的 GET/POST 出口。行情源一律直连，不走系统代理。

    `concurrency=1`（默认）时完全串行；大于 1 时允许多个请求同时在途，
    但相邻两次发起之间仍至少间隔 `min_interval` 秒。
    """

    def __init__(
        self, *, min_interval: float = 0.4, timeout: float = 12.0, concurrency: int = 1
    ) -> None:
        self._min_interval = min_interval
        self._timeout = timeout
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._pace = asyncio.Lock()
        self._last = 0.0

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        return await self._send(lambda http: http.get(url, headers))

    async def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        content_type: str = "application/json",
    ) -> bytes:
        merged = {**headers, "Content-Type": content_type}
        return await self._send(lambda http: http.post(url, content=body, headers=merged))

    async def _send(self, call: Callable[[ResilientHttpClient], Awaitable[bytes]]) -> bytes:
        async with self._slots:
            async with self._pace:
                wait = self._last + self._min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(self._timeout), trust_env=False
                ) as raw:
                    http = ResilientHttpClient(
                        HttpxTransport(raw), sleeper=asyncio.sleep, max_attempts=2
                    )
                    return await call(http)
            finally:
                self._last = time.monotonic()


_shared: PacedGateway | None = None


def shared_gateway() -> PacedGateway:
    """进程内所有行情服务共用一个限速出口，几个页面同时打开也不会叠加请求频率。"""

    global _shared
    if _shared is None:
        _shared = PacedGateway()
    return _shared
