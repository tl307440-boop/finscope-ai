from datetime import date

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.instruments import InstrumentRepository
from app.utils.identifiers import parse_search_key
from fastapi.testclient import TestClient
from tests.integration.test_company_research_api import _ready


@pytest.mark.integration
def test_financials_only_use_filings_visible_on_as_of() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    matches = InstrumentRepository(engine).search(parse_search_key("AAPL"), market="US", limit=5)
    instrument_id = next(item.id for item in matches if item.symbol == "AAPL")
    engine.dispose()
    as_of = "2024-06-01"
    with TestClient(create_app()) as client:
        response = client.get(
            f"/api/v1/companies/{instrument_id}/financials", params={"as_of": as_of}
        )
        before = client.get(
            f"/api/v1/companies/{instrument_id}/financials", params={"as_of": "1990-01-01"}
        )
        missing = client.get(
            "/api/v1/companies/00000000-0000-0000-0000-000000000000/financials",
            params={"as_of": as_of},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["data_status"] in {"edgar", "sample"}
    assert date.fromisoformat(data["reference_end"]) <= date.fromisoformat(as_of)
    keys = [tile["key"] for tile in data["tiles"]]
    assert keys == [
        "revenue",
        "netIncome",
        "operatingCashFlow",
        "grossMargin",
        "epsDiluted",
        "debtRatio",
    ]
    for tile in data["tiles"]:
        source = tile.get("source")
        if source:
            assert source["filed"] <= as_of
    for row in data["cash_vs_profit"]:
        assert row["period_end"] <= as_of
    assert isinstance(data["rule_observations"], dict)

    assert before.status_code == 200
    empty = before.json()["data"]
    assert empty["data_status"] == "empty"
    assert empty["tiles"] == []
    assert empty["notice"]
    assert missing.status_code == 404
