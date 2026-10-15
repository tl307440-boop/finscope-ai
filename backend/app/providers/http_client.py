from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from app.providers.errors import ProviderError, ProviderTimeout

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class OutboundRequest:
    method: str
    url: str


@dataclass(frozen=True)
class RawResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class Transport:
    async def send(self, request: OutboundRequest) -> RawResponse:
        raise NotImplementedError


class ResilientHttpClient:
    """有限次重试。只对 429、503 和超时重试，不会无限循环。"""

    def __init__(
        self,
        transport: Transport,
        *,
        sleeper: Sleep,
        max_attempts: int = 3,
        backoff_cap_seconds: float = 2,
        min_interval_seconds: float = 0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")
        self.transport = transport
        self.sleeper = sleeper
        self.max_attempts = max_attempts
        self.backoff_cap_seconds = backoff_cap_seconds
        self.min_interval_seconds = min_interval_seconds
        self._last_started = False

    async def get(self, url: str) -> bytes:
        await self._pace()
        last_error: ProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self.transport.send(OutboundRequest(method="GET", url=url))
            except ProviderTimeout:
                last_error = ProviderError(
                    code="UPSTREAM_TIMEOUT",
                    message="上游超时",
                    status_code=504,
                    attempts=attempt,
                )
            else:
                if response.status_code == 200:
                    return response.content
                last_error = _error_for_status(response.status_code, attempt)
                if response.status_code not in {429, 503}:
                    raise last_error
                if attempt < self.max_attempts:
                    await self.sleeper(_delay(response.headers, attempt, self.backoff_cap_seconds))
                continue
            if attempt < self.max_attempts:
                await self.sleeper(_delay({}, attempt, self.backoff_cap_seconds))
        if last_error is None:
            raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", attempts=0)
        raise last_error

    async def _pace(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        if self._last_started:
            await self.sleeper(self.min_interval_seconds)
        self._last_started = True


def _error_for_status(status_code: int, attempts: int) -> ProviderError:
    if status_code == 429:
        return ProviderError(
            code="RATE_LIMITED",
            message="上游限流",
            status_code=429,
            attempts=attempts,
        )
    if status_code == 503:
        return ProviderError(
            code="UPSTREAM_UNAVAILABLE",
            message="上游暂时不可用",
            status_code=503,
            attempts=attempts,
        )
    return ProviderError(
        code="UPSTREAM_ERROR",
        message="上游请求失败",
        status_code=status_code,
        attempts=attempts,
    )


def _delay(headers: Mapping[str, str], attempt: int, cap: float) -> float:
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if raw is not None:
        try:
            requested = float(raw)
        except ValueError:
            requested = None
        if requested is not None:
            bounded = requested if requested > 0 else 0.0
            return cap if bounded > cap else bounded
    exponential = 0.05 * (2 ** (attempt - 1))
    return cap if exponential > cap else exponential
