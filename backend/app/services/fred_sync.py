import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime

import httpx

from app.catalog.macro_series import seed_for
from app.core.config import get_settings
from app.providers.eastmoney_us_macro import LatestValue, fetch_indicator
from app.providers.errors import ProviderError
from app.providers.fred import FredAdapter, FredVintage
from app.providers.fred_public import fetch_public_csv
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.repositories.macro import MacroRepository
from app.utils.time import utc_now

VintageFetcher = Callable[[str, date, date], Awaitable[tuple[FredVintage, ...]]]
LatestFetcher = Callable[[str], Awaitable[tuple[LatestValue, ...]]]


async def sync_fred_series(
    repository: MacroRepository,
    *,
    series_code: str,
    observation_start: date,
    observation_end: date,
    fetch: VintageFetcher,
    fetched_at: datetime,
    fetch_latest: LatestFetcher | None = None,
) -> int:
    """美国宏观序列同步，按序列的数据源分派。

    FRED：有密钥时 fetch 走带修订历史的 observations 接口，
    没有时走公开 CSV，由调用方决定。
    东方财富（ISM PMI）：fetch_latest 只给最新值，版本由仓库按抓取日记录。
    """
    seed = seed_for(series_code)
    if seed is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
        )
    max_days = get_settings().macro_sync_max_window_days
    if observation_end < observation_start or _window_days(observation_start, observation_end) > (
        max_days
    ):
        raise ProviderError(
            code="VALIDATION_ERROR",
            message=f"观测区间无效或超过 {max_days} 天（MACRO_SYNC_MAX_WINDOW_DAYS）",
            status_code=422,
        )
    if seed.provider == "eastmoney":
        loader = fetch_latest or fetch_from_eastmoney
        points = [
            point
            for point in await loader(seed.source_key)
            if observation_start <= point.observation_date <= observation_end
        ]
        return repository.upsert_latest_values(series_code, points, fetched_at=fetched_at)
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
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.2,
        )
        if not api_key.strip():
            return await fetch_public_csv(
                http,
                series_code,
                observation_start,
                observation_end,
                fetched_at=utc_now(),
            )
        return await FredAdapter(http, api_key).fetch_vintages(
            series_code,
            observation_start,
            observation_end,
        )


async def fetch_from_eastmoney(indicator_id: str) -> tuple[LatestValue, ...]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.5,
        )
        return await fetch_indicator(http, indicator_id)
