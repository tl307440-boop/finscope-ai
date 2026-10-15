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
from tests.unit.test_pdf_upload import SCANNED_PDF, TEXT_PDF


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
                DELETE FROM documents
                WHERE owner_id IN (SELECT id FROM users WHERE username = :username)
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
def test_upload_rejects_bad_files_and_reuses_duplicates(tmp_path: Path) -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t14-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()
    application = create_app()
    application.state.upload_root = tmp_path
    try:
        with TestClient(application) as client:
            denied = client.post(
                "/api/v1/documents",
                files={"file": ("note.pdf", b"hello", "application/pdf")},
            )
            assert denied.status_code == 401
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            bad = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("../../evil.pdf", b"not-pdf", "application/pdf")},
            )
            assert bad.status_code == 422
            assert bad.json()["error"]["code"] == "REJECTED_FORMAT"
            first = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("report.pdf", TEXT_PDF, "application/pdf")},
            )
            assert first.status_code == 200
            assert first.json()["data"]["status"] == "queued"
            again = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("copy.pdf", TEXT_PDF, "application/pdf")},
            )
            assert again.status_code == 200
            assert again.json()["data"]["status"] == "duplicate"
            assert again.json()["data"]["document_id"] == first.json()["data"]["document_id"]
            scanned = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("scan.pdf", SCANNED_PDF, "application/pdf")},
            )
            assert scanned.status_code == 200
            assert scanned.json()["data"]["status"] == "ocr_required"
            damaged = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("broken.pdf", b"%PDF-1.4\nbroken", "application/pdf")},
            )
            assert damaged.status_code == 200
            assert damaged.json()["data"]["status"] == "failed"
            listed = client.get("/api/v1/documents", headers=headers)
            statuses = {item["parse_status"] for item in listed.json()["data"]["documents"]}
            assert statuses == {"queued", "ocr_required", "failed"}
        stored = list(tmp_path.rglob("*.pdf"))
        assert len(stored) == 2
        assert all(path.name != "report.pdf" for path in stored)
        assert all(path.name != "evil.pdf" for path in stored)
    finally:
        _cleanup(username)
