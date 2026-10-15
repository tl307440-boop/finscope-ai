import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime

import httpx

from app.catalog.macro_series import catalog_codes
from app.providers.errors import ProviderError
from app.providers.fred import FredAdapter, FredVintage
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.repositories.macro import MacroRepository

VintageFetcher = Callable[[str, date, date], Awaitable[tuple[FredVintage, ...]]]
_MAX_WINDOW_DAYS = 3660


async def sync_fred_series(
    repository: MacroRepository,
    *,
    api_key: str,
    series_code: str,
    observation_start: date,
    observation_end: date,
    fetch: VintageFetcher,
    fetched_at: datetime,
) -> int:
    if not api_key.strip():
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="未配置 FRED 密钥，数据源不可用",
            status_code=503,
        )
    if series_code not in catalog_codes():
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
        )
    if observation_end < observation_start or _window_days(observation_start, observation_end) > (
        _MAX_WINDOW_DAYS
    ):
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="观测区间无效或超过 10 年",
            status_code=422,
        )
    vintages = await fetch(series_code, observation_start, observation_end)
    return repository.upsert_vintages(series_code, vintages, fetched_at=fetched_at)


def _window_days(start: date, end: date) -> int:
    return (end - start).days


async def fetch_from_fred(
    api_key: str,
    series_code: str,
    observation_start: date,
    observation_end: date,
) -> tuple[FredVintage, ...]:
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.2,
        )
        return await FredAdapter(http, api_key).fetch_vintages(
            series_code,
            observation_start,
            observation_end,
        )
