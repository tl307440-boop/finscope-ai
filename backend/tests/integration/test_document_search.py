from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.repositories.document_chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.services.document_index import _vectors, index_next_queued, search_owned_documents
from app.services.pdf_chunks import chunk_pages
from app.services.pdf_text import PageText
from app.utils.time import utc_now
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from tests.unit.pdf_samples import build_text_pdf
from tests.unit.test_pdf_upload import SCANNED_PDF


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
def test_search_returns_the_uploaded_document_and_page(tmp_path: Path) -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t15-{uuid4().hex[:8]}"
    other = f"t15b-{uuid4().hex[:8]}"
    _user(username)
    _user(other)
    pdf = build_text_pdf([" ".join(["alpha"] * 900), "Revenue locator page two"])
    application = create_app()
    application.state.upload_root = tmp_path
    try:
        with TestClient(application) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            uploaded = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("locator.pdf", pdf, "application/pdf")},
            )
            assert uploaded.status_code == 200
            document_id = uploaded.json()["data"]["document_id"]
            engine = create_db_engine(get_settings().database_url)
            index_next_queued(engine, tmp_path)
            engine.dispose()
            indexed = client.post(f"/api/v1/documents/{document_id}/index", headers=headers)
            assert indexed.status_code == 200
            assert indexed.json()["data"]["status"] == "parsed"
            found = client.get(
                "/api/v1/documents/search",
                headers=headers,
                params={"q": "Revenue locator page two"},
            )
            assert found.status_code == 200
            items = found.json()["data"]["items"]
            assert items
            assert items[0]["document_id"] == document_id
            assert items[0]["page_end"] == 2
            assert 1 <= items[0]["page_start"] <= items[0]["page_end"]
            assert "Revenue locator" in items[0]["snippet"]
            assert "local-hashing-v1" in found.json()["data"]["notice"]
            missing = client.get(
                "/api/v1/documents/search",
                headers=headers,
                params={"q": "missing-sentence"},
            )
            assert missing.json()["data"]["items"] == []
            hidden = client.get(
                "/api/v1/documents/search",
                headers=headers,
                params={"q": "Revenue locator", "as_of": "2000-01-01T00:00:00Z"},
            )
            assert hidden.json()["data"]["items"] == []
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            foreign = client.get(
                "/api/v1/documents/search",
                headers={"Authorization": f"Bearer {other_token}"},
                params={"q": "Revenue locator"},
            )
            assert foreign.json()["data"]["items"] == []
            denied = client.post(
                f"/api/v1/documents/{document_id}/index",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert denied.status_code == 404
            scanned = client.post(
                "/api/v1/documents",
                headers=headers,
                files={"file": ("scan.pdf", SCANNED_PDF, "application/pdf")},
            )
            scanned_id = scanned.json()["data"]["document_id"]
            blocked = client.post(f"/api/v1/documents/{scanned_id}/index", headers=headers)
            assert blocked.status_code == 422
    finally:
        _cleanup(username)
        _cleanup(other)


@pytest.mark.integration
def test_chinese_locator_returns_its_page() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t15c-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    user = AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    try:
        document_id = DocumentRepository(engine).add(
            owner_id=user.id,
            title="locator.pdf",
            storage_path=None,
            sha256="b" * 64,
            page_count=2,
            size_bytes=10,
            parse_status="queued",
            parse_error=None,
        )
        pages = [
            PageText(1, " ".join(["alpha"] * 30)),
            PageText(2, "本页只用于检索定位，不代表任何公司的营业收入"),
        ]
        prepared = chunk_pages(pages, target_tokens=8, max_tokens=12, overlap_tokens=2)
        stored, embeddings, search_texts = _vectors(prepared)
        ChunkRepository(engine).replace(
            document_id,
            stored,
            embeddings,
            search_texts,
            page_count=2,
        )
        hits = search_owned_documents(
            ChunkRepository(engine),
            owner_id=user.id,
            query="检索定位",
            as_of=utc_now(),
        )
        assert hits
        assert hits[0].document_id == document_id
        assert hits[0].page_end == 2
        assert "检索定位" in hits[0].snippet
        future = datetime(2000, 1, 1, tzinfo=UTC)
        assert (
            search_owned_documents(
                ChunkRepository(engine),
                owner_id=user.id,
                query="检索定位",
                as_of=future,
            )
            == []
        )
    finally:
        engine.dispose()
        _cleanup(username)
