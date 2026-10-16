import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.account import User
from app.db.models.app_setting import AppSetting


@dataclass(frozen=True)
class StoredSetting:
    key: str
    value_enc: str
    is_secret: bool
    updated_by_name: str | None
    updated_at: datetime


class AppSettingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def all(self) -> dict[str, StoredSetting]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(AppSetting, User.username).outerjoin(User, User.id == AppSetting.updated_by)
            ).all()
            return {
                row.key: StoredSetting(
                    key=row.key,
                    value_enc=row.value_enc,
                    is_secret=row.is_secret,
                    updated_by_name=name,
                    updated_at=row.updated_at,
                )
                for row, name in rows
            }

    def put(
        self,
        *,
        key: str,
        value_enc: str,
        is_secret: bool,
        user_id: uuid.UUID | None,
        now: datetime,
    ) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(AppSetting, key)
            if row is None:
                session.add(
                    AppSetting(
                        key=key,
                        value_enc=value_enc,
                        is_secret=is_secret,
                        updated_by=user_id,
                        updated_at=now,
                    )
                )
                return
            row.value_enc = value_enc
            row.is_secret = is_secret
            row.updated_by = user_id
            row.updated_at = now

    def delete(self, key: str) -> bool:
        with Session(self.engine) as session, session.begin():
            row = session.get(AppSetting, key)
            if row is None:
                return False
            session.delete(row)
            return True
