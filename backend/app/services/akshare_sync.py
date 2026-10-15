import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import httpx
from sqlalchemy import Engine

from app.providers.akshare_bars import (
    AdjustmentCode,
    dedupe_bars,
    kline_url,
    parse_klines,
    target_for,
)
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.repositories.instruments import InstrumentRepository
from app.repositories.market_bars import MarketBarRepository
from app.services.ingest import ingest_bars
from app.utils.identifiers import parse_search_key

KlineFetcher = Callable[[str, date, date, AdjustmentCode], Awaitable[bytes]]
_MAX_WINDOW_DAYS = 3660


async def fetch_klines(
    display_symbol: str,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> bytes:
    target = target_for(display_symbol)
    if target is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="这三只股票以外的日线尚未开放",
            status_code=422,
        )
    url = kline_url(
        exchange=target.exchange,
        symbol=target.symbol,
        start=start,
        end=end,
        adjustment=adjustment,
    )
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.2,
        )
        return await http.get(url)


def valid_window(start: date, end: date) -> bool:
    if end < start:
        return False
    return (end - start).days <= _MAX_WINDOW_DAYS


@dataclass(frozen=True)
class AkshareSyncOutcome:
    rows_written: int
    rows_rejected: int
    rows_for_review: int
    duplicates_skipped: int


def apply_kline_payload(
    engine: Engine,
    payload: bytes,
    *,
    display_symbol: str,
    adjustment: AdjustmentCode,
) -> AkshareSyncOutcome:
    target = target_for(display_symbol)
    if target is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="这三只股票以外的日线尚未开放",
            status_code=422,
        )
    parsed = parse_klines(payload, display_symbol=target.display_symbol, adjustment=adjustment)
    kept, skipped = dedupe_bars(parsed)
    instrument = _stock(engine, target.display_symbol)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    outcome = ingest_bars(
        kept,
        MarketBarRepository(engine),
        instrument_id=instrument,
        provider_id=provider_id,
    )
    if outcome.error is not None:
        raise outcome.error
    return AkshareSyncOutcome(
        rows_written=outcome.rows_written,
        rows_rejected=outcome.rows_rejected,
        rows_for_review=outcome.rows_for_review,
        duplicates_skipped=skipped,
    )


def _stock(engine: Engine, display_symbol: str) -> UUID:
    records = InstrumentRepository(engine).search(
        parse_search_key(display_symbol),
        market="CN",
        limit=5,
    )
    matches = [
        record
        for record in records
        if record.display_symbol == display_symbol and record.instrument_type == "STOCK"
    ]
    if len(matches) != 1:
        raise ProviderError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    return matches[0].id
