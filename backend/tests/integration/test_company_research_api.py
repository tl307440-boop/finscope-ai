import pytest
from app.catalog.reference_data import reference_instruments
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
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
def test_two_periods_show_sources_without_inventing_risks() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    matches = InstrumentRepository(engine).search(parse_search_key("AAPL"), market="US", limit=5)
    instrument_id = next(item.id for item in matches if item.symbol == "AAPL")
    engine.dispose()
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/v1/companies/{instrument_id}/research",
            params={"as_of": "2024-06-01", "period_a": "2022-12-31", "period_b": "2023-12-31"},
        )
        same = client.get(
            f"/api/v1/companies/{instrument_id}/research",
            params={"as_of": "2024-06-01", "period_a": "2023-12-31", "period_b": "2023-12-31"},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert "不会编造" in data["notice"]
    revenue = next(item for item in data["metrics"] if item["unit"] == "USD")
    assert revenue["earlier"]["value"] == "80"
    assert revenue["earlier"]["accession_no"] == "0000320193-23-000001"
    assert revenue["later"]["value"] == "120"
    assert revenue["later"]["accession_no"] == "0000320193-24-000099"
    assert "data.sec.gov" in revenue["later"]["source_url"]
    assert revenue["change"] == "0.5"
    assert data["highlights"] == []
    assert data["risks"] == []
    assert any(rule["rule_id"] == "R006" for rule in data["unevaluated"])
    assert same.status_code == 422
