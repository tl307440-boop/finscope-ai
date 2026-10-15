import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.repositories.macro_china import MacroChinaRepository
from app.repositories.news_events import NewsEventRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

PASSWORD = "correct-password"

CSV_HEADER = "series_code,observation_date,value,unit,source,published_at"


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
    with TestClient(create_app()) as test_client:
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


def _create_user(username: str, role: str) -> None:
    _cleanup(username)
    engine = create_db_engine(get_settings().database_url)
    try:
        AccountRepository(engine).create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash(PASSWORD),
            role=role,
        )
    finally:
        engine.dispose()


def _token(client: TestClient, username: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200
    return str(response.json()["data"]["access_token"])


def _csv(*lines: str) -> str:
    return "\n".join([CSV_HEADER, *lines])


def _delete_observations(observation_date: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM macro_observations
                    WHERE series_id = (
                        SELECT id FROM macro_series WHERE series_code = 'CPI_CN'
                    )
                    AND observation_date = :observation_date
                    """
                ),
                {"observation_date": observation_date},
            )
    finally:
        engine.dispose()


def test_china_observations_import_requires_admin_token(client: TestClient) -> None:
    row = "CPI_CN,2026-01-01,0.8,Percent,国家统计局,2026-02-09"
    anonymous = client.post("/api/v1/macro/china/observations", json={"csv_text": _csv(row)})
    assert anonymous.status_code == 401
    username = f"t24-{uuid4().hex[:12]}"
    _create_user(username, "researcher")
    try:
        forbidden = client.post(
            "/api/v1/macro/china/observations",
            json={"csv_text": _csv(row)},
            headers={"Authorization": f"Bearer {_token(client, username)}"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"
    finally:
        _cleanup(username)


def test_china_observations_import_is_idempotent_and_reports_bad_rows(
    client: TestClient,
) -> None:
    username = f"t24a-{uuid4().hex[:12]}"
    _create_user(username, "admin")
    observation_date = "1990-01-01"
    good = f"CPI_CN,{observation_date},0.8,Percent,国家统计局,1990-02-09"
    try:
        headers = {"Authorization": f"Bearer {_token(client, username)}"}
        first = client.post(
            "/api/v1/macro/china/observations",
            json={
                "csv_text": _csv(
                    good,
                    "CPI_CN,1990-01-01,not-a-number,Percent,国家统计局,1990-02-09",
                    "NOT_A_SERIES,1990-01-01,1.0,Percent,国家统计局,1990-02-09",
                )
            },
            headers=headers,
        )
        assert first.status_code == 200
        body = first.json()["data"]
        assert body["accepted"] == 1
        assert len(body["errors"]) == 2
        cpi = next(item for item in body["series"] if item["code"] == "CPI_CN")
        assert cpi["observation_count"] >= 1
        # NUMERIC(24,6) 存回来是 0.800000；响应里 exclude_none，字段缺失即无数据。
        assert cpi["latest_value"] == "0.800000"

        again = client.post(
            "/api/v1/macro/china/observations",
            json={"csv_text": _csv(good)},
            headers=headers,
        )
        assert again.status_code == 200
        replay = again.json()["data"]
        assert replay["accepted"] == 0
        assert replay["duplicate_count"] == 1
        assert any("已存在" in item for item in replay["errors"])
    finally:
        _delete_observations(observation_date)
        _cleanup(username)


def test_hotspot_event_import_shows_up_in_hotspots_and_digest(client: TestClient) -> None:
    username = f"t23-{uuid4().hex[:12]}"
    _create_user(username, "admin")
    settings = get_settings()
    original = settings.enable_news
    settings.enable_news = True
    title = f"热度用例公告 {uuid4().hex[:8]}"
    url = f"https://www.cninfo.com.cn/new/disclosure/detail?anno={uuid4().hex[:12]}"
    published = datetime.now(UTC).replace(microsecond=0)
    engine = create_db_engine(settings.database_url)
    repository = NewsEventRepository(engine)
    created_id = None
    try:
        headers = {"Authorization": f"Bearer {_token(client, username)}"}
        bad = client.post(
            "/api/v1/market/hotspots/events",
            json={
                "events": [
                    {
                        "title": "来源不合规的公告",
                        "canonical_url": "http://blog.example.com/a",
                        "source_name": "some_blog",
                        "published_at": published.isoformat(),
                        "event_type": "filing",
                        "usage_rights": "unknown",
                    }
                ]
            },
            headers=headers,
        )
        assert bad.status_code == 200
        rejected = bad.json()["data"]
        assert rejected["accepted"] == 0
        assert rejected["errors"]

        accepted = client.post(
            "/api/v1/market/hotspots/events",
            json={
                "events": [
                    {
                        "title": title,
                        "canonical_url": url,
                        "source_name": "cninfo",
                        "published_at": published.isoformat(),
                        "event_type": "dividend",
                        "usage_rights": "public-disclosure",
                    }
                ]
            },
            headers=headers,
        )
        assert accepted.status_code == 200
        imported = accepted.json()["data"]
        assert imported["accepted"] == 1
        assert imported["duplicate_count"] == 0
        assert imported["events"][0]["source_badge"] == "巨潮资讯"
        created_id = imported["events"][0]["id"]

        hotspots = client.get("/api/v1/market/hotspots")
        assert hotspots.status_code == 200
        data = hotspots.json()["data"]
        matching = [item for item in data["events"] if item["title"] == title]
        assert len(matching) == 1
        assert matching[0]["source_badge"] == "巨潮资讯"
        assert matching[0]["event_type_label"] == "分红送转"
        assert matching[0]["usage_rights"] == "public-disclosure"
        assert data["count"] == len(data["events"])
        assert any(item["day"] == published.date().isoformat() for item in data["timeline"])
        assert any(
            item["event_type"] == "dividend" and item["count"] >= 1 for item in data["topics"]
        )
        assert "巨潮资讯" in data["sources"]

        digest = client.get("/api/v1/me/digest", headers=headers)
        assert digest.status_code == 200
        assert "合规公告" in json.dumps(digest.json()["data"], ensure_ascii=False)
    finally:
        settings.enable_news = original
        if created_id is not None:
            from uuid import UUID

            repository.delete_by_id(UUID(created_id))
        engine.dispose()
        _cleanup(username)


def test_hotspot_window_without_events_returns_empty_list_not_fake_numbers(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/market/hotspots",
        params={"start": "1990-01-01", "end": "1990-01-02"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 0
    assert data["events"] == []
    assert data["topics"] == []
    assert data["timeline"] == []
    assert "0 条" in data["notice"] or "ENABLE_NEWS=false" in data["notice"]
    assert data["start"] == "1990-01-01"
    assert data["end"] == "1990-01-02"

    inverted = client.get(
        "/api/v1/market/hotspots", params={"start": "2026-01-02", "end": "2026-01-01"}
    )
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "INVALID_RANGE"


def test_macro_china_reports_real_stored_counts(client: TestClient) -> None:
    response = client.get("/api/v1/macro/china")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["series"]) == 6
    engine = create_db_engine(get_settings().database_url)
    try:
        summaries = {
            item.series_code: item for item in MacroChinaRepository(engine).list_series_summary()
        }
    finally:
        engine.dispose()
    for item in data["series"]:
        summary = summaries.get(item["code"])
        expected = 0 if summary is None else summary.observation_count
        assert item["observation_count"] == expected
        if expected == 0:
            assert item["status"] == "unavailable"
            # 响应走 exclude_none：无数据时这些字段直接不出现，而不是编造 0。
            assert item.get("latest_value") is None
            assert item.get("latest_observation_date") is None
        else:
            assert item["status"] in {"stored", "stale"}
            assert item["latest_value"] is not None
    assert "还没有导入正式 CSV" in data["notice"]
