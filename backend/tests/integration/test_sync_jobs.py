from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.errors import ProviderError
from app.repositories.accounts import AccountRepository
from app.repositories.market_bars import MarketBarRepository
from app.repositories.sync_jobs import SyncJobRepository, pump_once
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

NOW = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
KEY = "test-queue:same-window"


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


def _cleanup() -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM sync_job_events
                WHERE job_id IN (
                    SELECT id FROM sync_jobs WHERE dedupe_key LIKE 'test-queue:%'
                )
                """
            )
        )
        connection.execute(text("DELETE FROM sync_jobs WHERE dedupe_key LIKE 'test-queue:%'"))
        connection.execute(
            text(
                """
                DELETE FROM refresh_sessions
                WHERE user_id IN (SELECT id FROM users WHERE username LIKE 't07-%')
                """
            )
        )
        connection.execute(
            text(
                """
                DELETE FROM user_preferences
                WHERE user_id IN (SELECT id FROM users WHERE username LIKE 't07-%')
                """
            )
        )
        connection.execute(text("DELETE FROM users WHERE username LIKE 't07-%'"))
    engine.dispose()


def test_duplicate_enqueue_returns_the_active_job() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    repository = SyncJobRepository(engine)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    try:
        _cleanup()
        first, created = repository.enqueue(
            job_type="akshare_daily",
            provider_id=provider_id,
            scope={"display_symbol": "600519.SH"},
            dedupe_key=KEY,
            requested_by=None,
            now=NOW,
        )
        second, again = repository.enqueue(
            job_type="akshare_daily",
            provider_id=provider_id,
            scope={"display_symbol": "600519.SH"},
            dedupe_key=KEY,
            requested_by=None,
            now=NOW,
        )
        assert created is True
        assert again is False
        assert first.id == second.id
        assert second.status == "queued"
    finally:
        _cleanup()
        engine.dispose()


def test_second_worker_does_not_claim_a_locked_job() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    repository = SyncJobRepository(engine)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    session = None
    try:
        _cleanup()
        repository.enqueue(
            job_type="akshare_daily",
            provider_id=provider_id,
            scope={"display_symbol": "600519.SH"},
            dedupe_key=KEY,
            requested_by=None,
            now=NOW,
        )
        session, held = repository.hold_claim(NOW)
        assert held is not None
        assert SyncJobRepository(engine).claim(NOW) is None
    finally:
        if session is not None:
            session.rollback()
            session.close()
        _cleanup()
        engine.dispose()


def test_timeout_requeues_then_stops_at_the_attempt_limit() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    repository = SyncJobRepository(engine)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()

    def fail(job: object) -> int:
        del job
        raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)

    try:
        _cleanup()
        job, _created = repository.enqueue(
            job_type="akshare_daily",
            provider_id=provider_id,
            scope={"display_symbol": "600519.SH"},
            dedupe_key=KEY,
            requested_by=None,
            now=NOW,
            max_attempts=2,
        )
        assert pump_once(repository, NOW, fail) is True
        waiting = repository.get(job.id)
        assert waiting is not None
        assert waiting.status == "queued"
        assert waiting.attempts == 1
        assert pump_once(repository, NOW, fail) is True
        finished = repository.get(job.id)
        assert finished is not None
        assert finished.status == "failed"
        assert finished.attempts == 2
        assert finished.error_summary == "上游超时"
        assert pump_once(repository, NOW, fail) is False
        health = repository.provider_health()["akshare"]
        assert health.status == "down"
    finally:
        _cleanup()
        engine.dispose()


def test_unexpected_error_does_not_store_the_secret() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    repository = SyncJobRepository(engine)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()

    def explode(job: object) -> int:
        del job
        raise RuntimeError("postgresql://finscope:super-secret@db/finscope")

    try:
        _cleanup()
        job, _created = repository.enqueue(
            job_type="akshare_daily",
            provider_id=provider_id,
            scope={"display_symbol": "600519.SH"},
            dedupe_key=KEY,
            requested_by=None,
            now=NOW,
            max_attempts=1,
        )
        assert pump_once(repository, NOW, explode) is True
        finished = repository.get(job.id)
        assert finished is not None
        assert finished.status == "failed"
        assert finished.error_summary == "任务执行失败"
        assert finished.error_summary is not None
        assert "super-secret" not in finished.error_summary
    finally:
        _cleanup()
        engine.dispose()


def test_retry_keeps_the_failed_row_and_opens_a_new_job() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t07-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="admin",
    )
    engine.dispose()
    try:
        _cleanup_jobs_only()
        application = create_app()
        with TestClient(application) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            body = {
                "job_type": "akshare_daily",
                "scope": {
                    "display_symbol": "601318.SH",
                    "observation_start": "1999-01-04",
                    "observation_end": "1999-01-04",
                    "adjustment": "none",
                },
            }
            first = client.post("/api/v1/admin/sync-jobs", headers=headers, json=body)
            assert first.status_code == 200
            assert first.json()["data"]["created"] is True
            job_id = first.json()["data"]["job"]["id"]
            duplicate = client.post("/api/v1/admin/sync-jobs", headers=headers, json=body)
            assert duplicate.json()["data"]["created"] is False
            assert duplicate.json()["data"]["job"]["id"] == job_id
            repository = SyncJobRepository(create_db_engine(get_settings().database_url))
            repository.finish_failure(
                repository.claim(NOW).id,  # type: ignore[union-attr]
                ProviderError(code="DATA_UNAVAILABLE", message="未配置", status_code=503),
                NOW,
            )
            failed = repository.get(job_id)
            assert failed is not None
            assert failed.status == "failed"
            retried = client.post(f"/api/v1/admin/sync-jobs/{job_id}/retries", headers=headers)
            assert retried.status_code == 200
            assert retried.json()["data"]["created"] is True
            assert retried.json()["data"]["job"]["id"] != job_id
            assert repository.get(job_id) is not None
            assert repository.get(job_id).status == "failed"  # type: ignore[union-attr]
    finally:
        _cleanup_http()


def _cleanup_jobs_only() -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM sync_job_events
                WHERE job_id IN (
                    SELECT id FROM sync_jobs
                    WHERE dedupe_key LIKE 'akshare:601318.SH:none:1999-01-04%'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                DELETE FROM sync_jobs
                WHERE dedupe_key LIKE 'akshare:601318.SH:none:1999-01-04%'
                """
            )
        )
    engine.dispose()


def _cleanup_http() -> None:
    _cleanup_jobs_only()
    _cleanup()
