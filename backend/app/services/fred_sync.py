import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime

import httpx

from app.catalog.macro_series import catalog_codes
from app.providers.errors import ProviderError
from app.providers.fred import FredAdapter, FredVintage
from app.providers.fred_public import fetch_public_csv
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.repositories.macro import MacroRepository
from app.utils.time import utc_now

VintageFetcher = Callable[[str, date, date], Awaitable[tuple[FredVintage, ...]]]
_MAX_WINDOW_DAYS = 3660


async def sync_fred_series(
    repository: MacroRepository,
    *,
    series_code: str,
    observation_start: date,
    observation_end: date,
    fetch: VintageFetcher,
    fetched_at: datetime,
) -> int:
    """有密钥时 fetch 走带修订历史的 observations 接口，没有时走公开 CSV，由调用方决定。"""
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
