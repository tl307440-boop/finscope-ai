import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Engine, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.market import MarketBar
from app.providers.quality import CheckedBar
from app.utils.time import utc_now


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
            "amount": statement.excluded.amount,
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
