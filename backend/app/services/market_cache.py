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

    def clear(self) -> None:
        self._entries.clear()


class PacedGateway:
    """串行、限速的 GET/POST 出口。行情源一律直连，不走系统代理。"""

    def __init__(self, *, min_interval: float = 0.4, timeout: float = 12.0) -> None:
        self._min_interval = min_interval
        self._timeout = timeout
        self._lock = asyncio.Lock()
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
        async with self._lock:
            wait = self._last + self._min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
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
