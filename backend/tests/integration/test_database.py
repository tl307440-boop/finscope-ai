from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.services.probes import DatabaseProbe, WorkerProbe
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]


def test_foundation_migration_declares_pgvector_and_heartbeat() -> None:
    source = (ROOT / "alembic" / "versions" / "20261015_0001_enable_pgvector.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in source
    assert "worker_heartbeats" in source


def test_database_probe_down_does_not_leak_password() -> None:
    engine = create_db_engine("postgresql+psycopg://finscope:super-secret@127.0.0.1:1/finscope")
    try:
        result = DatabaseProbe(engine).check()
    finally:
        engine.dispose()
    assert result.status == "down"
    assert result.reason == "database_unavailable"
    assert "super-secret" not in str(result)


@pytest.mark.integration
def test_migration_heartbeat_and_extension_roundtrip() -> None:
    database_url = get_settings().database_url
    engine = create_db_engine(database_url)
    try:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            pytest.skip("PostgreSQL 不可用，跳过迁移集成测试")

        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        with engine.connect() as connection:
            extension = connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one()
        assert extension == "vector"

        now = datetime.now(UTC)
        repository = WorkerHeartbeatRepository(engine, "worker-test")
        probe = WorkerProbe(repository)
        missing = probe.check(now=now, ttl_seconds=30)
        assert missing.status == "down"
        assert missing.reason == "heartbeat_missing"

        repository.upsert(status="alive", beat_at=now, pid=42)
        fresh = probe.check(now=now, ttl_seconds=30)
        assert fresh.status == "up"
        stale = probe.check(now=now + timedelta(seconds=31), ttl_seconds=30)
        assert stale.status == "down"
        assert stale.reason == "heartbeat_stale"
        database = DatabaseProbe(engine).check()
        assert database.status == "up"
    finally:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM worker_heartbeats WHERE service_name = 'worker-test'")
                )
        except SQLAlchemyError:
            pass
        engine.dispose()
