import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.board_market import BoardDailyBar, BoardDailySnapshot
from app.providers import eastmoney_boards as em

_QUOTE_COLUMNS = (
    "last",
    "change_pct",
    "change_5d_pct",
    "change_10d_pct",
    "change_60d_pct",
    "change_ytd_pct",
    "amount",
    "turnover_pct",
    "pe_ttm",
    "market_cap",
    "main_net_inflow",
    "main_net_inflow_5d",
    "advancers",
    "decliners",
    "unchanged",
    "leader_code",
    "leader_name",
    "leader_change_pct",
)


@dataclass(frozen=True)
class StoredSnapshot:
    node: em.BoardNode
    quote: em.BoardQuote
    trade_date: date
    fetched_at: datetime


@dataclass(frozen=True)
class StoredBars:
    bars: tuple[em.BoardBar, ...]
    fetched_at: datetime | None


class BoardMarketRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_snapshots(
        self,
        trade_date: date,
        rows: Sequence[tuple[em.BoardNode, em.BoardQuote]],
        fetched_at: datetime,
    ) -> int:
        if not rows:
            return 0
        values = [
            {
                "id": uuid.uuid4(),
                "board_code": node.code,
                "trade_date": trade_date,
                "name": node.name,
                "kind": node.kind,
                "level": node.level,
                **{column: getattr(quote, column) for column in _QUOTE_COLUMNS},
                "fetched_at": fetched_at,
            }
            for node, quote in rows
        ]
        statement = insert(BoardDailySnapshot).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_board_daily_snapshots_identity",
            set_={
                column: statement.excluded[column]
                for column in ("name", "kind", "level", *_QUOTE_COLUMNS, "fetched_at")
            },
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)
        return len(values)

    def upsert_bars(self, secid: str, bars: Sequence[em.BoardBar], fetched_at: datetime) -> int:
        if not bars:
            return 0
        values = [
            {
                "id": uuid.uuid4(),
                "secid": secid,
                "trade_date": bar.trade_date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
                "fetched_at": fetched_at,
            }
            for bar in bars
        ]
        statement = insert(BoardDailyBar).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_board_daily_bars_identity",
            set_={
                column: statement.excluded[column]
                for column in ("open", "high", "low", "close", "volume", "amount", "fetched_at")
            },
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)
        return len(values)

    def last_bar_dates(self, secids: Sequence[str]) -> dict[str, date]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(BoardDailyBar.secid, func.max(BoardDailyBar.trade_date))
                .where(BoardDailyBar.secid.in_(list(secids)))
                .group_by(BoardDailyBar.secid)
            ).all()
        return {secid: day for secid, day in rows}

    def latest_snapshot_date(self, on_or_before: date | None = None) -> date | None:
        query = select(func.max(BoardDailySnapshot.trade_date))
        if on_or_before is not None:
            query = query.where(BoardDailySnapshot.trade_date <= on_or_before)
        with Session(self.engine) as session:
            return session.scalar(query)

    def snapshot_dates(self, limit: int) -> list[date]:
        with Session(self.engine) as session:
            return list(
                session.scalars(
                    select(BoardDailySnapshot.trade_date)
                    .distinct()
                    .order_by(BoardDailySnapshot.trade_date.desc())
                    .limit(limit)
                ).all()
            )

    def snapshots(self, trade_date: date, kind: str, level: int | None) -> list[StoredSnapshot]:
        query = select(BoardDailySnapshot).where(
            BoardDailySnapshot.trade_date == trade_date, BoardDailySnapshot.kind == kind
        )
        if level is not None:
            query = query.where(BoardDailySnapshot.level == level)
        with Session(self.engine) as session:
            rows = session.scalars(query.order_by(BoardDailySnapshot.board_code)).all()
            return [_snapshot(row) for row in rows]

    def latest_snapshot(self, board_code: str) -> StoredSnapshot | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(BoardDailySnapshot)
                .where(BoardDailySnapshot.board_code == board_code)
                .order_by(BoardDailySnapshot.trade_date.desc())
                .limit(1)
            ).one_or_none()
            return _snapshot(row) if row is not None else None

    def bars(self, secid: str, since: date, until: date | None = None) -> StoredBars:
        return self.bars_many([secid], since, until).get(
            secid, StoredBars(bars=(), fetched_at=None)
        )

    def bars_many(
        self, secids: Sequence[str], since: date, until: date | None = None
    ) -> dict[str, StoredBars]:
        query = select(BoardDailyBar).where(
            BoardDailyBar.secid.in_(list(secids)), BoardDailyBar.trade_date >= since
        )
        if until is not None:
            query = query.where(BoardDailyBar.trade_date <= until)
        grouped: dict[str, list[BoardDailyBar]] = {}
        with Session(self.engine) as session:
            for row in session.scalars(query.order_by(BoardDailyBar.trade_date)).all():
                grouped.setdefault(row.secid, []).append(row)
        return {
            secid: StoredBars(
                bars=tuple(
                    em.BoardBar(
                        trade_date=row.trade_date,
                        open=row.open,
                        close=row.close,
                        high=row.high,
                        low=row.low,
                        volume=row.volume,
                        amount=row.amount,
                    )
                    for row in rows
                ),
                fetched_at=max(row.fetched_at for row in rows),
            )
            for secid, rows in grouped.items()
        }


def _snapshot(row: BoardDailySnapshot) -> StoredSnapshot:
    kind: em.BoardKind = "concept" if row.kind == "concept" else "industry"
    return StoredSnapshot(
        node=em.BoardNode(code=row.board_code, name=row.name, kind=kind, level=row.level),
        quote=em.BoardQuote(
            code=row.board_code,
            name=row.name,
            **{column: getattr(row, column) for column in _QUOTE_COLUMNS},
        ),
        trade_date=row.trade_date,
        fetched_at=row.fetched_at,
    )
