import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.services import cockpit
from app.services.cockpit import DashboardService
from app.services.market_cache import PacedGateway, TtlCache

FETCHED = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


class FakeSnapshots:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[Any, datetime]] = {}

    def save(self, key: str, payload: Any, fetched_at: datetime) -> None:
        self.rows[key] = (payload, fetched_at)

    def load(self, prefix: str) -> dict[str, tuple[Any, datetime]]:
        return {key: row for key, row in self.rows.items() if key.startswith(prefix)}


def _service(snapshots: FakeSnapshots | None) -> DashboardService:
    service = DashboardService.__new__(DashboardService)
    service.cache = TtlCache()
    service.snapshots = snapshots  # type: ignore[assignment]
    service._restored = False
    service._inflight = {}
    service.now = lambda: FETCHED
    return service


def test_cache_seed_is_stale_and_never_overwrites() -> None:
    cache = TtlCache()
    cache.seed("k", 1, FETCHED)
    cache.seed("k", 2, FETCHED)
    entry = cache.peek("k")
    assert entry is not None
    assert (entry.value, entry.stale) == (1, True)
    assert cache.peek("missing") is None


def test_seeded_value_is_served_when_refresh_fails() -> None:
    cache = TtlCache()
    cache.seed("k", "old", FETCHED)

    async def failing() -> str:
        raise ProviderError(code="UPSTREAM_TIMEOUT", message="超时", status_code=504)

    cached = asyncio.run(cache.get("k", 60, failing))
    assert (cached.value, cached.stale, cached.fetched_at) == ("old", True, FETCHED)


def test_restore_snapshots_seeds_fred_cache_once() -> None:
    snapshots = FakeSnapshots()
    snapshots.rows["fred:UNRATE"] = ([["2026-09-01", "4.3"], ["2026-10-01", "4.4"]], FETCHED)
    snapshots.rows["fred:BAD"] = ([["not-a-date", "x"]], FETCHED)
    service = _service(snapshots)

    async def run() -> None:
        await service._restore_snapshots()
        await service._restore_snapshots()

    asyncio.run(run())

    entry = service.cache.peek("fred:UNRATE")
    assert entry is not None and entry.stale
    assert [(point.day, point.value) for point in entry.value] == [
        (date(2026, 9, 1), Decimal("4.3")),
        (date(2026, 10, 1), Decimal("4.4")),
    ]
    assert service.cache.peek("fred:BAD") is None


def test_save_snapshot_round_trips_through_restore() -> None:
    snapshots = FakeSnapshots()
    service = _service(snapshots)
    fresh = _service(snapshots)
    observations = (cockpit.rules.Observation(date(2026, 10, 1), Decimal("4.4")),)

    async def run() -> None:
        await service._save_snapshot("fred:UNRATE", observations)
        await fresh._restore_snapshots()

    asyncio.run(run())

    entry = fresh.cache.peek("fred:UNRATE")
    assert entry is not None and tuple(entry.value) == observations


def test_ingest_fred_saves_and_serves_fresh_value_without_upstream() -> None:
    snapshots = FakeSnapshots()
    service = _service(snapshots)
    series = {"UNRATE": [(date(2026, 9, 1), Decimal("4.4")), (date(2026, 8, 1), Decimal("4.3"))]}

    total = asyncio.run(service.ingest_fred(series))

    assert total == 2
    entry = service.cache.peek("fred:UNRATE")
    assert entry is not None and not entry.stale
    assert [point.day for point in entry.value] == [date(2026, 8, 1), date(2026, 9, 1)]
    assert snapshots.rows["fred:UNRATE"][0] == [["2026-08-01", "4.3"], ["2026-09-01", "4.4"]]


def test_ingest_fred_rejects_unknown_series_and_missing_database() -> None:
    with pytest.raises(AppError) as unknown:
        asyncio.run(
            _service(FakeSnapshots()).ingest_fred({"GDP": [(date(2026, 1, 1), Decimal(1))]})
        )
    assert unknown.value.status_code == 422

    with pytest.raises(AppError) as missing:
        asyncio.run(_service(None).ingest_fred({"UNRATE": [(date(2026, 1, 1), Decimal(1))]}))
    assert missing.value.status_code == 503


def test_paced_gateway_allows_configured_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    peak = 0

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    async def call(_: object) -> bytes:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return b"ok"

    monkeypatch.setattr("app.services.market_cache.httpx.AsyncClient", lambda **_: FakeClient())

    async def run() -> None:
        gateway = PacedGateway(min_interval=0, concurrency=3)
        await asyncio.gather(*(gateway._send(call) for _ in range(6)))

    asyncio.run(run())
    assert peak == 3
