"""午盘成交额快照的读写。

只做两件事：按 ``(trade_date, market)`` 覆盖写、按区间读回成 ``{日期: 成交额}``。
读回来的形状直接喂给 ``ASharePanoramaService.volume_margin(noon_snapshot=...)``。
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from app.db.models.market_noon import MarketNoonAmount


class MarketNoonRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert(
        self,
        *,
        trade_date: date,
        market: str,
        amount_yuan: Decimal,
        captured_at: datetime,
        source: str,
    ) -> None:
        """同一交易日同一市场只留一行：12:05 的补跑覆盖 11:35 的首次抓取。"""

        statement = insert(MarketNoonAmount).values(
            id=uuid.uuid4(),
            trade_date=trade_date,
            market=market,
            amount_yuan=amount_yuan,
            captured_at=captured_at,
            source=source,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_market_noon_amounts_identity",
            set_={
                "amount_yuan": statement.excluded["amount_yuan"],
                "captured_at": statement.excluded["captured_at"],
                "source": statement.excluded["source"],
            },
        )
        with self.engine.begin() as connection:
            connection.execute(statement)

    def amount_yuan_by_day(self, market: str, *, start: date, end: date) -> dict[str, float]:
        """``{ISO 日期: 成交额(元)}``；没有快照的交易日不会出现在结果里。"""

        statement = (
            select(MarketNoonAmount.trade_date, MarketNoonAmount.amount_yuan)
            .where(
                MarketNoonAmount.market == market,
                MarketNoonAmount.trade_date >= start,
                MarketNoonAmount.trade_date <= end,
            )
            .order_by(MarketNoonAmount.trade_date)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement).all()
        return {day.isoformat(): float(amount) for day, amount in rows}
