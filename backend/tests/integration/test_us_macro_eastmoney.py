import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.eastmoney_us_macro import LatestValue
from app.providers.fred import FredVintage
from app.repositories.macro import MacroRepository
from app.services.fred_sync import sync_fred_series
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

OBSERVED = date(1899, 1, 1)
OPEN = date(9999, 12, 31)


@pytest.fixture
def engine() -> Iterator[Engine]:
    created = create_db_engine(get_settings().database_url)
    try:
        with created.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        created.dispose()
        pytest.skip("PostgreSQL 不可用")
    _cleanup(created)
    try:
        yield created
    finally:
        _cleanup(created)
        created.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM macro_observations WHERE observation_date = :observed"),
            {"observed": OBSERVED},
        )


async def _no_fred(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
    raise AssertionError("ISM 不应请求 FRED")


def _sync(repository: MacroRepository, value: str, fetched_at: datetime) -> int:
    async def latest(indicator_id: str) -> tuple[LatestValue, ...]:
        assert indicator_id == "EMG00002790"
        return (LatestValue(OBSERVED, date(1899, 2, 1), Decimal(value)),)

    return asyncio.run(
        sync_fred_series(
            repository,
            series_code="ISM_MFG",
            observation_start=date(1898, 12, 1),
            observation_end=date(1899, 12, 31),
            fetch=_no_fred,
            fetched_at=fetched_at,
            fetch_latest=latest,
        )
    )


@pytest.mark.integration
def test_ism_revision_is_recorded_from_first_sync(engine: Engine) -> None:
    repository = MacroRepository(engine)
    assert _sync(repository, "50.3", datetime(2026, 10, 1, 15, tzinfo=UTC)) == 1
    assert _sync(repository, "50.3", datetime(2026, 10, 2, 15, tzinfo=UTC)) == 0
    assert _sync(repository, "49.8", datetime(2027, 1, 10, 15, tzinfo=UTC)) == 2
    rows = repository.list_vintages("ISM_MFG", start=OBSERVED, end=OBSERVED) or []
    rows.sort(key=lambda row: row.realtime_start)
    assert [(row.value, row.realtime_start, row.realtime_end) for row in rows] == [
        (Decimal("50.3"), date(1899, 2, 1), date(2027, 1, 10)),
        (Decimal("49.8"), date(2027, 1, 10), OPEN),
    ]
    seeds = {seed.series_code: seed for seed in repository.list_series()}
    assert seeds["ISM_MFG"].provider == "eastmoney"
    assert seeds["ISM_MFG"].source_key == "EMG00002790"


@pytest.mark.integration
def test_batch_endpoint_hides_ism_until_the_day_after_release(engine: Engine) -> None:
    MacroRepository(engine).upsert_latest_values(
        "ISM_MFG",
        [LatestValue(OBSERVED, date(1899, 2, 1), Decimal("50.3"))],
        fetched_at=datetime(2026, 10, 1, 15, tzinfo=UTC),
    )
    with TestClient(create_app()) as client:
        release = client.get(
            "/api/v1/macro/observations", params={"codes": "ISM_MFG,UNRATE", "as_of": "1899-02-01"}
        )
        after = client.get(
            "/api/v1/macro/observations", params={"codes": "ISM_MFG", "as_of": "1899-02-02"}
        )
        unknown = client.get("/api/v1/macro/observations", params={"codes": "ISM_MFG,NOPE"})
    assert release.status_code == 200
    items = release.json()["data"]["items"]
    assert [item["series_code"] for item in items] == ["ISM_MFG", "UNRATE"]
    assert items[0]["observations"] == []
    assert [row["value"] for row in after.json()["data"]["items"][0]["observations"]] == ["50.3"]
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"]["unknown_codes"] == ["NOPE"]
