from datetime import date
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.eastmoney_statements import CnFact
from app.providers.universe import UsListing
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.universe import UniverseRepository
from app.utils.identifiers import parse_search_key
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.integration.test_company_research_api import _ready

TEST_PERIOD_END = date(1999, 12, 31)


def _fact(value: str, filed: date) -> CnFact:
    return CnFact(
        concept="cn-gaap:Revenues",
        unit="CNY",
        value=Decimal(value),
        period_start=date(1999, 1, 1),
        period_end=TEST_PERIOD_END,
        form="年报",
        fiscal_year=1999,
        fiscal_period="FY",
        filed=filed,
        source_field="OPERATE_INCOME",
        source_url="https://example.invalid/test",
    )


@pytest.mark.integration
def test_universe_upsert_is_idempotent_and_never_overwrites() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    repository = UniverseRepository(engine)
    listing = UsListing(cik="0009999001", ticker="ZZTST1", name="Test Corp", exchange="NASDAQ")
    try:
        before = repository.count("US")
        assert repository.upsert_us([listing]) == 1
        assert repository.upsert_us([listing]) == 0
        assert repository.count("US") == before + 1
        renamed = UsListing(cik="0009999001", ticker="ZZTST1", name="Renamed", exchange="NASDAQ")
        repository.upsert_us([renamed])
        found = InstrumentRepository(engine).search(
            parse_search_key("ZZTST1"), market="US", limit=3
        )
        assert [item.name_en for item in found] == ["Test Corp"]
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM instruments WHERE cik = '0009999001'"))
        engine.dispose()


@pytest.mark.integration
def test_cn_statement_facts_respect_filing_date_and_are_idempotent() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    matches = InstrumentRepository(engine).search(parse_search_key("600519"), market="CN", limit=3)
    instrument_id = matches[0].id
    repository = CnFinancialRepository(engine)
    try:
        assert repository.upsert_facts(instrument_id, [_fact("100", date(2000, 3, 1))]) == 1
        assert repository.upsert_facts(instrument_id, [_fact("100", date(2000, 3, 1))]) == 0
        before = [
            fact
            for fact in repository.list_facts(instrument_id, date(2000, 2, 28))
            if fact.period_end == TEST_PERIOD_END
        ]
        assert before == []
        visible = [
            fact
            for fact in repository.list_facts(instrument_id, date(2000, 3, 1))
            if fact.period_end == TEST_PERIOD_END
        ]
        assert [(fact.value, fact.filing_status) for fact in visible] == [
            (Decimal("100"), "eastmoney")
        ]
        # 公司更正了数值：覆盖并保持唯一
        assert repository.upsert_facts(instrument_id, [_fact("120", date(2000, 3, 1))]) == 1
        corrected = [
            fact
            for fact in repository.list_facts(instrument_id, date(2000, 3, 1))
            if fact.period_end == TEST_PERIOD_END
        ]
        assert [fact.value for fact in corrected] == [Decimal("120")]
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM cn_statement_facts WHERE period_end = :end"),
                {"end": TEST_PERIOD_END},
            )
        engine.dispose()


@pytest.mark.integration
def test_data_status_is_public_and_requests_need_login() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    matches = InstrumentRepository(engine).search(parse_search_key("AAPL"), market="US", limit=5)
    instrument_id = next(item.id for item in matches if item.symbol == "AAPL")
    engine.dispose()
    with TestClient(create_app()) as client:
        status = client.get(f"/api/v1/companies/{instrument_id}/data-status")
        anonymous = client.post(f"/api/v1/companies/{instrument_id}/data-requests")
        unknown = client.get("/api/v1/companies/00000000-0000-0000-0000-000000000000/data-status")
        freshness = client.get("/api/v1/system/freshness")
    assert status.status_code == 200
    datasets = {row["dataset"]: row for row in status.json()["data"]["datasets"]}
    assert set(datasets) == {"financials", "bars"}
    assert datasets["financials"]["source"] == "SEC EDGAR"
    assert datasets["bars"]["state"] in {"fresh", "warn", "stale", "missing", "unavailable"}
    assert anonymous.status_code == 401
    assert unknown.status_code == 404
    assert freshness.status_code == 200
    body = freshness.json()["data"]
    assert {"process", "macro", "bars", "sync"} >= {item["group"] for item in body["items"]}
    assert isinstance(body["scheduler_running"], bool)
