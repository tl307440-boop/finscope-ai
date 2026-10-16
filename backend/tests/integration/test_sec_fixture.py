from datetime import date

import pytest
from app.catalog.reference_data import reference_instruments
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.sec_edgar import load_fixture, parse_companyfacts, payload_sha256
from app.repositories.filings import FilingRepository
from app.repositories.instruments import InstrumentRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


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
        InstrumentRepository(engine).upsert_reference(reference_instruments())
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
                DELETE FROM thesis_evidence
                WHERE fact_id IN (
                    SELECT s.id FROM sec_facts s JOIN filings f ON f.id = s.filing_id
                    WHERE f.accession_no LIKE '0000320193-%' AND f.status = 'fixture'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                DELETE FROM sec_facts
                WHERE filing_id IN (
                    SELECT id FROM filings
                    WHERE accession_no LIKE '0000320193-%' AND status = 'fixture'
                )
                """
            )
        )
        connection.execute(
            text(
                "DELETE FROM filings WHERE accession_no LIKE '0000320193-%' AND status = 'fixture'"
            )
        )
    engine.dispose()


def _has_live_filings() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        return FilingRepository(engine).has_live_filings("0000320193")
    finally:
        engine.dispose()


@pytest.mark.integration
def test_fixture_is_idempotent_and_visible_with_its_source() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    if _has_live_filings():
        pytest.skip("库里已有 AAPL 的真实 EDGAR 财报，样例断言需要干净的 CIK")
    engine = create_db_engine(get_settings().database_url)
    repository = FilingRepository(engine)
    payload = load_fixture()
    facts = parse_companyfacts(payload)
    try:
        _cleanup()
        first = repository.upsert_facts(facts, payload_sha256(payload))
        second = repository.upsert_facts(facts, payload_sha256(payload))
        assert first == 4
        assert second == 0
        with TestClient(create_app()) as client:
            early = client.get(
                "/api/v1/sec/facts",
                params={"cik": "320193", "as_of": "2024-03-01"},
            )
            later = client.get(
                "/api/v1/sec/facts",
                params={"cik": "320193", "as_of": date(2024, 6, 1).isoformat()},
            )
        assert early.status_code == 200
        early_body = early.json()["data"]
        assert early_body["notice"] == "本地样例，不是本次从 SEC 拉取。"
        usd = [
            item
            for item in early_body["facts"]
            if item["unit"] == "USD" and item["period_end"] == "2023-12-31"
        ]
        assert usd[0]["value"] == "100"
        assert usd[0]["accession_no"] == "0000320193-24-000001"
        assert "data.sec.gov" in usd[0]["source_url"]
        amended = [
            item
            for item in later.json()["data"]["facts"]
            if item["unit"] == "USD" and item["period_end"] == "2023-12-31"
        ]
        assert amended[0]["value"] == "120"
        assert amended[0]["accession_no"] == "0000320193-24-000099"
    finally:
        _cleanup()
        engine.dispose()
