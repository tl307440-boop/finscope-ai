from collections.abc import Iterator
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.services.runtime_settings import invalidate
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

PASSWORD = "correct-horse-9"


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _drop(prefix: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            params = {"p": f"{prefix}%"}
            for table in ("refresh_sessions", "user_preferences"):
                connection.execute(
                    text(
                        f"DELETE FROM {table} WHERE user_id IN "
                        "(SELECT id FROM users WHERE username LIKE :p)"
                    ),
                    params,
                )
            connection.execute(
                text(
                    "DELETE FROM app_settings WHERE updated_by IN "
                    "(SELECT id FROM users WHERE username LIKE :p)"
                ),
                params,
            )
            connection.execute(text("DELETE FROM users WHERE username LIKE :p"), params)
    finally:
        engine.dispose()


@pytest.fixture
def prefix() -> Iterator[str]:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    value = f"rg{uuid4().hex[:8]}"
    invalidate()
    yield value
    _drop(value)
    invalidate()


@pytest.fixture
def client(prefix: str) -> Iterator[TestClient]:
    del prefix
    with TestClient(create_app()) as test_client:
        yield test_client


def _register(client: TestClient, name: str, password: str = PASSWORD, email: str | None = None):
    return client.post(
        "/api/v1/auth/register",
        json={"username": name, "email": email or f"{name}@example.com", "password": password},
    )


def test_register_creates_researcher_and_logs_in(client: TestClient, prefix: str) -> None:
    response = _register(client, f"{prefix}-Ann")
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["user"]["role"] == "researcher"
    assert data["user"]["username"] == f"{prefix}-ann"
    assert "finscope_refresh=" in response.headers["set-cookie"]
    assert PASSWORD not in response.text
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200
    # 账号不区分大小写，用户用注册时的大写写法也能登录。
    again = client.post(
        "/api/v1/auth/login", json={"username": f"{prefix}-ANN", "password": PASSWORD}
    )
    assert again.status_code == 200


def test_register_rejects_bad_input_and_duplicates(client: TestClient, prefix: str) -> None:
    assert _register(client, f"{prefix}-weak", password="short1").status_code == 422
    assert _register(client, f"{prefix}-mail", email="not-an-email").status_code == 422
    assert _register(client, f"{prefix}-dup").status_code == 201
    duplicate = _register(client, f"{prefix}-DUP", email=f"other-{prefix}@example.com")
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ACCOUNT_EXISTS"
    same_mail = _register(client, f"{prefix}-two", email=f"{prefix}-dup@example.com")
    assert same_mail.status_code == 409


def test_register_is_rate_limited_per_client(client: TestClient, prefix: str) -> None:
    limit = get_settings().register_max_per_hour
    codes = [
        _register(client, f"{prefix}-r{i}", password="x").status_code for i in range(limit + 1)
    ]
    assert codes[:limit] == [422] * limit
    assert codes[-1] == 429


def test_admin_can_close_registration(client: TestClient, prefix: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    admin = f"{prefix}-adm"
    try:
        AccountRepository(engine).create_user(
            username=admin,
            email=f"{admin}@example.com",
            password_hash=PasswordService("test").hash(PASSWORD),
            role="admin",
        )
    finally:
        engine.dispose()
    token = client.post(
        "/api/v1/auth/login", json={"username": admin, "password": PASSWORD}
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    original = client.get("/api/v1/auth/options").json()["data"]["registration_open"]
    try:
        closed = client.put(
            "/api/v1/admin/integrations/ALLOW_REGISTRATION",
            json={"value": "false"},
            headers=headers,
        )
        assert closed.status_code == 200
        assert client.get("/api/v1/auth/options").json()["data"]["registration_open"] is False
        denied = _register(client, f"{prefix}-late")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "REGISTRATION_CLOSED"
    finally:
        client.delete("/api/v1/admin/integrations/ALLOW_REGISTRATION", headers=headers)
    assert client.get("/api/v1/auth/options").json()["data"]["registration_open"] is original


def test_change_password_revokes_other_sessions(client: TestClient, prefix: str) -> None:
    name = f"{prefix}-pw"
    first = _register(client, name)
    assert first.status_code == 201
    old_refresh = client.cookies.get("finscope_refresh")
    token = first.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    wrong = client.post(
        "/api/v1/me/password",
        json={"current_password": "not-the-password1", "new_password": "brand-new-pass-2"},
        headers=headers,
    )
    assert wrong.status_code == 400
    weak = client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "short1"},
        headers=headers,
    )
    assert weak.status_code == 422
    same = client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=headers,
    )
    assert same.status_code == 422

    changed = client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "brand-new-pass-2"},
        headers=headers,
    )
    assert changed.status_code == 200
    assert "brand-new-pass-2" not in changed.text

    assert (
        client.post("/api/v1/auth/login", json={"username": name, "password": PASSWORD}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": name, "password": "brand-new-pass-2"}
        ).status_code
        == 200
    )
    # 改密码之前签发的刷新凭证已作废。
    client.cookies.set("finscope_refresh", old_refresh or "", path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 401
