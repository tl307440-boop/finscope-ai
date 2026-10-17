import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.market import Instrument, MarketBar
from app.db.models.watchlist import WatchlistGroup, WatchlistItem
from app.utils.time import utc_now


@dataclass(frozen=True)
class WatchItem:
    instrument_id: UUID
    display_symbol: str
    name_cn: str
    market: str = "CN"
    exchange: str = ""
    symbol: str = ""


@dataclass(frozen=True)
class ImportOutcome:
    added: tuple[str, ...]
    existing: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class RecentClose:
    trade_date: date
    close: Decimal


@dataclass(frozen=True)
class WatchGroup:
    id: UUID
    name: str
    created_at: datetime
    items: tuple[WatchItem, ...]


class WatchlistRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_group(self, user_id: UUID, name: str) -> WatchGroup | None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("请填写分组名称")
        row = WatchlistGroup(
            id=uuid.uuid4(),
            user_id=user_id,
            name=cleaned[:40],
            created_at=utc_now(),
        )
        try:
            with Session(self.engine) as session, session.begin():
                session.add(row)
                session.flush()
                return WatchGroup(id=row.id, name=row.name, created_at=row.created_at, items=())
        except IntegrityError:
            return None

    def list_groups(self, user_id: UUID) -> list[WatchGroup]:
        with Session(self.engine) as session:
            groups = session.scalars(
                select(WatchlistGroup)
                .where(WatchlistGroup.user_id == user_id)
                .order_by(WatchlistGroup.created_at.desc())
            ).all()
            return [self._group(session, group) for group in groups]

    def add_item(self, user_id: UUID, group_id: UUID, instrument_id: UUID) -> bool | None:
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            if session.get(Instrument, instrument_id) is None:
                return False
            exists = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
            ).one_or_none()
            if exists is not None:
                return True
            session.add(
                WatchlistItem(
                    id=uuid.uuid4(),
                    group_id=group_id,
                    instrument_id=instrument_id,
                    created_at=utc_now(),
                )
            )
            return True

    def add_codes(
        self, user_id: UUID, group_id: UUID, codes: Sequence[tuple[str, str]]
    ) -> ImportOutcome | None:
        """按 (交易所, 代码) 批量加入；库里没有的证券原样列进 missing，不臆造。"""

        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            found = (
                session.scalars(
                    select(Instrument).where(
                        Instrument.market == "CN",
                        tuple_(Instrument.exchange, Instrument.symbol).in_(list(codes)),
                    )
                ).all()
                if codes
                else []
            )
            by_key = {(row.exchange, row.symbol): row for row in found}
            present = set(
                session.scalars(
                    select(WatchlistItem.instrument_id).where(WatchlistItem.group_id == group_id)
                ).all()
            )
            added: list[str] = []
            existing: list[str] = []
            missing: list[str] = []
            for key in dict.fromkeys(codes):
                row = by_key.get(key)
                if row is None:
                    missing.append(key[1])
                    continue
                if row.id in present:
                    existing.append(row.display_symbol)
                    continue
                session.add(
                    WatchlistItem(
                        id=uuid.uuid4(),
                        group_id=group_id,
                        instrument_id=row.id,
                        created_at=utc_now(),
                    )
                )
                present.add(row.id)
                added.append(row.display_symbol)
            return ImportOutcome(tuple(added), tuple(existing), tuple(missing))

    def get_group(self, user_id: UUID, group_id: UUID) -> WatchGroup | None:
        with Session(self.engine) as session:
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            return self._group(session, group)

    def recent_closes(
        self, instrument_ids: Sequence[UUID], since: date
    ) -> dict[UUID, list[RecentClose]]:
        """已入库的不复权日线收盘价（任意来源），供实时行情不可用时计算涨跌幅。"""

        if not instrument_ids:
            return {}
        with Session(self.engine) as session:
            rows = session.execute(
                select(MarketBar.instrument_id, MarketBar.trade_date, MarketBar.close)
                .where(
                    MarketBar.instrument_id.in_(list(instrument_ids)),
                    MarketBar.timeframe == "1d",
                    MarketBar.adjustment == "none",
                    MarketBar.trade_date >= since,
                )
                .order_by(MarketBar.instrument_id, MarketBar.trade_date)
            ).all()
        result: dict[UUID, dict[date, Decimal]] = {}
        for instrument_id, trade_date, close in rows:
            result.setdefault(instrument_id, {})[trade_date] = close
        return {
            key: [RecentClose(day, close) for day, close in sorted(days.items())]
            for key, days in result.items()
        }

    def remove_item(self, user_id: UUID, group_id: UUID, instrument_id: UUID) -> bool | None:
        with Session(self.engine) as session, session.begin():
            group = session.get(WatchlistGroup, group_id)
            if group is None or group.user_id != user_id:
                return None
            item = session.scalars(
                select(WatchlistItem).where(
                    WatchlistItem.group_id == group_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
            ).one_or_none()
            if item is None:
                return False
            session.delete(item)
            return True

    def _group(self, session: Session, group: WatchlistGroup) -> WatchGroup:
        rows = session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.group_id == group.id)
            .order_by(WatchlistItem.created_at.desc())
        ).all()
        items: list[WatchItem] = []
        for row in rows:
            instrument = session.get(Instrument, row.instrument_id)
            if instrument is None:
                continue
            items.append(
                WatchItem(
                    instrument_id=instrument.id,
                    display_symbol=instrument.display_symbol,
                    name_cn=instrument.name_cn,
                    market=instrument.market,
                    exchange=instrument.exchange,
                    symbol=instrument.symbol,
                )
            )
        return WatchGroup(
            id=group.id, name=group.name, created_at=group.created_at, items=tuple(items)
        )
