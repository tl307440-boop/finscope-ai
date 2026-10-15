from datetime import date
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.industry import Industry, IndustryMembership
from app.db.models.market import Instrument
from app.repositories.market_bars import MarketBarRepository
from app.services.market_temperature import (
    PriceBar,
    TemperatureResult,
    market_temperature,
    sector_strength,
)
from app.utils.identifiers import display_symbol_for


def temperature_for(engine: Engine, pit: date) -> TemperatureResult:
    bars = MarketBarRepository(engine)
    provider_id = bars.ensure_akshare_provider()
    index_id = _instrument_id(engine, "SSE", "000300")
    if index_id is None:
        return market_temperature([], {})
    index_bars = _prices(bars, index_id, provider_id, pit)
    members: dict[str, list[PriceBar]] = {}
    for instrument_id, symbol in _stocks(engine):
        members[symbol] = _prices(bars, instrument_id, provider_id, pit)
    return market_temperature(index_bars, members)


def sectors_for(engine: Engine, pit: date) -> list[tuple[str, TemperatureResult]]:
    bars = MarketBarRepository(engine)
    provider_id = bars.ensure_akshare_provider()
    index_id = _instrument_id(engine, "SSE", "000300")
    index_bars = [] if index_id is None else _prices(bars, index_id, provider_id, pit)
    found: list[tuple[str, TemperatureResult]] = []
    for industry_id, name in _industries(engine):
        members: dict[str, list[PriceBar]] = {}
        for instrument_id, symbol in _members(engine, industry_id):
            members[symbol] = _prices(bars, instrument_id, provider_id, pit)
        found.append((name, sector_strength(index_bars, members)))
    return found


def _prices(
    bars: MarketBarRepository,
    instrument_id: UUID,
    provider_id: UUID,
    pit: date,
) -> list[PriceBar]:
    rows, _truncated = bars.list_daily(
        instrument_id=instrument_id,
        provider_id=provider_id,
        adjustment="none",
        start=None,
        end=None,
        pit=pit,
        limit=3000,
    )
    return [PriceBar(row.trade_date, row.close, row.amount) for row in rows]


def _instrument_id(engine: Engine, exchange: str, symbol: str) -> UUID | None:
    with Session(engine) as session:
        row = session.scalars(
            select(Instrument).where(Instrument.exchange == exchange, Instrument.symbol == symbol)
        ).one_or_none()
        return None if row is None else row.id


def _stocks(engine: Engine) -> list[tuple[UUID, str]]:
    with Session(engine) as session:
        rows = session.scalars(
            select(Instrument).where(Instrument.instrument_type == "STOCK")
        ).all()
        return [(row.id, display_symbol_for(row.exchange, row.symbol)) for row in rows]


def _industries(engine: Engine) -> list[tuple[UUID, str]]:
    with Session(engine) as session:
        rows = session.scalars(select(Industry).order_by(Industry.name)).all()
        return [(row.id, row.name) for row in rows]


def _members(engine: Engine, industry_id: UUID) -> list[tuple[UUID, str]]:
    with Session(engine) as session:
        links = session.scalars(
            select(IndustryMembership).where(IndustryMembership.industry_id == industry_id)
        ).all()
        found: list[tuple[UUID, str]] = []
        for link in links:
            instrument = session.get(Instrument, link.instrument_id)
            if instrument is None:
                continue
            found.append(
                (instrument.id, display_symbol_for(instrument.exchange, instrument.symbol))
            )
        return found
