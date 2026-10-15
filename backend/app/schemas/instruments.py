from datetime import date
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel

from app.repositories.instruments import InstrumentRecord
from app.utils.calendar import CalendarStatus, calendar_status
from app.utils.identifiers import SearchKey, parse_search_key


class InstrumentHit(BaseModel):
    id: UUID
    canonical_code: str
    display_symbol: str
    exchange: str
    symbol: str
    name_cn: str
    name_en: str
    instrument_type: Literal["STOCK", "INDEX", "ETF"]
    market: Literal["CN", "US"]
    cik: str | None
    currency: str


class SearchData(BaseModel):
    query: str
    unique: bool
    empty_message: str | None = None
    results: list[InstrumentHit]


class CalendarDay(BaseModel):
    exchange: str
    session_date: date
    status: CalendarStatus
    is_open: bool | None
    source: str | None
    weekday_inferred: bool = False


def search_instruments(
    records: list[InstrumentRecord],
    *,
    query: str,
    limit: int,
) -> SearchData:
    hits = [_hit(record) for record in records[:limit]]
    if not hits:
        return SearchData(query=query, unique=False, empty_message="暂无该证券", results=[])
    return SearchData(query=query, unique=len(hits) == 1, results=hits)


def build_search_key(query: str) -> SearchKey:
    return parse_search_key(query)


def build_calendar_day(
    exchange: str,
    session_date: date,
    is_open: bool | None,
    source: str | None,
) -> CalendarDay:
    status = calendar_status(is_open)
    return CalendarDay(
        exchange=exchange,
        session_date=session_date,
        status=status,
        is_open=None if status == "unknown" else is_open,
        source=source,
        weekday_inferred=False,
    )


def _hit(record: InstrumentRecord) -> InstrumentHit:
    instrument_type = cast(Literal["STOCK", "INDEX", "ETF"], record.instrument_type)
    market = cast(Literal["CN", "US"], record.market)
    return InstrumentHit(
        id=record.id,
        canonical_code=record.canonical_code,
        display_symbol=record.display_symbol,
        exchange=record.exchange,
        symbol=record.symbol,
        name_cn=record.name_cn,
        name_en=record.name_en,
        instrument_type=instrument_type,
        market=market,
        cik=record.cik,
        currency=record.currency,
    )
