from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.catalog.reference_data import reference_instruments
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.financial_metrics import FinancialMetricRepository
from app.repositories.instruments import InstrumentRepository
from app.utils.identifiers import parse_search_key
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


@pytest.mark.integration
def test_sample_quarter_api_and_metric_row() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    matches = InstrumentRepository(engine).search(parse_search_key("AAPL"), market="US", limit=5)
    instrument_id = next(item.id for item in matches if item.symbol == "AAPL")
    period_end = date(2023, 6, 30)
    try:
        with TestClient(create_app()) as client:
            response = client.post(
                "/api/v1/metrics/evaluations",
                json={
                    "statement": "income",
                    "q1": "80",
                    "q2": "200",
                    "market_cap": "100",
                    "profit": "-5",
                },
            )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["notice"] == "计算样例，不是公司真实财报。"
        quarters = {item["quarter"]: item for item in data["quarters"]}
        assert quarters["Q2"]["value"] == "120"
        assert data["pe_display"] == "N/A"
        assert "pe_value" not in data
        repository = FinancialMetricRepository(engine)
        repository.save(
            instrument_id=instrument_id,
            metric_code="revenue_q2_sample",
            period_type="Q",
            period_end=period_end,
            value=Decimal("120"),
            unit="CNY",
            quality_flag="ok",
            source_facts={"note": "sample"},
            published_at=datetime(2024, 8, 15, tzinfo=UTC),
        )
        assert repository.read_value(
            instrument_id=instrument_id,
            metric_code="revenue_q2_sample",
            period_type="Q",
            period_end=period_end,
        ) == Decimal("120.000000")
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    DELETE FROM financial_metrics
                    WHERE metric_code = 'revenue_q2_sample'
                    """
                )
            )
        engine.dispose()
