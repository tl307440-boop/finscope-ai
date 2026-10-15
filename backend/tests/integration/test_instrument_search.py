from pathlib import Path

import pytest
from app.catalog.reference_data import reference_calendar, reference_instruments
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.instruments import InstrumentRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]


def _prepare() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        engine.dispose()
        return False
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    InstrumentRepository(engine).upsert_reference(reference_instruments())
    InstrumentRepository(engine).upsert_calendar(reference_calendar())
    engine.dispose()
    return True


@pytest.fixture
def client() -> TestClient:
    if not _prepare():
        pytest.skip("PostgreSQL 不可用")
    application = create_app()
    with TestClient(application) as test_client:
        yield test_client


def test_exact_code_returns_one_exchange(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "600519.SH"})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["unique"] is True
    assert body["results"][0]["canonical_code"] == "SSE:600519"
    assert body["results"][0]["instrument_type"] == "STOCK"


def test_name_search_returns_one_canonical_code(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "贵州茅台"})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["unique"] is True
    assert body["results"][0]["canonical_code"] == "SSE:600519"


def test_bare_code_stays_ambiguous_between_index_and_stock(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "000001"})
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["unique"] is False
    codes = {item["canonical_code"] for item in body["results"]}
    assert codes == {"SSE:000001", "SZSE:000001"}
    types = {item["canonical_code"]: item["instrument_type"] for item in body["results"]}
    assert types["SSE:000001"] == "INDEX"
    assert types["SZSE:000001"] == "STOCK"


def test_suffixed_index_code_is_unique(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "000001.SH"})
    body = response.json()["data"]
    assert body["unique"] is True
    assert body["results"][0]["canonical_code"] == "SSE:000001"


def test_cik_search_pads_and_returns_one_listing(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "320193"})
    body = response.json()["data"]
    assert body["unique"] is True
    assert body["results"][0]["canonical_code"] == "NASDAQ:AAPL"
    assert body["results"][0]["cik"] == "0000320193"


def test_missing_security_is_empty(client: TestClient) -> None:
    response = client.get("/api/v1/instruments/search", params={"q": "没有这家公司"})
    body = response.json()["data"]
    assert body["results"] == []
    assert body["empty_message"] == "暂无该证券"
    assert body["unique"] is False


def test_holiday_tuesday_is_closed_and_unknown_date_is_not_guessed(client: TestClient) -> None:
    holiday = client.get(
        "/api/v1/market/calendar",
        params={"exchange": "SSE", "session_date": "2024-10-01"},
    )
    holiday_body = holiday.json()["data"]
    assert holiday_body["status"] == "closed"
    assert holiday_body["weekday_inferred"] is False
    makeup = client.get(
        "/api/v1/market/calendar",
        params={"exchange": "SSE", "session_date": "2024-09-29"},
    )
    assert makeup.json()["data"]["status"] == "open"
    unknown = client.get(
        "/api/v1/market/calendar",
        params={"exchange": "SSE", "session_date": "2024-03-04"},
    )
    unknown_body = unknown.json()["data"]
    assert unknown_body["status"] == "unknown"
    assert unknown_body.get("is_open") is None
    assert unknown_body["weekday_inferred"] is False
    us_day = client.get(
        "/api/v1/market/calendar",
        params={"exchange": "NASDAQ", "session_date": "2024-10-01"},
    )
    assert us_day.json()["data"]["status"] == "unknown"
