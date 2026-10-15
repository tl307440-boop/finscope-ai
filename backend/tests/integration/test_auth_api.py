import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


def _database_ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    if not _database_ready():
        pytest.skip("PostgreSQL 不可用")
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM refresh_sessions
                    WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                    """
                ),
                {"username": username},
            )
            connection.execute(
                text(
                    """
                    DELETE FROM user_preferences
                    WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                    """
                ),
                {"username": username},
            )
            connection.execute(
                text("DELETE FROM users WHERE username = :username"),
                {"username": username},
            )
    finally:
        engine.dispose()


def _create_user(username: str, role: str, password: str = "correct-password") -> None:
    _cleanup(username)
    engine = create_db_engine(get_settings().database_url)
    try:
        AccountRepository(engine).create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash(password),
            role=role,
        )
    finally:
        engine.dispose()


def test_login_sets_httponly_cookie_and_hides_secrets(client: TestClient) -> None:
    username = f"t01-{uuid4().hex[:12]}"
    _create_user(username, "researcher")
    try:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "correct-password"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["token_type"] == "bearer"
        assert body["data"]["user"]["role"] == "researcher"
        assert "password_hash" not in response.text
        assert "correct-password" not in response.text
        cookie = response.headers["set-cookie"].lower()
        assert "finscope_refresh=" in cookie
        assert "httponly" in cookie
        assert "samesite=lax" in cookie
        assert body["data"]["access_token"] not in cookie
    finally:
        _cleanup(username)


def test_unknown_user_and_wrong_password_match_and_are_not_logged(client: TestClient) -> None:
    username = f"t01-{uuid4().hex[:12]}"
    _create_user(username, "researcher")
    messages: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("app.services.auth")
    handler = ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        unknown = client.post(
            "/api/v1/auth/login",
            json={"username": f"missing-{uuid4().hex[:8]}", "password": "super-secret-pass"},
        )
        wrong = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "super-secret-pass"},
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["error"] == wrong.json()["error"]
        assert "super-secret-pass" not in unknown.text
        assert "super-secret-pass" not in wrong.text
        assert "super-secret-pass" not in " ".join(messages)
    finally:
        logger.removeHandler(handler)
        _cleanup(username)


def test_validation_error_does_not_echo_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ab", "password": "super-secret-pass"},
    )
    assert response.status_code == 422
    assert "super-secret-pass" not in response.text


def test_refresh_rotates_and_logout_revokes(client: TestClient) -> None:
    username = f"t01-{uuid4().hex[:12]}"
    _create_user(username, "researcher")
    try:
        first = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "correct-password"},
        )
        old_refresh = first.cookies.get("finscope_refresh")
        assert old_refresh
        second = client.post("/api/v1/auth/refresh")
        assert second.status_code == 200
        client.cookies.clear()
        client.cookies.set("finscope_refresh", old_refresh)
        stale = client.post("/api/v1/auth/refresh")
        assert stale.status_code == 401
        fresh = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "correct-password"},
        )
        assert fresh.status_code == 200
        logged_out = client.post("/api/v1/auth/logout")
        assert logged_out.status_code == 204
        again = client.post("/api/v1/auth/refresh")
        assert again.status_code == 401
    finally:
        _cleanup(username)


def test_me_requires_login_and_preferences_update(client: TestClient) -> None:
    username = f"t01-{uuid4().hex[:12]}"
    _create_user(username, "researcher")
    try:
        anonymous = client.get("/api/v1/me")
        assert anonymous.status_code == 401
        logged_in = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "correct-password"},
        )
        token = logged_in.json()["data"]["access_token"]
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["data"]["preferences"]["theme"] == "dark"
        updated = client.patch(
            "/api/v1/me/preferences",
            headers={"Authorization": f"Bearer {token}"},
            json={"theme": "light"},
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["preferences"]["theme"] == "light"
        assert "correct-password" not in updated.text
    finally:
        _cleanup(username)


def test_researcher_cannot_read_admin_providers(client: TestClient) -> None:
    researcher = f"t01-{uuid4().hex[:12]}"
    admin = f"t01-{uuid4().hex[:12]}"
    _create_user(researcher, "researcher")
    _create_user(admin, "admin")
    try:
        researcher_token = client.post(
            "/api/v1/auth/login",
            json={"username": researcher, "password": "correct-password"},
        ).json()["data"]["access_token"]
        forbidden = client.get(
            "/api/v1/admin/providers",
            headers={"Authorization": f"Bearer {researcher_token}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"
        admin_token = client.post(
            "/api/v1/auth/login",
            json={"username": admin, "password": "correct-password"},
        ).json()["data"]["access_token"]
        allowed = client.get(
            "/api/v1/admin/providers",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert allowed.status_code == 200
        codes = [item["code"] for item in allowed.json()["data"]["providers"]]
        assert codes == ["fred", "sec_edgar", "tushare", "akshare"]
        assert "change-me" not in allowed.text
        assert "correct-password" not in allowed.text
    finally:
        _cleanup(researcher)
        _cleanup(admin)
