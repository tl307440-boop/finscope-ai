"""全市场证券清单的写入，以及「哪些公司值得定时刷新」的查询。

写入只补缺：已存在的证券（含手工整理的中文名）一律不改，避免定时同步把中文名覆盖成英文。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Engine, and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.market import Instrument, MarketBar
from app.db.models.provider import SyncJob
from app.db.models.sec import CnStatementFact, Filing
from app.db.models.watchlist import WatchlistItem
from app.providers.universe import CnListing, UsListing
from app.utils.time import utc_now

_CHUNK = 1000


@dataclass(frozen=True)
class TrackedCompany:
    instrument_id: UUID
    display_symbol: str
    cik: str | None


class UniverseRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_us(self, listings: Sequence[UsListing]) -> int:
        """补缺写入美股。CIK 已被别的代码占用（如 GOOG 与 GOOGL）时跳过，保持一家一条。"""

        with Session(self.engine) as session:
            taken = set(
                session.scalars(select(Instrument.cik).where(Instrument.cik.is_not(None))).all()
            )
        rows: list[dict[str, Any]] = []
        now = utc_now()
        for item in listings:
            if item.cik in taken:
                continue
            taken.add(item.cik)
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "market": "US",
                    "exchange": item.exchange,
                    "symbol": item.ticker,
                    "display_symbol": item.ticker,
                    "name_cn": item.name,
                    "name_en": item.name,
                    "type": "STOCK",
                    "currency": "USD",
                    "cik": item.cik,
                    "is_active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        return self._insert(rows)

    def upsert_cn(self, listings: Sequence[CnListing]) -> int:
        now = utc_now()
        rows = [
            {
                "id": uuid.uuid4(),
                "market": "CN",
                "exchange": item.exchange,
                "symbol": item.code,
                "display_symbol": item.display_symbol,
                "name_cn": item.name,
                "name_en": item.name,
                "type": "STOCK",
                "currency": "CNY",
                "cik": None,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            for item in listings
        ]
        return self._insert(rows)

    def _insert(self, rows: list[dict[str, Any]]) -> int:
        written = 0
        with Session(self.engine) as session, session.begin():
            for start in range(0, len(rows), _CHUNK):
                statement = (
                    pg_insert(Instrument)
                    .values(rows[start : start + _CHUNK])
                    .on_conflict_do_nothing()
                    .returning(Instrument.id)
                )
                # 不用 rowcount：psycopg 对多行 upsert 可能返回 -1；RETURNING 只返回真正写入的行。
                written += len(session.execute(statement).all())
        return written

    def count(self, market: str) -> int:
        with Session(self.engine) as session:
            return int(
                session.scalar(
                    select(func.count()).select_from(Instrument).where(Instrument.market == market)
                )
                or 0
            )

    def tracked_us(self) -> list[TrackedCompany]:
        """被关注过的美股：自选、已抓过财报、或已有行情。只有这些才进定时刷新。"""

        return self._tracked(
            "US",
            or_(
                _watched(),
                exists().where(Filing.instrument_id == Instrument.id, Filing.status == "edgar"),
                _has_bars(),
            ),
            require_cik=True,
        )

    def tracked_cn_bars(self) -> list[TrackedCompany]:
        """需要每天补日线的 A 股：已有日线（含指数）或在自选里的。"""

        return self._tracked(
            "CN",
            and_(
                Instrument.instrument_type.in_(("STOCK", "INDEX")),
                or_(_watched(), _has_bars()),
            ),
            require_cik=False,
        )

    def tracked_cn_stocks(self) -> list[TrackedCompany]:
        return self._tracked(
            "CN",
            and_(
                Instrument.instrument_type == "STOCK",
                or_(
                    _watched(),
                    _has_bars(),
                    exists().where(CnStatementFact.instrument_id == Instrument.id),
                ),
            ),
            require_cik=False,
        )

    def tracked_us_by_oldest_bar(self, limit: int) -> list[TrackedCompany]:
        """美股日线按「最久没更新」排序取前 N 个，免费档限额下轮流刷新。"""

        newest = (
            select(MarketBar.instrument_id, func.max(MarketBar.trade_date).label("last_bar"))
            .group_by(MarketBar.instrument_id)
            .subquery()
        )
        statement = (
            select(Instrument.id, Instrument.display_symbol, Instrument.cik)
            .outerjoin(newest, newest.c.instrument_id == Instrument.id)
            .where(
                Instrument.market == "US",
                Instrument.is_active.is_(True),
                or_(
                    _watched(),
                    _has_bars(),
                    exists().where(Filing.instrument_id == Instrument.id, Filing.status == "edgar"),
                ),
            )
            .order_by(newest.c.last_bar.asc().nullsfirst(), Instrument.display_symbol)
            .limit(limit)
        )
        with Session(self.engine) as session:
            return [TrackedCompany(row[0], row[1], row[2]) for row in session.execute(statement)]

    def _tracked(
        self, market: str, condition: ColumnElement[bool], *, require_cik: bool
    ) -> list[TrackedCompany]:
        statement = select(Instrument.id, Instrument.display_symbol, Instrument.cik).where(
            Instrument.market == market,
            Instrument.is_active.is_(True),
            condition,
        )
        if require_cik:
            statement = statement.where(Instrument.cik.is_not(None))
        statement = statement.order_by(Instrument.display_symbol)
        with Session(self.engine) as session:
            return [TrackedCompany(row[0], row[1], row[2]) for row in session.execute(statement)]

    def last_bar_date(self, instrument_id: UUID) -> date | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(MarketBar.trade_date)).where(
                    MarketBar.instrument_id == instrument_id, MarketBar.timeframe == "1d"
                )
            )

    def last_success(self, job_type: str, display_symbol: str) -> datetime | None:
        """某个数据集最近一次成功同步的完成时间；从任务队列里取，不另造状态表。"""

        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(SyncJob.finished_at)).where(
                    SyncJob.job_type == job_type,
                    SyncJob.status == "succeeded",
                    SyncJob.scope["display_symbol"].astext == display_symbol,
                )
            )


def _watched() -> ColumnElement[bool]:
    return exists().where(WatchlistItem.instrument_id == Instrument.id)


def _has_bars() -> ColumnElement[bool]:
    return exists().where(MarketBar.instrument_id == Instrument.id)
