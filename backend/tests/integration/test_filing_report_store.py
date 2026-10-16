import re
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
                DELETE FROM reports
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text(
                """
                DELETE FROM analysis_runs
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
def test_saved_report_keeps_the_sample_filing_sources() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t17-{uuid4().hex[:8]}"
    other = f"t17b-{uuid4().hex[:8]}"
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
            found = client.get("/api/v1/instruments/search", params={"q": "AAPL"})
            instrument_id = found.json()["data"]["results"][0]["id"]
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post(
                "/api/v1/analysis/filing",
                headers=headers,
                json={
                    "instrument_id": instrument_id,
                    "as_of": "2024-06-01",
                    "period_a": "2022-12-31",
                    "period_b": "2023-12-31",
                },
            )
            assert created.status_code == 200
            data = created.json()["data"]
            assert data["status"] == "succeeded"
            assert "80" in data["body"]
            assert "120" in data["body"]
            assert "0000320193-23-000001" in data["body"]
            assert "0000320193-24-000099" in data["body"]
            assert "本地样例" in data["body"]
            # 正文里带着 run/user 的 UUID，随机 UUID 有小概率碰巧含 "999"，
            # 所以按「独立的 999」判定：模型编的数字不许出现在规则报告里。
            assert not re.search(r"\b999\b", data["body"])
            detail = client.get(f"/api/v1/analysis/runs/{data['run_id']}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["data"]["body"] == data["body"]
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            hidden = client.get(
                f"/api/v1/analysis/runs/{data['run_id']}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert hidden.status_code == 404
    finally:
        _cleanup(username)
        _cleanup(other)
