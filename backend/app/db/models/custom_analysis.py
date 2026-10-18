import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OptionDailyStat(Base):
    """交易所公布的某个标的、某个交易日的认购 / 认沽成交量与未平仓量。

    只存原始量，PCR 由读取侧自己算：上交所披露的是成交量比、深交所披露的是持仓量比，
    口径不同，直接存对方的比值会混淆。披露的比值只留作对账。
    """

    __tablename__ = "option_daily_stats"
    __table_args__ = (
        UniqueConstraint(
            "exchange", "underlying_code", "trade_date", name="uq_option_daily_stats_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    underlying_code: Mapped[str] = mapped_column(String(16), nullable=False)
    underlying_name: Mapped[str | None] = mapped_column(String(64))
    call_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    put_volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    call_oi: Mapped[int] = mapped_column(BigInteger, nullable=False)
    put_oi: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reported_ratio: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    reported_ratio_kind: Mapped[str | None] = mapped_column(String(16))
    quality_flag: Mapped[str] = mapped_column(String(8), nullable=False)
    quality_note: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomSeriesPoint(Base):
    """自定义分析的日频 / 季频标量序列。

    包括指数收盘、PE、国债收益率、总市值、中位数 PB、GDP 累计值。
    """

    __tablename__ = "custom_series_points"
    __table_args__ = (
        UniqueConstraint("series_code", "trade_date", name="uq_custom_series_points_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    series_code: Mapped[str] = mapped_column(String(40), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(48), nullable=False)
    quality_flag: Mapped[str] = mapped_column(String(8), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
