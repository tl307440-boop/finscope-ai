from pathlib import Path
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


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        for statement in (
            """
            DELETE FROM watchlist_groups
            WHERE user_id IN (SELECT id FROM users WHERE username = :username)
            """,
            """
            DELETE FROM reports
            WHERE user_id IN (SELECT id FROM users WHERE username = :username)
            """,
            """
            DELETE FROM analysis_runs
            WHERE user_id IN (SELECT id FROM users WHERE username = :username)
            """,
            """
            DELETE FROM refresh_sessions
            WHERE user_id IN (SELECT id FROM users WHERE username = :username)
            """,
            """
            DELETE FROM user_preferences
            WHERE user_id IN (SELECT id FROM users WHERE username = :username)
            """,
            "DELETE FROM users WHERE username = :username",
        ):
            connection.execute(text(statement), {"username": username})
    engine.dispose()


def _user(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()


@pytest.mark.integration
def test_watchlist_and_reports_stay_with_their_owner() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    owner = f"t20-{uuid4().hex[:8]}"
    other = f"t20b-{uuid4().hex[:8]}"
    _user(owner)
    _user(other)
    try:
        with TestClient(create_app()) as client:
            owner_token = client.post(
                "/api/v1/auth/login",
                json={"username": owner, "password": "correct-password"},
            ).json()["data"]["access_token"]
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            owner_headers = {"Authorization": f"Bearer {owner_token}"}
            other_headers = {"Authorization": f"Bearer {other_token}"}
            instrument_id = client.get(
                "/api/v1/instruments/search",
                params={"q": "AAPL"},
            ).json()["data"]["results"][0]["id"]
            group = client.post(
                "/api/v1/watchlists",
                headers=owner_headers,
                json={"name": "关注"},
            )
            assert group.status_code == 200
            group_id = group.json()["data"]["id"]
            added = client.post(
                f"/api/v1/watchlists/{group_id}/items",
                headers=owner_headers,
                json={"instrument_id": instrument_id},
            )
            assert added.status_code == 200
            assert added.json()["data"]["items"][0]["name_cn"] == "苹果"
            foreign_lists = client.get("/api/v1/watchlists", headers=other_headers)
            assert foreign_lists.json()["data"]["groups"] == []
            denied = client.delete(
                f"/api/v1/watchlists/{group_id}/items/{instrument_id}",
                headers=other_headers,
            )
            assert denied.status_code == 404
            created = client.post(
                "/api/v1/analysis/filing",
                headers=owner_headers,
                json={
                    "instrument_id": instrument_id,
                    "as_of": "2024-06-01",
                    "period_a": "2022-12-31",
                    "period_b": "2023-12-31",
                },
            )
            report_id = created.json()["data"]["report_id"]
            listed = client.get(
                "/api/v1/analysis/reports", headers=owner_headers, params={"q": "苹果"}
            )
            assert any(item["report_id"] == report_id for item in listed.json()["data"]["reports"])
            assert (
                client.get("/api/v1/analysis/reports", headers=other_headers).json()["data"][
                    "reports"
                ]
                == []
            )
            assert (
                client.delete(
                    f"/api/v1/analysis/reports/{report_id}", headers=other_headers
                ).status_code
                == 404
            )
            assert (
                client.delete(
                    f"/api/v1/analysis/reports/{report_id}",
                    headers=owner_headers,
                ).status_code
                == 200
            )
            remaining = client.get("/api/v1/analysis/reports", headers=owner_headers).json()[
                "data"
            ]["reports"]
            assert all(item["report_id"] != report_id for item in remaining)
    finally:
        _cleanup(owner)
        _cleanup(other)
