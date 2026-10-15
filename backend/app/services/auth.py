import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.core.exceptions import AppError
from app.core.security import TokenService, hash_refresh_token, new_refresh_token
from app.services.auth_types import (
    DEFAULT_PREFERENCES,
    PreferenceRecord,
    SessionRecord,
    UserRecord,
)
from app.services.login_limiter import LoginRateLimiter
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


def invalid_login() -> AppError:
    return AppError(code="ACCOUNT_INVALID", message="账号或密码错误", status_code=401)


class PasswordChecker(Protocol):
    def verify(self, password_hash: str | None, password: str) -> bool: ...


class UserDirectory(Protocol):
    def get_by_username(self, username: str) -> UserRecord | None: ...

    def get_by_id(self, user_id: uuid.UUID) -> UserRecord | None: ...

    def get_preferences(self, user_id: uuid.UUID) -> PreferenceRecord | None: ...

    def update_preferences(
        self,
        user_id: uuid.UUID,
        changes: dict[str, str],
        now: datetime,
    ) -> PreferenceRecord: ...


class SessionDirectory(Protocol):
    def save(
        self,
        record: SessionRecord,
        *,
        created_at: datetime,
        device_label: str | None,
    ) -> None: ...

    def get_by_hash(self, token_hash: str) -> SessionRecord | None: ...

    def revoke(self, session_id: uuid.UUID, now: datetime) -> None: ...


class LoginResult:
    def __init__(self, *, access_token: str, refresh_token: str, user: UserRecord) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user = user


class AuthService:
    def __init__(
        self,
        *,
        users: UserDirectory,
        sessions: SessionDirectory,
        passwords: PasswordChecker,
        tokens: TokenService,
        limiter: LoginRateLimiter,
        refresh_token_days: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.passwords = passwords
        self.tokens = tokens
        self.limiter = limiter
        self.refresh_token_days = refresh_token_days
        self.clock = clock or utc_now

    def login(
        self,
        *,
        username: str,
        password: str,
        device_label: str | None = None,
    ) -> LoginResult:
        now = self.clock()
        self.limiter.check(username, now)
        user = self.users.get_by_username(username)
        password_ok = self.passwords.verify(None if user is None else user.password_hash, password)
        if user is None or not password_ok or not _account_enabled(user):
            self.limiter.record_failure(username, now)
            logger.info("login failed")
            raise invalid_login()
        self.limiter.clear(username)
        return self._issue(user, now, device_label)

    def refresh(self, raw_token: str | None, device_label: str | None = None) -> LoginResult:
        now = self.clock()
        found = self._active_session(raw_token, now)
        if found is None:
            raise _unauthenticated()
        user = self.users.get_by_id(found.user_id)
        if user is None or not _account_enabled(user):
            raise _unauthenticated()
        self.sessions.revoke(found.id, now)
        return self._issue(user, now, device_label)

    def logout(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        found = self.sessions.get_by_hash(hash_refresh_token(raw_token))
        if found is None or found.revoked_at is not None:
            return
        self.sessions.revoke(found.id, self.clock())

    def user_from_access_token(self, token: str) -> UserRecord:
        user_id = self.tokens.read_access_subject(token)
        user = self.users.get_by_id(user_id)
        if user is None or not _account_enabled(user):
            raise _unauthenticated()
        return user

    def preferences_for(self, user_id: uuid.UUID) -> PreferenceRecord:
        found = self.users.get_preferences(user_id)
        return found if found is not None else DEFAULT_PREFERENCES

    def update_preferences(self, user_id: uuid.UUID, changes: dict[str, str]) -> PreferenceRecord:
        return self.users.update_preferences(user_id, changes, self.clock())

    def _issue(self, user: UserRecord, now: datetime, device_label: str | None) -> LoginResult:
        refresh = new_refresh_token()
        self.sessions.save(
            SessionRecord(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_refresh_token(refresh),
                expires_at=now.astimezone(UTC) + timedelta(days=self.refresh_token_days),
                revoked_at=None,
            ),
            created_at=now,
            device_label=device_label,
        )
        access = self.tokens.issue_access(user_id=user.id, role=user.role, now=now)
        return LoginResult(access_token=access, refresh_token=refresh, user=user)

    def _active_session(self, raw_token: str | None, now: datetime) -> SessionRecord | None:
        if not raw_token:
            return None
        found = self.sessions.get_by_hash(hash_refresh_token(raw_token))
        if found is None or found.revoked_at is not None:
            return None
        if found.expires_at.tzinfo is None:
            expires_at = found.expires_at.replace(tzinfo=UTC)
        else:
            expires_at = found.expires_at
        if expires_at <= now.astimezone(UTC):
            return None
        return found


def _account_enabled(user: UserRecord) -> bool:
    return user.is_active and user.disabled_at is None


def _unauthenticated() -> AppError:
    return AppError(code="UNAUTHENTICATED", message="未登录或登录已失效", status_code=401)
