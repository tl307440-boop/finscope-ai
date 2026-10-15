import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from app.core.exceptions import AppError
from app.core.security import (
    PasswordService,
    TokenService,
    hash_refresh_token,
    refresh_cookie_secure,
)
from app.schemas.auth import list_provider_credentials
from app.scripts.seed import ensure_admin
from app.services.auth import AuthService
from app.services.auth_types import PreferenceRecord, SessionRecord, UserRecord
from app.services.login_limiter import LoginRateLimiter


class MemoryUsers:
    def __init__(self, user: UserRecord | None = None) -> None:
        self.user = user
        self.created: list[dict[str, str]] = []

    def get_by_username(self, username: str) -> UserRecord | None:
        if self.user and self.user.username == username:
            return self.user
        return None

    def get_by_id(self, user_id: object) -> UserRecord | None:
        if self.user and self.user.id == user_id:
            return self.user
        return None

    def get_preferences(self, user_id: object) -> PreferenceRecord | None:
        del user_id
        return None

    def update_preferences(
        self,
        user_id: object,
        changes: dict[str, str],
        now: datetime,
    ) -> PreferenceRecord:
        del user_id, changes, now
        raise AssertionError("not used")

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str,
    ) -> object:
        self.created.append(
            {
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "role": role,
            }
        )
        return object()


class MemorySessions:
    def __init__(self) -> None:
        self.rows: list[SessionRecord] = []

    def save(
        self,
        record: SessionRecord,
        *,
        created_at: datetime,
        device_label: str | None,
    ) -> None:
        del created_at, device_label
        self.rows.append(record)

    def get_by_hash(self, token_hash: str) -> SessionRecord | None:
        for row in self.rows:
            if row.token_hash == token_hash:
                return row
        return None

    def revoke(self, session_id: object, now: datetime) -> None:
        for index, row in enumerate(self.rows):
            if row.id == session_id and row.revoked_at is None:
                self.rows[index] = SessionRecord(
                    id=row.id,
                    user_id=row.user_id,
                    token_hash=row.token_hash,
                    expires_at=row.expires_at,
                    revoked_at=now,
                )


def _user(password_hash: str, *, active: bool = True) -> UserRecord:
    return UserRecord(
        id=uuid4(),
        username="researcher",
        email="researcher@example.com",
        password_hash=password_hash,
        role="researcher",
        is_active=active,
        disabled_at=None if active else datetime(2026, 10, 15, tzinfo=UTC),
    )


def _service(
    user: UserRecord | None,
    limiter: LoginRateLimiter | None = None,
) -> tuple[AuthService, MemorySessions]:
    passwords = PasswordService("test")
    sessions = MemorySessions()
    service = AuthService(
        users=MemoryUsers(user),
        sessions=sessions,
        passwords=passwords,
        tokens=TokenService("test-secret-not-for-production-32ch", 15),
        limiter=limiter or LoginRateLimiter(max_failures=5, window_seconds=300),
        refresh_token_days=7,
        clock=lambda: datetime(2026, 10, 15, 8, tzinfo=UTC),
    )
    return service, sessions


def test_password_hash_is_argon2id_and_hides_plaintext() -> None:
    passwords = PasswordService("test")
    stored = passwords.hash("correct-password")
    assert stored.startswith("$argon2id$")
    assert "correct-password" not in stored
    assert passwords.verify(stored, "correct-password") is True
    assert passwords.verify(stored, "wrong-password") is False
    assert passwords.verify(None, "wrong-password") is False


def test_unknown_user_and_bad_password_use_the_same_error() -> None:
    passwords = PasswordService("test")
    user = _user(passwords.hash("correct-password"))
    missing, _sessions = _service(None)
    wrong, _sessions = _service(user)
    with pytest.raises(AppError) as missing_error:
        missing.login(username="missing-user", password="super-secret-pass")
    with pytest.raises(AppError) as wrong_error:
        wrong.login(username="researcher", password="super-secret-pass")
    assert missing_error.value.code == wrong_error.value.code == "ACCOUNT_INVALID"
    assert missing_error.value.message == wrong_error.value.message == "账号或密码错误"
    assert "super-secret-pass" not in str(missing_error.value)
    assert "super-secret-pass" not in str(wrong_error.value)


def test_disabled_account_uses_the_same_login_error() -> None:
    passwords = PasswordService("test")
    user = _user(passwords.hash("correct-password"), active=False)
    service, _sessions = _service(user)
    with pytest.raises(AppError) as caught:
        service.login(username="researcher", password="correct-password")
    assert caught.value.message == "账号或密码错误"


def test_login_failure_log_omits_password(caplog: pytest.LogCaptureFixture) -> None:
    service, _sessions = _service(None)
    with caplog.at_level(logging.INFO, logger="app.services.auth"), pytest.raises(AppError):
        service.login(username="missing-user", password="super-secret-pass")
    assert "super-secret-pass" not in caplog.text


def test_login_is_rate_limited_without_echoing_the_password() -> None:
    limiter = LoginRateLimiter(max_failures=2, window_seconds=60)
    service, _sessions = _service(None, limiter)
    for _ in range(2):
        with pytest.raises(AppError) as caught:
            service.login(username="researcher", password="super-secret-pass")
        assert caught.value.status_code == 401
    with pytest.raises(AppError) as limited:
        service.login(username="researcher", password="super-secret-pass")
    assert limited.value.status_code == 429
    assert "super-secret-pass" not in limited.value.message


def test_rate_limit_window_expires() -> None:
    now = datetime(2026, 10, 15, 8, tzinfo=UTC)
    limiter = LoginRateLimiter(max_failures=1, window_seconds=10)
    limiter.record_failure("researcher", now)
    with pytest.raises(AppError):
        limiter.check("researcher", now + timedelta(seconds=10))
    limiter.check("researcher", now + timedelta(seconds=11))


def test_refresh_rotation_rejects_the_previous_token() -> None:
    passwords = PasswordService("test")
    user = _user(passwords.hash("correct-password"))
    service, sessions = _service(user)
    first = service.login(username="researcher", password="correct-password")
    second = service.refresh(first.refresh_token)
    assert second.refresh_token != first.refresh_token
    with pytest.raises(AppError) as caught:
        service.refresh(first.refresh_token)
    assert caught.value.status_code == 401
    assert sum(1 for row in sessions.rows if row.revoked_at is None) == 1


def test_access_token_does_not_contain_the_password_and_rejects_bad_signatures() -> None:
    passwords = PasswordService("test")
    user = _user(passwords.hash("correct-password"))
    service, _sessions = _service(user)
    result = service.login(username="researcher", password="correct-password")
    assert "correct-password" not in result.access_token
    payload = jwt.decode(
        result.access_token,
        "test-secret-not-for-production-32ch",
        algorithms=["HS256"],
    )
    assert "password" not in payload
    forged = jwt.encode(
        {"sub": str(user.id), "typ": "access", "exp": 9999999999},
        "other-secret-not-for-production-32",
        algorithm="HS256",
    )
    with pytest.raises(AppError):
        service.user_from_access_token(forged)


def test_refresh_token_is_stored_only_as_a_hash() -> None:
    raw = "plain-refresh-token"
    assert hash_refresh_token(raw) != raw
    assert "plain-refresh-token" not in hash_refresh_token(raw)


def test_refresh_cookie_is_secure_only_in_production() -> None:
    assert refresh_cookie_secure("production") is True
    assert refresh_cookie_secure("development") is False
    assert refresh_cookie_secure("test") is False


def test_provider_status_does_not_reveal_secrets() -> None:
    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        fred_api_key="super-secret-fred",
        tushare_token="",
        sec_user_agent="FinScopeAI research-admin@example.com",
    )
    rendered = list_provider_credentials(settings).model_dump_json()
    assert "super-secret-fred" not in rendered
    assert '"code":"fred"' in rendered.replace(" ", "")
    assert '"credential_present":true' in rendered.replace(" ", "")


def test_ensure_admin_hashes_password_and_does_not_reset_existing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    passwords = PasswordService("test")
    users = MemoryUsers()
    assert (
        ensure_admin(
            users,
            passwords,
            username="admin",
            email="admin@example.com",
            password="super-secret-pass",
        )
        == "created"
    )
    assert users.created[0]["password_hash"].startswith("$argon2id$")
    assert "super-secret-pass" not in users.created[0]["password_hash"]
    users.user = _user(users.created[0]["password_hash"])
    users.user = UserRecord(
        id=uuid4(),
        username="admin",
        email="admin@example.com",
        password_hash=users.created[0]["password_hash"],
        role="admin",
        is_active=True,
        disabled_at=None,
    )
    assert (
        ensure_admin(
            users,
            passwords,
            username="admin",
            email="admin@example.com",
            password="super-secret-pass",
        )
        == "exists"
    )
    assert len(users.created) == 1
    assert "super-secret-pass" not in capsys.readouterr().out
