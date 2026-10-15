from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class UserRecord:
    id: UUID
    username: str
    email: str
    password_hash: str
    role: str
    is_active: bool
    disabled_at: datetime | None


@dataclass(frozen=True)
class PreferenceRecord:
    theme: str
    rise_color_scheme: str
    timezone: str
    default_market: str
    default_lookback: str


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None


DEFAULT_PREFERENCES = PreferenceRecord(
    theme="dark",
    rise_color_scheme="cn",
    timezone="Asia/Shanghai",
    default_market="CN",
    default_lookback="1y",
)
