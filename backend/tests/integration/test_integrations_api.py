from collections.abc import Iterator
from uuid import uuid4

import pytest
from app.api.v1 import integrations as integrations_module
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.services.integration_probe import ProbeResult
from app.services.runtime_settings import effective_settings, invalidate
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

SECRET = "tiingo-secret-1234567890"


def _ready() -> bool:
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
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    with engine.connect() as connection:
        saved = connection.execute(text("SELECT * FROM app_settings")).mappings().all()
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM app_settings"))
    invalidate()
    with TestClient(create_app()) as test_client:
        yield test_client
    # 把开发库里原有的界面配置原样放回去，测试不能吃掉真实密钥。
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM app_settings"))
        for row in saved:
            connection.execute(
                text(
                    "INSERT INTO app_settings (key, value_enc, is_secret, updated_by, updated_at) "
                    "VALUES (:key, :value_enc, :is_secret, :updated_by, :updated_at)"
                ),
                dict(row),
            )
    engine.dispose()
    invalidate()


def _make_user(role: str) -> tuple[str, str]:
    username = f"it-{uuid4().hex[:10]}"
    engine = create_db_engine(get_settings().database_url)
    try:
        AccountRepository(engine).create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash("correct-password"),
            role=role,
        )
    finally:
        engine.dispose()
    return username, "correct-password"


def _drop_user(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            params = {"u": username}
            connection.execute(
                text(
                    "DELETE FROM app_settings WHERE updated_by IN "
                    "(SELECT id FROM users WHERE username = :u)"
                ),
                params,
            )
            for table in ("refresh_sessions", "user_preferences"):
                connection.execute(
                    text(
                        f"DELETE FROM {table} WHERE user_id IN "
                        "(SELECT id FROM users WHERE username = :u)"
                    ),
                    params,
                )
            connection.execute(text("DELETE FROM users WHERE username = :u"), params)
    finally:
        engine.dispose()


def _headers(client: TestClient, role: str) -> tuple[dict[str, str], str]:
    username, password = _make_user(role)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}, username


def test_integrations_require_admin(client: TestClient) -> None:
    assert client.get("/api/v1/admin/integrations").status_code == 401
    headers, username = _headers(client, "researcher")
    try:
        assert client.get("/api/v1/admin/integrations", headers=headers).status_code == 403
        response = client.put(
            "/api/v1/admin/integrations/TIINGO_API_KEY", json={"value": SECRET}, headers=headers
        )
        assert response.status_code == 403
    finally:
        _drop_user(username)


def test_secret_is_masked_encrypted_and_effective(client: TestClient) -> None:
    headers, username = _headers(client, "admin")
    try:
        saved = client.put(
            "/api/v1/admin/integrations/TIINGO_API_KEY", json={"value": SECRET}, headers=headers
        )
        assert saved.status_code == 200
        assert SECRET not in saved.text
        item = saved.json()["data"]
        assert item["configured"] is True
        assert item["source"] == "database"
        assert item["preview"].endswith("7890")

        listing = client.get("/api/v1/admin/integrations", headers=headers)
        assert SECRET not in listing.text
        keys = {entry["key"] for entry in listing.json()["data"]["items"]}
        assert {"TIINGO_API_KEY", "FRED_API_KEY", "SEC_USER_AGENT", "LLM_API_KEY"} <= keys

        engine = create_db_engine(get_settings().database_url)
        try:
            with engine.connect() as connection:
                raw = connection.execute(
                    text("SELECT value_enc FROM app_settings WHERE key = 'TIINGO_API_KEY'")
                ).scalar_one()
            assert SECRET not in raw
            assert effective_settings(engine, use_cache=False).tiingo_api_key == SECRET

            cleared = client.delete("/api/v1/admin/integrations/TIINGO_API_KEY", headers=headers)
            assert cleared.status_code == 200
            assert effective_settings(engine, use_cache=False).tiingo_api_key == (
                get_settings().tiingo_api_key
            )
        finally:
            engine.dispose()
    finally:
        _drop_user(username)


def test_invalid_values_and_unknown_keys(client: TestClient) -> None:
    headers, username = _headers(client, "admin")
    try:
        base = "/api/v1/admin/integrations"
        assert (
            client.put(f"{base}/DATABASE_URL", json={"value": "x"}, headers=headers).status_code
            == 404
        )
        bad_ua = client.put(f"{base}/SEC_USER_AGENT", json={"value": "no contact"}, headers=headers)
        assert bad_ua.status_code == 422
        bad_url = client.put(f"{base}/LLM_API_BASE_URL", json={"value": "ftp://x"}, headers=headers)
        assert bad_url.status_code == 422
    finally:
        _drop_user(username)


def test_probe_tests_candidate_without_saving_or_echoing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def fake_probe(key: str, value: str, **_: str) -> ProbeResult:
        seen[key] = value
        return ProbeResult(True, "ok")

    monkeypatch.setattr(integrations_module, "probe", fake_probe)
    headers, username = _headers(client, "admin")
    try:
        response = client.post(
            "/api/v1/admin/integrations/TIINGO_API_KEY/test",
            json={"value": SECRET},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["data"] == {"ok": True, "message": "ok"}
        assert SECRET not in response.text
        assert seen["TIINGO_API_KEY"] == SECRET
        listing = client.get("/api/v1/admin/integrations", headers=headers).json()["data"]["items"]
        tiingo = next(entry for entry in listing if entry["key"] == "TIINGO_API_KEY")
        assert tiingo["source"] != "database"
    finally:
        _drop_user(username)
