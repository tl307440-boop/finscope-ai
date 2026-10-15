from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.config import get_settings, reset_settings_cache
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.fred import FredVintage
from app.repositories.accounts import AccountRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

OBSERVED = date(1899, 1, 1)


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM macro_observations
                WHERE observation_date = :observed
                """
            ),
            {"observed": OBSERVED},
        )
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
    engine.dispose()


def _admin_token(client: TestClient, username: str) -> str:
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="admin",
    )
    engine.dispose()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    assert isinstance(token, str)
    return token


@pytest.mark.integration
def test_series_catalog_is_unavailable_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "")
    reset_settings_cache()
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    try:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/macro/series")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["availability"] == "unavailable"
        assert body["provider_code"] == "fred"
        codes = {item["series_code"] for item in body["series"]}
        assert "UNRATE" in codes
        assert "GDP" in codes
        assert "1.25" not in response.text
        assert "演示" not in body["notice"] or "不会用演示数字填补" in body["notice"]
    finally:
        reset_settings_cache()


@pytest.mark.integration
def test_missing_key_sync_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "")
    reset_settings_cache()
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t05-{uuid4().hex[:10]}"
    called = False

    async def fetch(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
        nonlocal called
        called = True
        return ()

    try:
        application = create_app()
        with TestClient(application) as client:
            application.state.fred_fetch = fetch
            token = _admin_token(client, username)
            response = client.post(
                "/api/v1/admin/fred-syncs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "series_code": "UNRATE",
                    "observation_start": "1899-01-01",
                    "observation_end": "1899-02-01",
                },
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATA_UNAVAILABLE"
        assert response.json()["error"]["details"]["rows_written"] == 0
        assert response.json()["error"]["details"]["preserved_existing"] is True
        assert called is False
    finally:
        _cleanup(username)
        reset_settings_cache()


@pytest.mark.integration
def test_key_sync_hides_revision_until_the_following_new_york_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRED_API_KEY", "unit-test-fred-key")
    reset_settings_cache()
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t05-{uuid4().hex[:10]}"

    async def fetch(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
        assert series_code == "UNRATE"
        return (
            FredVintage(
                observation_date=OBSERVED,
                value=Decimal("1.25"),
                realtime_start=date(2020, 2, 7),
                realtime_end=date(2020, 3, 5),
            ),
            FredVintage(
                observation_date=OBSERVED,
                value=Decimal("9.75"),
                realtime_start=date(2020, 3, 6),
                realtime_end=date(9999, 12, 31),
            ),
        )

    try:
        application = create_app()
        with TestClient(application) as client:
            application.state.fred_fetch = fetch
            token = _admin_token(client, username)
            synced = client.post(
                "/api/v1/admin/fred-syncs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "series_code": "UNRATE",
                    "observation_start": "1899-01-01",
                    "observation_end": "1899-01-01",
                },
            )
            assert synced.status_code == 200
            assert synced.json()["data"]["rows_written"] == 2
            assert "unit-test-fred-key" not in synced.text
            release_day = _values(client, "2020-02-07")
            next_day = _values(client, "2020-02-08")
            revision_day = _values(client, "2020-03-06")
            after_revision = _values(client, "2020-03-07")
            before_midnight = _values(client, "2020-02-08T04:30:00Z")
        assert release_day == []
        assert next_day == ["1.25"]
        assert revision_day == []
        assert after_revision == ["9.75"]
        assert before_midnight == []
    finally:
        _cleanup(username)
        reset_settings_cache()


def _values(client: TestClient, as_of: str) -> list[str]:
    response = client.get(
        "/api/v1/macro/series/UNRATE/observations",
        params={"as_of": as_of, "start": "1899-01-01", "end": "1899-01-01"},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["time_precision"] == "date"
    return [item["value"] for item in body["observations"]]
