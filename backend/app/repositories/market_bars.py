import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.market import MarketBar
from app.db.models.provider import DataProvider
from app.providers.quality import CheckedBar
from app.utils.time import utc_now

AKSHARE_PROVIDER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://finscope.local/providers/akshare")


@dataclass(frozen=True)
class StoredDailyBar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None
    adjustment: str
    quality_flag: str


class MarketBarRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert(self, bars: list[CheckedBar], *, instrument_id: UUID, provider_id: UUID) -> int:
        if not bars:
            return 0
        written = 0
        fetched_at = utc_now()
        with Session(self.engine) as session:
            for bar in bars:
                written += _upsert_one(
                    session,
                    bar,
                    instrument_id=instrument_id,
                    provider_id=provider_id,
                    fetched_at=fetched_at,
                )
            session.commit()
        return written

    def get_close(
        self,
        *,
        instrument_id: UUID,
        provider_id: UUID,
        trade_date: date,
        adjustment: str,
    ) -> tuple[str, str] | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(MarketBar).where(
                    MarketBar.instrument_id == instrument_id,
                    MarketBar.provider_id == provider_id,
                    MarketBar.trade_date == trade_date,
                    MarketBar.adjustment == adjustment,
                    MarketBar.timeframe == "1d",
                )
            ).one_or_none()
            if row is None:
                return None
            return str(row.close), row.quality_flag

    def ensure_akshare_provider(self) -> UUID:
        """A 股日线用的 provider 行。

        provider 的 code 从 20261015_0005 起就是 `akshare`，它已经被 market_bars
        的外键引用，所以这里不改 code，只把对外可见的名字和上游清单写实：
        现在这一行底下同时存着东方财富和腾讯行情两个上游抓来的日线。
        """

        with Session(self.engine) as session:
            found = session.scalars(
                select(DataProvider).where(DataProvider.code == "akshare")
            ).one_or_none()
            if found is not None:
                return found.id
            provider = DataProvider(
                id=AKSHARE_PROVIDER_ID,
                code="akshare",
                name="A股日线（东方财富 / 腾讯行情）",
                enabled=True,
                config_public={
                    "upstreams": [
                        "https://push2his.eastmoney.com",
                        "https://web.ifzq.gtimg.cn",
                    ],
                    "credential": "none",
                    "volume_unit": "hand",
                },
            )
            session.add(provider)
            session.commit()
            return provider.id

    def list_daily(
        self,
        *,
        instrument_id: UUID,
        provider_id: UUID,
        adjustment: str,
        start: date | None,
        end: date | None,
        pit: date,
        limit: int,
    ) -> tuple[list[StoredDailyBar], bool]:
        statement = select(MarketBar).where(
            MarketBar.instrument_id == instrument_id,
            MarketBar.provider_id == provider_id,
            MarketBar.timeframe == "1d",
            MarketBar.adjustment == adjustment,
            MarketBar.trade_date <= pit,
        )
        if start is not None:
            statement = statement.where(MarketBar.trade_date >= start)
        if end is not None:
            statement = statement.where(MarketBar.trade_date <= end)
        statement = statement.order_by(MarketBar.trade_date).limit(limit + 1)
        with Session(self.engine) as session:
            rows = session.scalars(statement).all()
        truncated = len(rows) > limit
        visible = rows[:limit]
        return (
            [
                StoredDailyBar(
                    trade_date=row.trade_date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    amount=row.amount,
                    adjustment=row.adjustment,
                    quality_flag=row.quality_flag,
                )
                for row in visible
            ],
            truncated,
        )


def _upsert_one(
    session: Session,
    bar: CheckedBar,
    *,
    instrument_id: UUID,
    provider_id: UUID,
    fetched_at: datetime,
) -> int:
    ins = pg_insert(MarketBar)
    statement = ins.values(
        id=uuid.uuid4(),
        instrument_id=instrument_id,
        trade_date=bar.trade_date,
        timeframe="1d",
        adjustment=bar.adjustment,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        amount=bar.amount,
        provider_id=provider_id,
        fetched_at=fetched_at,
        quality_flag=bar.quality_flag,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_market_bars_identity",
        set_={
            "open": statement.excluded.open,
            "high": statement.excluded.high,
            "low": statement.excluded.low,
            "close": statement.excluded.close,
            "volume": statement.excluded.volume,
            # 新数据缺成交额时保留库里已有的：腾讯不给成交额，不能让它把
            # 东方财富已经抓到的成交额覆盖成空。
            "amount": func.coalesce(statement.excluded.amount, MarketBar.amount),
            "fetched_at": statement.excluded.fetched_at,
            "quality_flag": statement.excluded.quality_flag,
        },
        where=or_(
            MarketBar.quality_flag != "ok",
            statement.excluded.quality_flag == "ok",
        ),
    )
    result = session.execute(statement.returning(MarketBar.id))
    return len(result.scalars().all())
