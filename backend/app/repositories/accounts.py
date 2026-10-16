import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from app.db.models.account import RefreshSession, User, UserPreference
from app.services.auth_types import (
    DEFAULT_PREFERENCES,
    PreferenceRecord,
    SessionRecord,
    UserRecord,
)
from app.utils.time import utc_now


class AccountRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_by_username(self, username: str) -> UserRecord | None:
        with Session(self.engine) as session:
            # 账号不区分大小写；有精确同名的老账号时优先用它。
            row = session.scalars(
                select(User)
                .where(func.lower(User.username) == username.lower())
                .order_by((User.username == username).desc())
                .limit(1)
            ).first()
            return None if row is None else _user_record(row)

    def get_by_id(self, user_id: uuid.UUID) -> UserRecord | None:
        with Session(self.engine) as session:
            row = session.get(User, user_id)
            return None if row is None else _user_record(row)

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str,
        now: datetime | None = None,
    ) -> UserRecord:
        moment = now or utc_now()
        user_id = uuid.uuid4()
        with Session(self.engine) as session:
            session.add(
                User(
                    id=user_id,
                    username=username,
                    email=email.lower(),
                    password_hash=password_hash,
                    role=role,
                    is_active=True,
                    disabled_at=None,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            defaults = DEFAULT_PREFERENCES
            session.add(
                UserPreference(
                    user_id=user_id,
                    theme=defaults.theme,
                    rise_color_scheme=defaults.rise_color_scheme,
                    timezone=defaults.timezone,
                    default_market=defaults.default_market,
                    default_lookback=defaults.default_lookback,
                    updated_at=moment,
                )
            )
            session.commit()
        created = self.get_by_id(user_id)
        if created is None:
            raise RuntimeError("user disappeared after insert")
        return created

    def set_password(self, user_id: uuid.UUID, password_hash: str, now: datetime) -> None:
        with Session(self.engine) as session:
            row = session.get(User, user_id)
            if row is None:
                return
            row.password_hash = password_hash
            row.updated_at = now
            session.commit()

    def admin_count(self) -> int:
        with Session(self.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == "admin", User.is_active.is_(True))
                )
                or 0
            )

    def deactivate(self, user_id: uuid.UUID, now: datetime) -> None:
        with Session(self.engine) as session:
            row = session.get(User, user_id)
            if row is None:
                return
            row.is_active = False
            row.disabled_at = now
            row.updated_at = now
            session.commit()

    def get_preferences(self, user_id: uuid.UUID) -> PreferenceRecord | None:
        with Session(self.engine) as session:
            row = session.get(UserPreference, user_id)
            if row is None:
                return None
            return PreferenceRecord(
                theme=row.theme,
                rise_color_scheme=row.rise_color_scheme,
                timezone=row.timezone,
                default_market=row.default_market,
                default_lookback=row.default_lookback,
            )

    def update_preferences(
        self,
        user_id: uuid.UUID,
        changes: dict[str, str],
        now: datetime,
    ) -> PreferenceRecord:
        with Session(self.engine) as session:
            row = session.get(UserPreference, user_id)
            if row is None:
                defaults = DEFAULT_PREFERENCES
                row = UserPreference(
                    user_id=user_id,
                    theme=defaults.theme,
                    rise_color_scheme=defaults.rise_color_scheme,
                    timezone=defaults.timezone,
                    default_market=defaults.default_market,
                    default_lookback=defaults.default_lookback,
                    updated_at=now,
                )
                session.add(row)
            for key, value in changes.items():
                setattr(row, key, value)
            row.updated_at = now
            session.commit()
            return PreferenceRecord(
                theme=row.theme,
                rise_color_scheme=row.rise_color_scheme,
                timezone=row.timezone,
                default_market=row.default_market,
                default_lookback=row.default_lookback,
            )


class RefreshSessionRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self,
        record: SessionRecord,
        *,
        created_at: datetime,
        device_label: str | None,
    ) -> None:
        with Session(self.engine) as session:
            session.add(
                RefreshSession(
                    id=record.id,
                    user_id=record.user_id,
                    token_hash=record.token_hash,
                    expires_at=record.expires_at,
                    revoked_at=record.revoked_at,
                    created_at=created_at,
                    device_label=device_label,
                )
            )
            session.commit()

    def get_by_hash(self, token_hash: str) -> SessionRecord | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(RefreshSession).where(RefreshSession.token_hash == token_hash)
            ).one_or_none()
            return None if row is None else _session_record(row)

    def revoke(self, session_id: uuid.UUID, now: datetime) -> None:
        with Session(self.engine) as session:
            row = session.get(RefreshSession, session_id)
            if row is None or row.revoked_at is not None:
                return
            row.revoked_at = now.astimezone(UTC)
            session.commit()

    def revoke_all(self, user_id: uuid.UUID, now: datetime) -> int:
        """改密码后让这个账号所有设备上的登录凭证失效。"""

        with Session(self.engine) as session:
            result = session.execute(
                update(RefreshSession)
                .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
                .values(revoked_at=now.astimezone(UTC))
            )
            session.commit()
            return int(getattr(result, "rowcount", 0) or 0)


def _user_record(row: User) -> UserRecord:
    return UserRecord(
        id=row.id,
        username=row.username,
        email=row.email,
        password_hash=row.password_hash,
        role=row.role,
        is_active=row.is_active,
        disabled_at=row.disabled_at,
    )


def _session_record(row: RefreshSession) -> SessionRecord:
    return SessionRecord(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )
