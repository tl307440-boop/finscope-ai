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
        connection.execute(
            text(
                """
                DELETE FROM agent_simulation_runs
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
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
            text("DELETE FROM users WHERE username = :username"), {"username": username}
        )
    engine.dispose()


@pytest.mark.integration
def test_mock_simulation_persists_rating_without_an_order() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t29-{uuid4().hex[:8]}"
    other = f"t29b-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    accounts = AccountRepository(engine)
    accounts.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    accounts.create_user(
        username=other,
        email=f"{other}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()
    try:
        with TestClient(create_app()) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post(
                "/api/v1/simulation/runs",
                headers=headers,
                json={"symbol": "600519.SH", "as_of": "2026-06-01", "max_debate_rounds": 1},
            )
            assert created.status_code == 200
            data = created.json()["data"]
            assert data["rating"] == "Hold"
            assert "Market Analyst" in data["nodes"]
            assert "Conservative Analyst" in data["nodes"]
            assert "委托" in data["disclaimer"]
            detail = client.get(f"/api/v1/simulation/runs/{data['run_id']}", headers=headers)
            assert detail.status_code == 200
            stream = client.get(f"/api/v1/simulation/runs/{data['run_id']}/stream", headers=headers)
            assert "event: node_started" in stream.text
            assert "Market Analyst" in stream.text
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            hidden = client.get(
                f"/api/v1/simulation/runs/{data['run_id']}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert hidden.status_code == 404
            missing = client.get("/api/v1/macro/china")
            names = [item["code"] for item in missing.json()["data"]["series"]]
            assert "SOCIAL_FINANCING" in names
            assert all(item["status"] == "unavailable" for item in missing.json()["data"]["series"])
            hot = client.get("/api/v1/market/hotspots")
            assert "情绪" in hot.json()["data"]["label"]
            temp = client.get("/api/v1/market/temperature")
            assert temp.json()["data"]["label"] in {"无法判断", "偏弱", "中性", "偏强"}
    finally:
        _cleanup(username)
        _cleanup(other)
