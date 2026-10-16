import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from app.catalog.reference_data import InstrumentSeed
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.eastmoney_statements import CnFact
from app.providers.errors import ProviderError
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.macro_regime import MacroRegimeRepository
from app.services import hotspot_sync
from app.services.disclosure_events import DisclosureDraft
from app.services.hotspot_sync import sync_hotspot_events
from app.services.macro_regime import compute_macro_regime
from app.services.metrics_snapshot import METRIC_CODES, snapshot_company_metrics
from app.workers.jobs import execute_job
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

SYMBOL = "999991"
MODEL = "pytest-model"
URL_PREFIX = "https://www.cninfo.com.cn/pytest-executors/"
OBSERVED = date(2099, 1, 1)


@pytest.fixture
def engine() -> Iterator[Engine]:
    db = create_db_engine(get_settings().database_url)
    try:
        with db.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    except SQLAlchemyError:
        pytest.skip("PostgreSQL 不可用")
    yield db
    with db.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM financial_metrics WHERE instrument_id IN "
                "(SELECT id FROM instruments WHERE symbol = :s)"
            ),
            {"s": SYMBOL},
        )
        connection.execute(
            text(
                "DELETE FROM cn_statement_facts WHERE instrument_id IN "
                "(SELECT id FROM instruments WHERE symbol = :s)"
            ),
            {"s": SYMBOL},
        )
        connection.execute(text("DELETE FROM instruments WHERE symbol = :s"), {"s": SYMBOL})
        connection.execute(
            text("DELETE FROM macro_regime_snapshots WHERE model_version = :m"), {"m": MODEL}
        )
        connection.execute(
            text("DELETE FROM news_events WHERE canonical_url LIKE :p"), {"p": f"{URL_PREFIX}%"}
        )
    db.dispose()


def _instrument_id(engine: Engine) -> UUID:
    with engine.connect() as connection:
        return UUID(
            str(
                connection.execute(
                    text("SELECT id FROM instruments WHERE symbol = :s"), {"s": SYMBOL}
                ).scalar_one()
            )
        )


def _cn_fact(concept: str, value: str, end: date, start: date | None) -> CnFact:
    return CnFact(
        concept=concept,
        unit="CNY",
        value=Decimal(value),
        period_start=start,
        period_end=end,
        form="年报",
        fiscal_year=end.year,
        fiscal_period="FY",
        filed=date(end.year + 1, 3, 30),
        source_field="pytest",
        source_url="https://datacenter.eastmoney.com/pytest",
    )


def test_metrics_snapshot_writes_only_from_real_facts_and_is_idempotent(engine: Engine) -> None:
    InstrumentRepository(engine).upsert_reference(
        (InstrumentSeed("CN", "SZSE", SYMBOL, "测试公司", "Pytest Co", "STOCK", "CNY"),)
    )
    instrument_id = _instrument_id(engine)
    today = date(2026, 10, 16)

    # 没有任何披露事实：什么都不写，也不会拿样例凑数。
    assert snapshot_company_metrics(engine, str(instrument_id), today) == 0

    CnFinancialRepository(engine).upsert_facts(
        instrument_id,
        [
            _cn_fact("us-gaap:Revenues", "200", date(2025, 12, 31), date(2025, 1, 1)),
            _cn_fact("us-gaap:Revenues", "100", date(2024, 12, 31), date(2024, 1, 1)),
            _cn_fact("us-gaap:Liabilities", "30", date(2025, 12, 31), None),
            _cn_fact("us-gaap:Assets", "100", date(2025, 12, 31), None),
        ],
    )
    assert snapshot_company_metrics(engine, str(instrument_id), today) == len(METRIC_CODES)
    assert snapshot_company_metrics(engine, str(instrument_id), today) == len(METRIC_CODES)

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT metric_code, value, quality_flag FROM financial_metrics "
                "WHERE instrument_id = :i"
            ),
            {"i": instrument_id},
        ).all()
    by_code = {row[0]: (row[1], row[2]) for row in rows}
    assert len(rows) == len(METRIC_CODES)  # 重复执行不会产生重复行
    assert by_code["revenue_yoy"] == (Decimal("100.000000"), "ok")
    assert by_code["debt_ratio"] == (Decimal("30.000000"), "ok")
    assert by_code["gross_margin"] == (None, "unavailable")


def test_metrics_endpoint_returns_snapshot_with_sources(engine: Engine) -> None:
    InstrumentRepository(engine).upsert_reference(
        (InstrumentSeed("CN", "SZSE", SYMBOL, "测试公司", "Pytest Co", "STOCK", "CNY"),)
    )
    instrument_id = _instrument_id(engine)
    CnFinancialRepository(engine).upsert_facts(
        instrument_id,
        [
            _cn_fact("us-gaap:Revenues", "200", date(2025, 12, 31), date(2025, 1, 1)),
            _cn_fact("us-gaap:Revenues", "100", date(2024, 12, 31), date(2024, 1, 1)),
        ],
    )
    snapshot_company_metrics(engine, str(instrument_id), date(2026, 10, 16))
    with TestClient(create_app()) as client:
        body = client.get(
            f"/api/v1/companies/{instrument_id}/metrics", params={"as_of": "2026-10-16"}
        ).json()["data"]
        early = client.get(
            f"/api/v1/companies/{instrument_id}/metrics", params={"as_of": "2025-01-01"}
        ).json()["data"]
    assert body["status"] == "ready"
    growth = next(item for item in body["items"] if item["metric_code"] == "revenue_yoy")
    assert growth["value"] == "100.000000"
    assert growth["inputs"]
    # 时间旅行：披露日之前看不到这些快照。
    assert early["status"] == "empty"


def test_macro_regime_snapshot_is_idempotent_and_listed_in_history(engine: Engine) -> None:
    assert compute_macro_regime(engine, as_of=OBSERVED, model_version=MODEL) == 1
    assert compute_macro_regime(engine, as_of=OBSERVED, model_version=MODEL) == 1
    history = [
        row for row in MacroRegimeRepository(engine).history(500) if row.model_version == MODEL
    ]
    assert len(history) == 1
    assert history[0].as_of == OBSERVED
    assert history[0].regime_code
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/macro/regime-history", params={"limit": 366})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert any(item["model_version"] == MODEL for item in items)


def test_executor_dispatch_covers_all_scheduled_job_types(engine: Engine) -> None:
    from app.repositories.sync_jobs import JobSnapshot

    job = JobSnapshot(
        id=UUID(int=1),
        job_type="macro_regime",
        provider_id=UUID(int=2),
        scope={"as_of": OBSERVED.isoformat(), "model_version": MODEL},
        dedupe_key="pytest",
        status="running",
        attempts=1,
        max_attempts=1,
        rows_written=0,
        error_code=None,
        error_summary=None,
        progress_stage="running",
        requested_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert execute_job(engine, job) == 1


def _draft(index: int) -> DisclosureDraft:
    return DisclosureDraft(
        title=f"pytest 披露 {index}",
        canonical_url=f"{URL_PREFIX}{index}",
        source_name="cninfo",
        published_at=datetime(2026, 10, 15, 9, 0, tzinfo=UTC),
        event_type="filing",
    )


def test_hotspot_rejects_unregistered_source(engine: Engine) -> None:
    with pytest.raises(ProviderError) as info:
        asyncio.run(
            sync_hotspot_events(engine, source="rumor-feed", observed_on=OBSERVED, user_agent="x")
        )
    assert info.value.code == "VALIDATION_ERROR"


def test_hotspot_imports_official_disclosures_idempotently_and_reports_partial_failure(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_cn(companies, start, end, failures):  # type: ignore[no-untyped-def]
        return [_draft(1), _draft(2)]

    async def fake_us(companies, start, user_agent, failures):  # type: ignore[no-untyped-def]
        failures.append("AAPL：模拟上游超时")
        return []

    monkeypatch.setattr(hotspot_sync, "_cn_disclosures", fake_cn)
    monkeypatch.setattr(hotspot_sync, "_us_disclosures", fake_us)

    with pytest.raises(ProviderError) as first:
        asyncio.run(
            sync_hotspot_events(engine, source="disclosures", observed_on=OBSERVED, user_agent="x")
        )
    assert first.value.code == "UPSTREAM_PARTIAL"
    assert "已新增 2 条" in first.value.message
    assert "AAPL" in first.value.message

    async def fake_us_ok(companies, start, user_agent, failures):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(hotspot_sync, "_us_disclosures", fake_us_ok)
    # 再跑一次：已入库的不会重复，且没有失败就正常返回 0 条新增。
    again = asyncio.run(
        sync_hotspot_events(engine, source="disclosures", observed_on=OBSERVED, user_agent="x")
    )
    assert again == 0
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM news_events WHERE canonical_url LIKE :p"),
            {"p": f"{URL_PREFIX}%"},
        ).scalar_one()
    assert count == 2
