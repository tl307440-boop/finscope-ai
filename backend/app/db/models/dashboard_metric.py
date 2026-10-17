import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DashboardDailyMetric(Base):
    """总览驾驶舱每个 A 股交易日的汇总指标（温度、流动性、热点板块数），只用于「较上一交易日」对比。

    同一交易日重复计算时覆盖为最新值；收盘后计算的才是当日终值。
    """

    __tablename__ = "dashboard_daily_metrics"
    __table_args__ = (
        UniqueConstraint("trade_date", "metric", name="uq_dashboard_daily_metrics_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
