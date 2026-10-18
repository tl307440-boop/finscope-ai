from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.eastmoney import EastmoneyBinding
from app.utils.time import utc_now


@dataclass(frozen=True)
class BindingRecord:
    id: UUID
    user_id: UUID
    em_uid: str
    em_alias: str
    cookies_enc: str
    status: str
    bound_at: datetime
    last_sync_at: datetime | None
    last_sync_message: str | None
    last_sync_groups: int | None
    last_sync_items: int | None


class EastmoneyBindingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, user_id: UUID) -> BindingRecord | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(EastmoneyBinding).where(EastmoneyBinding.user_id == user_id)
            ).one_or_none()
            return None if row is None else _record(row)

    def list_active(self) -> list[BindingRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(EastmoneyBinding)
                .where(EastmoneyBinding.status == "active")
                .order_by(EastmoneyBinding.last_sync_at.asc().nullsfirst())
            ).all()
            return [_record(row) for row in rows]

    def upsert(self, user_id: UUID, em_uid: str, em_alias: str, cookies_enc: str) -> BindingRecord:
        """一个用户只有一个绑定；重新扫码就是覆盖，分组和条目不受影响。"""

        with Session(self.engine) as session, session.begin():
            row = session.scalars(
                select(EastmoneyBinding).where(EastmoneyBinding.user_id == user_id)
            ).one_or_none()
            if row is None:
                row = EastmoneyBinding(id=uuid4(), user_id=user_id, bound_at=utc_now())
                session.add(row)
            row.em_uid = em_uid[:32]
            row.em_alias = em_alias[:64]
            row.cookies_enc = cookies_enc
            row.status = "active"
            row.bound_at = utc_now()
            row.last_sync_message = None
            session.flush()
            return _record(row)

    def mark_synced(self, user_id: UUID, *, message: str, groups: int, items: int) -> None:
        with Session(self.engine) as session, session.begin():
            row = _row(session, user_id)
            if row is None:
                return
            row.status = "active"
            row.last_sync_at = utc_now()
            row.last_sync_message = message[:200]
            row.last_sync_groups = groups
            row.last_sync_items = items

    def mark_failed(self, user_id: UUID, message: str) -> None:
        """同步失败：只记原因，不动已同步的数据，也不改 last_sync_at。"""

        with Session(self.engine) as session, session.begin():
            row = _row(session, user_id)
            if row is not None:
                row.last_sync_message = message[:200]

    def mark_expired(self, user_id: UUID, message: str) -> None:
        with Session(self.engine) as session, session.begin():
            row = _row(session, user_id)
            if row is not None:
                row.status = "expired"
                row.last_sync_message = message[:200]

    def delete(self, user_id: UUID) -> bool:
        with Session(self.engine) as session, session.begin():
            row = _row(session, user_id)
            if row is None:
                return False
            session.delete(row)
            return True


def _row(session: Session, user_id: UUID) -> EastmoneyBinding | None:
    return session.scalars(
        select(EastmoneyBinding).where(EastmoneyBinding.user_id == user_id)
    ).one_or_none()


def _record(row: EastmoneyBinding) -> BindingRecord:
    return BindingRecord(
        id=row.id,
        user_id=row.user_id,
        em_uid=row.em_uid,
        em_alias=row.em_alias,
        cookies_enc=row.cookies_enc,
        status=row.status,
        bound_at=row.bound_at,
        last_sync_at=row.last_sync_at,
        last_sync_message=row.last_sync_message,
        last_sync_groups=row.last_sync_groups,
        last_sync_items=row.last_sync_items,
    )
