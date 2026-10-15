import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

MARKET = ENUM("CN", "US", name="market_code", create_type=False)
INSTRUMENT_TYPE = ENUM("STOCK", "INDEX", "ETF", name="instrument_type", create_type=False)
BAR_TIMEFRAME = ENUM("1d", "1w", "1mo", name="bar_timeframe", create_type=False)
BAR_ADJUSTMENT = ENUM("none", "qfq", "hfq", name="bar_adjustment", create_type=False)


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    market: Mapped[str] = mapped_column(MARKET, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    display_symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str] = mapped_column(String(128), nullable=False)
    instrument_type: Mapped[str] = mapped_column("type", INSTRUMENT_TYPE, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    cik: Mapped[str | None] = mapped_column(String(10))
    industry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TradingCalendar(Base):
    __tablename__ = "trading_calendars"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    session_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketBar(Base):
    __tablename__ = "market_bars"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id"),
        nullable=False,
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(BAR_TIMEFRAME, nullable=False)
    adjustment: Mapped[str] = mapped_column(BAR_ADJUSTMENT, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("data_providers.id"),
        nullable=False,
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_flag: Mapped[str] = mapped_column(String(32), nullable=False)
