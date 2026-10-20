"""公开行情接口的进程内缓存与限速。

- 同一 key 并发只回源一次；
- 可选 stale-while-revalidate（`grace_seconds`）：过期但仍在宽限期内的值立刻返回，
  回源放到后台单飞完成，用户请求不再为上游慢 / 抖动买单；
- 回源失败但有旧值时返回旧值并标记 stale，不把上游抖动放大成整页报错；
  失败后在退避期内（`failure_backoff_seconds`）不再重复打一个已经挂掉的上游；
- 没有旧值的 key 回源失败后同样短暂记住失败，并发请求不会各自把挂掉的上游重等一遍；
- 回源经按主机隔离的并发 / 限速出口发出，相邻两次发起至少间隔 `min_interval` 秒，
  避免触发上游频控；一个主机变慢不会拖住其它来源；出口复用长连接，不再每个请求重新握手 TLS。
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.utils.time import utc_now

T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cached[V]:
    value: V
    fetched_at: datetime
    stale: bool
    stale_reason: str | None = None
    # 值已过期但仍在宽限期内：本次直接用它，后台正在刷新。不是故障，所以 stale 仍为 False。
    refreshing: bool = False


@dataclass
class _Entry[V]:
    value: V
    fetched_at: datetime
    expires_at: float
    # 最近一次回源失败后，在这个时刻之前不再尝试回源。
    retry_at: float = 0.0
    error: str | None = None


class TtlCache:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        grace_seconds: float = 0.0,
        failure_backoff_seconds: float = 0.0,
    ) -> None:
        self._clock = clock
        self._grace = grace_seconds
        self._backoff = failure_backoff_seconds
        self._entries: dict[str, _Entry[Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._refreshing: dict[str, asyncio.Task[None]] = {}
        # 还没有任何旧值的 key 回源失败后的短暂记忆：退避期内直接复述失败，
        # 不让每个并发请求都再把一个挂掉的上游完整等一遍。
        self._failures: dict[str, tuple[float, ProviderError]] = {}

    async def get(
        self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[T]]
    ) -> Cached[T]:
        entry: _Entry[T] | None = self._entries.get(key)
        now = self._clock()
        if entry is not None and entry.expires_at > now:
            return Cached(value=entry.value, fetched_at=entry.fetched_at, stale=False)
        if entry is not None:
            if now < entry.retry_at:
                return self._stale(entry, entry.error)
            if now < entry.expires_at + self._grace:
                self._refresh_in_background(key, ttl_seconds, loader)
                if entry.error is not None:
                    # 上一次刷新已经失败过：如实标成旧数据，而不是装作只是在例行刷新。
                    return self._stale(entry, entry.error)
                return Cached(
                    value=entry.value,
                    fetched_at=entry.fetched_at,
                    stale=False,
                    refreshing=True,
                )
        if entry is None:
            self._raise_if_recently_failed(key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > self._clock():
                return Cached(value=entry.value, fetched_at=entry.fetched_at, stale=False)
            if entry is None:
                # 排队等锁期间前一个请求可能刚失败过：别再把同一个挂掉的上游重等一遍。
                self._raise_if_recently_failed(key)
            try:
                value = await loader()
            except ProviderError as error:
                if entry is None:
                    if self._backoff > 0:
                        self._failures[key] = (self._clock() + self._backoff, error)
                    raise
                self._record_failure(key, error.message)
                return self._stale(entry, error.message)
            return self._store(key, value, ttl_seconds)

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

    def put(self, key: str, value: Any, fetched_at: datetime, ttl_seconds: float) -> None:
        """直接写入一个有效期内的值（例如管理员从外部推送的数据）。"""

        self._entries[key] = _Entry(
            value=value, fetched_at=fetched_at, expires_at=self._clock() + ttl_seconds
        )

    def seed(self, key: str, value: Any, fetched_at: datetime) -> None:
        """用持久化的旧值填充还没有的 key；立即视为过期且不在宽限期内，下一次读取会阻塞回源。"""

        if key not in self._entries:
            self._entries[key] = _Entry(
                value=value, fetched_at=fetched_at, expires_at=float("-inf")
            )

    def clear(self) -> None:
        self._entries.clear()
        self._failures.clear()

    def _stale(self, entry: _Entry[T], reason: str | None) -> Cached[T]:
        return Cached(
            value=entry.value,
            fetched_at=entry.fetched_at,
            stale=True,
            stale_reason=reason,
        )

    def _raise_if_recently_failed(self, key: str) -> None:
        failed = self._failures.get(key)
        if failed is None:
            return
        retry_at, error = failed
        if self._clock() >= retry_at:
            del self._failures[key]
            return
        raise ProviderError(
            code=error.code,
            message=error.message,
            status_code=error.status_code,
            attempts=error.attempts,
        )

    def _store(self, key: str, value: T, ttl_seconds: float) -> Cached[T]:
        self._failures.pop(key, None)
        fetched_at = utc_now()
        self._entries[key] = _Entry(
            value=value, fetched_at=fetched_at, expires_at=self._clock() + ttl_seconds
        )
        return Cached(value=value, fetched_at=fetched_at, stale=False)

    def _record_failure(self, key: str, reason: str) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.retry_at = self._clock() + self._backoff
            entry.error = reason

    def _refresh_in_background(
        self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[Any]]
    ) -> None:
        running = self._refreshing.get(key)
        if running is not None and not running.done():
            return
        task = asyncio.ensure_future(self._refresh(key, ttl_seconds, loader))
        self._refreshing[key] = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._refreshing.get(key) is done:
                del self._refreshing[key]

        task.add_done_callback(finished)

    async def _refresh(
        self, key: str, ttl_seconds: float, loader: Callable[[], Awaitable[Any]]
    ) -> None:
        async with self._locks.setdefault(key, asyncio.Lock()):
            entry = self._entries.get(key)
            if entry is not None and entry.expires_at > self._clock():
                return
            try:
                value = await loader()
            except ProviderError as error:
                self._record_failure(key, error.message)
                return
            except Exception:
                logger.warning("background refresh failed: %s", key, exc_info=True)
                self._record_failure(key, "后台刷新出错")
                return
            self._store(key, value, ttl_seconds)


class _Lane:
    """一个上游主机的并发槽与节奏：并发上限和两次发起的最小间隔都按主机算。"""

    def __init__(self, concurrency: int) -> None:
        self.slots = asyncio.Semaphore(concurrency)
        self.pace = asyncio.Lock()
        self.last = 0.0


class PacedGateway:
    """限速的 GET/POST 出口。行情源一律直连，不走系统代理。

    - 并发上限与 `min_interval` 都按上游主机隔离（舱壁）：一个主机变慢或挂掉，
      只占用它自己的槽位，不会拖住其它来源排队；`concurrency=1`（默认）时每个主机内串行。
    - 同一事件循环内复用一个带 keep-alive 的 httpx 客户端，省掉反复握手；
      连接超时单独设短，死掉的上游尽快失败而不是占满整个读超时。
    - 每个请求（含重试）有总时限，慢到没有尽头的上游不会无限占着槽位。
    """

    def __init__(
        self, *, min_interval: float = 0.4, timeout: float = 12.0, concurrency: int = 1
    ) -> None:
        self._min_interval = min_interval
        self._timeout = timeout
        self._deadline = timeout * 2 + 1  # 两次尝试加一点余量
        self._concurrency = max(1, concurrency)
        self._lanes: dict[str, _Lane] = {}
        self._raw: httpx.AsyncClient | None = None
        self._http: ResilientHttpClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        _gateways.add(self)

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        return await self._send(lambda http: http.get(url, headers), _host(url))

    async def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        content_type: str = "application/json",
    ) -> bytes:
        merged = {**headers, "Content-Type": content_type}
        return await self._send(
            lambda http: http.post(url, content=body, headers=merged), _host(url)
        )

    async def aclose(self) -> None:
        raw, loop = self._raw, self._loop
        self._raw, self._http, self._loop = None, None, None
        self._lanes.clear()
        # 别的（已结束的）事件循环里建的连接没法在这里关，丢给垃圾回收即可。
        if raw is not None and loop is asyncio.get_running_loop():
            await raw.aclose()

    def _client(self) -> ResilientHttpClient:
        loop = asyncio.get_running_loop()
        if self._http is None or self._loop is not loop:
            # 事件循环换了（测试里常见）：连接和锁都绑在旧循环上，整体重建。
            self._lanes.clear()
            self._raw = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 6.0)),
                limits=httpx.Limits(
                    max_connections=64,
                    max_keepalive_connections=32,
                    keepalive_expiry=20.0,
                ),
                trust_env=False,
            )
            self._http = ResilientHttpClient(
                HttpxTransport(self._raw), sleeper=asyncio.sleep, max_attempts=2
            )
            self._loop = loop
        return self._http

    async def _send(
        self, call: Callable[[ResilientHttpClient], Awaitable[bytes]], host: str = ""
    ) -> bytes:
        http = self._client()
        lane = self._lanes.setdefault(host, _Lane(self._concurrency))
        async with lane.slots:
            async with lane.pace:
                wait = lane.last + self._min_interval - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                lane.last = time.monotonic()
            try:
                async with asyncio.timeout(self._deadline):
                    return await call(http)
            except TimeoutError as error:
                raise ProviderError(
                    code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504
                ) from error
            finally:
                lane.last = time.monotonic()


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


_gateways: weakref.WeakSet[PacedGateway] = weakref.WeakSet()
_shared: PacedGateway | None = None


def shared_gateway() -> PacedGateway:
    """进程内所有行情服务共用一个限速出口，几个页面同时打开也不会叠加请求频率。

    每个上游主机最多 4 路并发、相邻两次发起至少隔 0.2 秒：总览一次要打几十个上游请求，
    完全串行时首屏要排队几十秒；而对单个主机的频率仍然受控。
    """

    global _shared
    if _shared is None:
        _shared = PacedGateway(min_interval=0.2, concurrency=4)
    return _shared


async def close_gateways() -> None:
    """进程退出时关闭所有出口持有的长连接。"""

    await asyncio.gather(*(gateway.aclose() for gateway in list(_gateways)), return_exceptions=True)


def market_cache() -> TtlCache:
    """行情聚合服务用的缓存：10 分钟宽限期 + 10 秒失败退避。"""

    return TtlCache(grace_seconds=600.0, failure_backoff_seconds=10.0)
