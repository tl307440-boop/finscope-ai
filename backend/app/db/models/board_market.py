import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BoardDailySnapshot(Base):
    """某个交易日收盘后抓取的东财板块行情快照；trade_date 当天收盘后才可见。"""

    __tablename__ = "board_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("board_code", "trade_date", name="uq_board_daily_snapshots_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    board_code: Mapped[str] = mapped_column(String(8), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    last: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    change_5d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    change_10d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    change_60d_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    change_ytd_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    turnover_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    main_net_inflow: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    main_net_inflow_5d: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    advancers: Mapped[int | None] = mapped_column(Integer)
    decliners: Mapped[int | None] = mapped_column(Integer)
    unchanged: Mapped[int | None] = mapped_column(Integer)
    leader_code: Mapped[str | None] = mapped_column(String(16))
    leader_name: Mapped[str | None] = mapped_column(String(64))
    leader_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BoardDailyBar(Base):
    """东财板块指数（secid 90.BKxxxx）与基准指数（1.000300）的不复权日线。"""

    __tablename__ = "board_daily_bars"
    __table_args__ = (UniqueConstraint("secid", "trade_date", name="uq_board_daily_bars_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    secid: Mapped[str] = mapped_column(String(16), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
