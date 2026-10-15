import uuid
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from app.catalog.reference_data import CalendarSeed, InstrumentSeed
from app.db.models.market import Instrument, TradingCalendar
from app.utils.identifiers import SearchKey, canonical_code
from app.utils.time import utc_now


@dataclass(frozen=True)
class InstrumentRecord:
    id: UUID
    market: str
    exchange: str
    symbol: str
    display_symbol: str
    name_cn: str
    name_en: str
    instrument_type: str
    currency: str
    cik: str | None

    @property
    def canonical_code(self) -> str:
        return canonical_code(self.exchange, self.symbol)


class InstrumentRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_reference(self, seeds: tuple[InstrumentSeed, ...]) -> int:
        now = utc_now()
        written = 0
        with Session(self.engine) as session:
            for seed in seeds:
                existing = session.scalars(
                    select(Instrument).where(
                        Instrument.market == seed.market,
                        Instrument.exchange == seed.exchange,
                        Instrument.symbol == seed.symbol,
                    )
                ).one_or_none()
                if existing is None:
                    session.add(
                        Instrument(
                            id=uuid.uuid4(),
                            market=seed.market,
                            exchange=seed.exchange,
                            symbol=seed.symbol,
                            display_symbol=seed.display_symbol,
                            name_cn=seed.name_cn,
                            name_en=seed.name_en,
                            instrument_type=seed.instrument_type,
                            currency=seed.currency,
                            cik=seed.cik,
                            industry_id=None,
                            is_active=True,
                            list_date=None,
                            delist_date=None,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    written += 1
                else:
                    existing.name_cn = seed.name_cn
                    existing.name_en = seed.name_en
                    existing.display_symbol = seed.display_symbol
                    existing.instrument_type = seed.instrument_type
                    existing.cik = seed.cik
                    existing.updated_at = now
            session.commit()
        return written

    def upsert_calendar(self, seeds: tuple[CalendarSeed, ...]) -> int:
        now = utc_now()
        written = 0
        with Session(self.engine) as session:
            for seed in seeds:
                existing = session.scalars(
                    select(TradingCalendar).where(
                        TradingCalendar.exchange == seed.exchange,
                        TradingCalendar.session_date == seed.session_date,
                    )
                ).one_or_none()
                if existing is None:
                    session.add(
                        TradingCalendar(
                            id=uuid.uuid4(),
                            exchange=seed.exchange,
                            session_date=seed.session_date,
                            is_open=seed.is_open,
                            session_json={"source": seed.source},
                            source=seed.source,
                            updated_at=now,
                        )
                    )
                    written += 1
                else:
                    existing.is_open = seed.is_open
                    existing.source = seed.source
                    existing.updated_at = now
            session.commit()
        return written

    def search(self, key: SearchKey, *, market: str | None, limit: int) -> list[InstrumentRecord]:
        statement = select(Instrument).where(Instrument.is_active.is_(True))
        if market is not None:
            statement = statement.where(Instrument.market == market)
        if key.exchange and key.symbol:
            statement = statement.where(
                Instrument.exchange == key.exchange,
                Instrument.symbol == key.symbol,
            )
        elif key.symbol and key.cik:
            statement = statement.where(
                or_(Instrument.symbol == key.symbol, Instrument.cik == key.cik)
            )
        elif key.cik:
            statement = statement.where(Instrument.cik == key.cik)
        else:
            text = (key.text or "").strip()
            like = f"%{_escape_like(text)}%"
            statement = statement.where(
                or_(
                    Instrument.symbol == text.upper(),
                    Instrument.display_symbol == text.upper(),
                    Instrument.name_cn.ilike(like, escape="\\"),
                    Instrument.name_en.ilike(like, escape="\\"),
                )
            )
        statement = statement.order_by(Instrument.exchange, Instrument.symbol).limit(limit)
        with Session(self.engine) as session:
            return [_record(row) for row in session.scalars(statement)]

    def get(self, instrument_id: UUID) -> InstrumentRecord | None:
        with Session(self.engine) as session:
            row = session.get(Instrument, instrument_id)
            if row is None:
                return None
            return _record(row)

    def calendar_open(self, exchange: str, session_date: date) -> bool | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(TradingCalendar).where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.session_date == session_date,
                )
            ).one_or_none()
            if row is None:
                return None
            return row.is_open

    def calendar_source(self, exchange: str, session_date: date) -> str | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(TradingCalendar).where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.session_date == session_date,
                )
            ).one_or_none()
            if row is None:
                return None
            return row.source


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _record(row: Instrument) -> InstrumentRecord:
    return InstrumentRecord(
        id=row.id,
        market=row.market,
        exchange=row.exchange,
        symbol=row.symbol,
        display_symbol=row.display_symbol,
        name_cn=row.name_cn,
        name_en=row.name_en,
        instrument_type=row.instrument_type,
        currency=row.currency,
        cik=row.cik,
    )
