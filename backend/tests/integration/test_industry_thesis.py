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
                DELETE FROM industry_theses
                WHERE created_by IN (SELECT id FROM users WHERE username = :username)
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
def test_thesis_keeps_support_and_challenge_from_stored_facts() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    owner = f"t21-{uuid4().hex[:8]}"
    other = f"t21b-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    accounts = AccountRepository(engine)
    accounts.create_user(
        username=owner,
        email=f"{owner}@example.com",
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
            owner_token = client.post(
                "/api/v1/auth/login",
                json={"username": owner, "password": "correct-password"},
            ).json()["data"]["access_token"]
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {owner_token}"}
            other_headers = {"Authorization": f"Bearer {other_token}"}
            industries = client.get("/api/v1/industries", params={"q": "信息技术"})
            industry_id = industries.json()["data"]["industries"][0]["id"]
            facts = client.get(
                f"/api/v1/industries/{industry_id}/facts",
                params={"as_of": "2024-06-01"},
            ).json()["data"]["facts"]
            revenue = next(
                item
                for item in facts
                if item["quote"].endswith("120 USD 申报号 0000320193-24-000099")
            )
            shares = next(item for item in facts if " shares " in f" {item['quote']} ")
            created = client.post(
                f"/api/v1/industries/{industry_id}/theses",
                headers=headers,
                json={
                    "title": "样例收入比较",
                    "hypothesis": "截止日可见的样例里，后期美元收入高于早期。",
                    "support_condition": "能指出后期美元收入及其申报号",
                    "challenge_condition": "股数行不能当作收入证据",
                },
            )
            thesis_id = created.json()["data"]["id"]
            assert created.json()["data"]["status"] == "open"
            supported = client.post(
                f"/api/v1/theses/{thesis_id}/evidence",
                headers=headers,
                json={"fact_id": revenue["id"], "stance": "support"},
            )
            challenged = client.post(
                f"/api/v1/theses/{thesis_id}/evidence",
                headers=headers,
                json={"fact_id": shares["id"], "stance": "challenge"},
            )
            assert supported.status_code == 200
            assert challenged.status_code == 200
            assessed = client.post(
                f"/api/v1/theses/{thesis_id}/assessments",
                headers=headers,
                json={"as_of": "2024-06-01"},
            )
            data = assessed.json()["data"]
            assert data["status"] == "contested"
            assert "不是已证实的结论" in data["rationale"]
            stances = {item["stance"] for item in data["evidence"]}
            assert stances == {"support", "challenge"}
            assert "120" in next(
                item["quote"] for item in data["evidence"] if item["stance"] == "support"
            )
            assert "999" not in data["rationale"]
            hidden = client.get(
                f"/api/v1/industries/{industry_id}/theses",
                headers=other_headers,
                params={"as_of": "2024-06-01"},
            )
            assert hidden.json()["data"]["theses"] == []
            early = client.post(
                f"/api/v1/theses/{thesis_id}/assessments",
                headers=headers,
                json={"as_of": "2023-01-31"},
            )
            assert early.json()["data"]["status"] == "open"
    finally:
        _cleanup(owner)
        _cleanup(other)
