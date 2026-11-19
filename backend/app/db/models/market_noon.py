"""午盘成交额快照（北证市场）。

上游（腾讯报价）只给「当日累计成交额」，没有 11:30 这一历史时点；而全市场午盘成交额 =
沪 + 深 + 京 的求和，缺一份整列就会空。所以交易日 11:30 之后由调度器抓一次落库，
按 ``(trade_date, market)`` 唯一。

历史只能从首次上线当天开始累积，无法回补：快照是「当时抓到的累计值」，事后无法重建。
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketNoonAmount(Base):
    """某个市场某个交易日在 11:30 的成交额快照。"""

    __tablename__ = "market_noon_amounts"
    __table_args__ = (
        UniqueConstraint("trade_date", "market", name="uq_market_noon_amounts_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    # 市场键：bj（北交所）。留成字符串而不是枚举，后续要给别的市场补快照时不用迁移。
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_yuan: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
