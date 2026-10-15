import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.market import Instrument
from app.db.models.watchlist import WatchlistGroup, WatchlistItem
from app.utils.time import utc_now


@dataclass(frozen=True)
class WatchItem:
    instrument_id: UUID
    display_symbol: str
    name_cn: str


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
                )
            )
        return WatchGroup(
            id=group.id, name=group.name, created_at=group.created_at, items=tuple(items)
        )
