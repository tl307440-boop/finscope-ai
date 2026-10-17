import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
from app.catalog.macro_series import catalog_codes, fred_codes, fred_series_catalog, seed_for
from app.providers.eastmoney_us_macro import LatestValue
from app.providers.errors import ProviderError
from app.providers.fred import (
    FredAdapter,
    FredVintage,
    merge_adjacent_vintages,
    parse_observations,
    vintage_windows,
)
from app.providers.fred_public import fetch_public_csv, parse_graph_csv, public_realtime_start
from app.providers.http_client import OutboundRequest, RawResponse, ResilientHttpClient, Transport
from app.providers.vintage import VintagePoint, pit_date, select_visible, vintage_visible
from app.repositories.macro import MacroRepository
from app.schemas.macro import FredSyncRequest, build_observations
from app.services.fred_sync import sync_fred_series


class ScriptTransport(Transport):
    def __init__(self, content: bytes, *, error: ProviderError | None = None) -> None:
        self.content = content
        self.error = error
        self.calls = 0

    async def send(self, request: OutboundRequest) -> RawResponse:
        self.calls += 1
        if self.error is not None:
            raise ProviderError(
                code=self.error.code,
                message=self.error.message.replace("{url}", request.url),
                status_code=self.error.status_code,
                attempts=self.error.attempts,
            )
        return RawResponse(status_code=200, headers={}, content=self.content)


class GuardRepository(MacroRepository):
    def __init__(self) -> None:
        self.writes = 0

    def upsert_vintages(
        self,
        series_code: str,
        vintages: tuple[FredVintage, ...] | list[FredVintage],
        *,
        fetched_at: datetime,
    ) -> int:
        self.writes += 1
        return 0


def test_catalog_keeps_verified_units_and_no_observations() -> None:
    seeds = {item.series_code: item for item in fred_series_catalog()}
    assert set(seeds) == {
        "GDP",
        "UNRATE",
        "PAYEMS",
        "CPIAUCSL",
        "PCEPILFE",
        "FEDFUNDS",
        "DGS2",
        "DGS10",
        "T10Y2Y",
        "CES0500000003",
        "GDPC1",
        "PCEC96",
        "DGS5",
        "DGS20",
        "DFEDTARL",
        "DFEDTARU",
        "ICSA",
        "ISM_MFG",
        "ISM_SVC",
    }
    assert seeds["GDP"].frequency == "Quarterly"
    assert seeds["GDP"].base_unit == "Billions of Dollars"
    assert seeds["GDP"].seasonal_adjustment == "Seasonally Adjusted Annual Rate"
    assert seeds["UNRATE"].frequency == "Monthly"
    assert seeds["UNRATE"].base_unit == "Percent"
    assert seeds["UNRATE"].seasonal_adjustment == "Seasonally Adjusted"
    assert seeds["PAYEMS"].base_unit == "Thousands of Persons"
    assert seeds["CPIAUCSL"].base_unit == "Index 1982-1984=100"
    assert seeds["PCEPILFE"].base_unit == "Index 2017=100"
    assert seeds["FEDFUNDS"].seasonal_adjustment == "Not Seasonally Adjusted"
    assert seeds["DGS10"].frequency == "Daily"
    assert seeds["T10Y2Y"].frequency == "Daily"
    assert seeds["CES0500000003"].base_unit == "Dollars per Hour"
    assert seeds["GDPC1"].base_unit == "Billions of Chained 2017 Dollars"
    assert seeds["GDPC1"].frequency == "Quarterly"
    assert seeds["PCEC96"].frequency == "Monthly"
    assert seeds["ICSA"].frequency == "Weekly"
    assert seeds["ICSA"].base_unit == "Number"
    assert seeds["DFEDTARL"].frequency == "Daily"
    assert seeds["ISM_MFG"].provider == "eastmoney"
    assert seeds["ISM_MFG"].source_key == "EMG00002790"
    assert seeds["ISM_SVC"].source_key == "EMG00002791"
    for seed in seeds.values():
        assert len(seed.name_en) <= 128
        assert len(seed.name_cn) <= 128
        assert len(seed.base_unit) <= 32
        assert len(seed.seasonal_adjustment) <= 32
        assert len(seed.frequency) <= 16
        assert len(seed.series_code) <= 32
        assert (seed.provider == "eastmoney") == bool(seed.source_key)


def test_fred_codes_exclude_eastmoney_series() -> None:
    assert "ISM_MFG" not in fred_codes()
    assert "ISM_SVC" not in fred_codes()
    assert "GDPC1" in fred_codes()
    assert {"ISM_MFG", "ISM_SVC"} <= catalog_codes()
    found = seed_for("ISM_SVC")
    assert found is not None and found.provider == "eastmoney"
    assert seed_for("NOT_A_SERIES") is None


def test_fred_adapters_refuse_eastmoney_series_before_any_request() -> None:
    transport = ScriptTransport(b"{}")
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def keyed() -> None:
        await FredAdapter(client, "k").fetch_vintages("ISM_MFG", date(2026, 1, 1), date(2026, 2, 1))

    async def public() -> None:
        await fetch_public_csv(
            client,
            "ISM_MFG",
            date(2026, 1, 1),
            date(2026, 2, 1),
            fetched_at=datetime(2026, 10, 16, 6, 0, tzinfo=UTC),
        )

    for action in (keyed, public):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(action())
        assert caught.value.code == "VALIDATION_ERROR"
    assert transport.calls == 0


def test_parser_skips_missing_marker_and_keeps_decimal() -> None:
    payload = json.dumps(
        {
            "observations": [
                {
                    "realtime_start": "2020-02-07",
                    "realtime_end": "2020-03-05",
                    "date": "1899-01-01",
                    "value": ".",
                },
                {
                    "realtime_start": "2020-02-07",
                    "realtime_end": "2020-03-05",
                    "date": "1899-02-01",
                    "value": "1.25",
                },
            ]
        }
    ).encode()
    parsed = parse_observations(payload)
    assert len(parsed) == 1
    assert parsed[0].value == Decimal("1.25")
    assert parsed[0].observation_date == date(1899, 2, 1)


def test_bad_payload_does_not_echo_the_body() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_observations(b"super-secret-fred")
    assert caught.value.code == "UPSTREAM_PAYLOAD"
    assert "super-secret-fred" not in caught.value.message


def test_observation_limit_keeps_most_recent_points() -> None:
    rows = [
        VintagePoint(
            observation_date=date(2020, 1, day),
            value=Decimal(day),
            unit="Percent",
            realtime_start=date(2020, 2, 1),
            realtime_end=date(9999, 12, 31),
            revision_no=1,
        )
        for day in range(1, 6)
    ]
    page = build_observations(series_code="DGS10", pit=date(2020, 3, 1), rows=rows, limit=2)
    assert [item.observation_date.day for item in page.observations] == [4, 5]
    assert page.truncated is True


def test_release_day_is_not_visible_and_later_revision_replaces_it() -> None:
    first = VintagePoint(
        observation_date=date(1899, 1, 1),
        value=Decimal("1.25"),
        unit="Percent",
        realtime_start=date(2020, 2, 7),
        realtime_end=date(2020, 3, 5),
        revision_no=1,
    )
    revised = VintagePoint(
        observation_date=date(1899, 1, 1),
        value=Decimal("9.75"),
        unit="Percent",
        realtime_start=date(2020, 3, 6),
        realtime_end=date(9999, 12, 31),
        revision_no=2,
    )
    assert (
        vintage_visible(
            realtime_start=first.realtime_start,
            realtime_end=first.realtime_end,
            pit=date(2020, 2, 7),
        )
        is False
    )
    early = select_visible([first, revised], date(2020, 2, 8))
    assert [item.value for item in early] == [Decimal("1.25")]
    assert select_visible([first, revised], date(2020, 3, 6)) == []
    later = select_visible([first, revised], date(2020, 3, 7))
    assert [item.value for item in later] == [Decimal("9.75")]
    assert pit_date(datetime(2020, 2, 8, 4, 30, tzinfo=UTC)) == date(2020, 2, 7)
    assert pit_date(datetime(2020, 2, 8, 5, 0, tzinfo=UTC)) == date(2020, 2, 8)


def test_missing_key_does_not_call_upstream() -> None:
    transport = ScriptTransport(b"{}")
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def run() -> None:
        await FredAdapter(client, "  ").fetch_vintages("UNRATE", date(2020, 1, 1), date(2020, 2, 1))

    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert caught.value.code == "DATA_UNAVAILABLE"
    assert transport.calls == 0


def test_adapter_redacts_the_key_from_upstream_errors() -> None:
    secret = "super-secret-fred"
    transport = ScriptTransport(
        b"",
        error=ProviderError(code="UPSTREAM_ERROR", message="failed {url}", status_code=502),
    )
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def run() -> None:
        await FredAdapter(client, secret).fetch_vintages(
            "UNRATE",
            date(2020, 1, 1),
            date(2020, 2, 1),
        )

    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert secret not in caught.value.message
    assert caught.value.message == "上游请求失败"


def test_sync_rejects_unknown_series_before_fetching() -> None:
    guard = GuardRepository()

    async def fetch(
        series_code: str,
        start: date,
        end: date,
    ) -> tuple[FredVintage, ...]:
        raise AssertionError("不应请求上游")

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            sync_fred_series(
                guard,
                series_code="NOT_A_SERIES",
                observation_start=date(2020, 1, 1),
                observation_end=date(2020, 2, 1),
                fetch=fetch,
                fetched_at=datetime(2020, 2, 8, tzinfo=UTC),
            )
        )
    assert caught.value.code == "VALIDATION_ERROR"
    assert guard.writes == 0


def test_public_csv_parses_values_and_skips_blanks() -> None:
    payload = b"observation_date,DGS10\n2026-10-09,5.24\n2026-10-12,\n2026-10-13,5.21\n"
    rows = parse_graph_csv(payload, series_id="DGS10", realtime_start=date(2026, 10, 15))
    assert [(row.observation_date, row.value) for row in rows] == [
        (date(2026, 10, 9), Decimal("5.24")),
        (date(2026, 10, 13), Decimal("5.21")),
    ]
    assert all(row.realtime_start == date(2026, 10, 15) for row in rows)
    assert all(row.realtime_end == date(9999, 12, 31) for row in rows)


def test_public_csv_rejects_a_different_series_header() -> None:
    with pytest.raises(ProviderError) as caught:
        parse_graph_csv(
            b"observation_date,UNRATE\n2026-01-01,4.3\n",
            series_id="DGS10",
            realtime_start=date(2026, 10, 15),
        )
    assert caught.value.code == "UPSTREAM_PAYLOAD"


def test_public_csv_never_backdates_past_the_new_york_quiet_hours() -> None:
    # 纽约 02:00 抓取：FRED 夜间不发布，CSV 里的数在前一纽约日已公开
    assert public_realtime_start(datetime(2026, 10, 16, 6, 0, tzinfo=UTC)) == date(2026, 10, 15)
    # 纽约 10:00 抓取：当天可能刚发布，记为当天，次日才可见
    assert public_realtime_start(datetime(2026, 10, 16, 14, 0, tzinfo=UTC)) == date(2026, 10, 16)


def test_public_csv_fetch_does_not_send_any_key() -> None:
    transport = ScriptTransport(b"observation_date,UNRATE\n2026-09-01,4.2\n")
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)
    rows = asyncio.run(
        fetch_public_csv(
            client,
            "UNRATE",
            date(2026, 1, 1),
            date(2026, 10, 1),
            fetched_at=datetime(2026, 10, 16, 6, 0, tzinfo=UTC),
        )
    )
    assert transport.calls == 1
    assert [row.value for row in rows] == [Decimal("4.2")]


async def _sleep(_: float) -> None:
    return None


def _page(value: str, observed: str) -> bytes:
    return json.dumps(
        {
            "observations": [
                {
                    "realtime_start": observed,
                    "realtime_end": "9999-12-31",
                    "date": observed,
                    "value": value,
                }
            ]
        }
    ).encode()


def test_vintage_windows_stay_within_2000_dates() -> None:
    start = date(2020, 10, 17)
    within = start + timedelta(days=1999)
    assert vintage_windows(start, within) == ((start, date(9999, 12, 31)),)

    over = start + timedelta(days=2000)
    first, second = vintage_windows(start, over)
    assert first == (start, start + timedelta(days=1999))
    assert second == (start + timedelta(days=2000), date(9999, 12, 31))

    # 政策利率从 2020-10-17 覆盖到 2026-10-17 有 2192 个自然日，必须切开。
    horizon = date(2026, 10, 17)
    windows = vintage_windows(start, horizon)
    assert len(windows) == 2
    cursor = start
    for realtime_start, realtime_end in windows:
        assert realtime_start == cursor
        end = horizon if realtime_end == date(9999, 12, 31) else realtime_end
        assert (end - realtime_start).days + 1 <= 2000
        cursor = end + timedelta(days=1)
    assert cursor == horizon + timedelta(days=1)
    assert "1776-07-04" not in str(windows)


def test_fetch_vintages_splits_the_realtime_window() -> None:
    class RecordingTransport(Transport):
        def __init__(self) -> None:
            self.urls: list[str] = []

        async def send(self, request: OutboundRequest) -> RawResponse:
            self.urls.append(request.url)
            observed = "2020-10-17" if len(self.urls) == 1 else "2026-10-17"
            content = _page(str(len(self.urls)), observed)
            return RawResponse(status_code=200, headers={}, content=content)

    transport = RecordingTransport()
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def run() -> tuple[FredVintage, ...]:
        return await FredAdapter(client, "k").fetch_vintages(
            "DFEDTARL",
            date(2020, 10, 17),
            date(2026, 10, 17),
            as_of=date(2026, 10, 17),
        )

    rows = asyncio.run(run())
    assert [row.value for row in rows] == [Decimal("1"), Decimal("2")]
    assert len(transport.urls) == 2
    queries = [parse_qs(urlsplit(url).query) for url in transport.urls]
    assert queries[0]["realtime_start"] == ["2020-10-17"]
    assert queries[0]["realtime_end"] == ["2026-04-08"]
    assert queries[1]["realtime_start"] == ["2026-04-09"]
    assert queries[1]["realtime_end"] == ["9999-12-31"]
    for query in queries:
        assert query["observation_start"] == ["2020-10-17"]
        assert query["observation_end"] == ["2026-10-17"]
        assert "1776-07-04" not in query["realtime_start"][0]


def _vintage(observed: date, value: str, start: date, end: date) -> FredVintage:
    return FredVintage(
        observation_date=observed,
        value=Decimal(value),
        realtime_start=start,
        realtime_end=end,
    )


def test_merge_joins_a_vintage_cut_by_the_window_boundary() -> None:
    observed = date(2024, 1, 1)
    cut = [
        _vintage(observed, "5.33", date(2020, 10, 17), date(2026, 4, 8)),
        _vintage(observed, "5.33", date(2026, 4, 9), date(9999, 12, 31)),
    ]
    assert merge_adjacent_vintages(cut) == (
        _vintage(observed, "5.33", date(2020, 10, 17), date(9999, 12, 31)),
    )


def test_merge_keeps_real_revisions_and_unrelated_rows() -> None:
    observed = date(2024, 1, 1)
    revised = [
        _vintage(observed, "5.33", date(2020, 10, 17), date(2026, 4, 8)),
        _vintage(observed, "5.50", date(2026, 4, 9), date(9999, 12, 31)),
    ]
    assert merge_adjacent_vintages(revised) == tuple(revised)

    gap = [
        _vintage(observed, "5.33", date(2020, 10, 17), date(2026, 4, 1)),
        _vintage(observed, "5.33", date(2026, 4, 9), date(9999, 12, 31)),
    ]
    assert merge_adjacent_vintages(gap) == tuple(gap)

    other_days = [
        _vintage(date(2024, 1, 1), "5.33", date(2020, 10, 17), date(2026, 4, 8)),
        _vintage(date(2024, 1, 2), "5.33", date(2026, 4, 9), date(9999, 12, 31)),
    ]
    assert merge_adjacent_vintages(other_days) == tuple(other_days)


def test_fetch_vintages_returns_one_row_for_a_value_spanning_the_split() -> None:
    class SpanningTransport(Transport):
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, request: OutboundRequest) -> RawResponse:
            self.calls += 1
            query = parse_qs(urlsplit(request.url).query)
            payload = {
                "observations": [
                    {
                        "realtime_start": query["realtime_start"][0],
                        "realtime_end": query["realtime_end"][0],
                        "date": "2024-01-01",
                        "value": "5.33",
                    }
                ]
            }
            return RawResponse(status_code=200, headers={}, content=json.dumps(payload).encode())

    transport = SpanningTransport()
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)
    rows = asyncio.run(
        FredAdapter(client, "k").fetch_vintages(
            "DFEDTARL",
            date(2020, 10, 17),
            date(2026, 10, 17),
            as_of=date(2026, 10, 17),
        )
    )
    assert transport.calls == 2
    assert len(rows) == 1
    assert rows[0].realtime_start == date(2020, 10, 17)
    assert rows[0].realtime_end == date(9999, 12, 31)


class LatestRepository(MacroRepository):
    def __init__(self) -> None:
        self.latest: list[tuple[str, list[LatestValue]]] = []

    def upsert_latest_values(
        self,
        series_code: str,
        points: Sequence[LatestValue],
        *,
        fetched_at: datetime,
    ) -> int:
        self.latest.append((series_code, list(points)))
        return len(points)


def test_ism_sync_goes_to_eastmoney_and_keeps_only_the_window() -> None:
    repository = LatestRepository()

    async def no_fred(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
        raise AssertionError("ISM 不应请求 FRED")

    async def latest(indicator_id: str) -> tuple[LatestValue, ...]:
        assert indicator_id == "EMG00002791"
        return (
            LatestValue(date(2026, 7, 1), date(2026, 8, 5), Decimal("54.1")),
            LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("54.9")),
        )

    written = asyncio.run(
        sync_fred_series(
            repository,
            series_code="ISM_SVC",
            observation_start=date(2026, 8, 1),
            observation_end=date(2026, 10, 17),
            fetch=no_fred,
            fetched_at=datetime(2026, 10, 17, 15, tzinfo=UTC),
            fetch_latest=latest,
        )
    )
    assert written == 1
    assert repository.latest == [
        ("ISM_SVC", [LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("54.9"))])
    ]


def test_sync_request_accepts_underscored_codes() -> None:
    body = FredSyncRequest(
        series_code="ISM_MFG",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 2, 1),
    )
    assert body.series_code == "ISM_MFG"


def test_scheduler_syncs_every_catalog_series_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    monkeypatch.delenv("SCHEDULER_FRED_SERIES", raising=False)
    settings = Settings(_env_file=None)
    assert set(settings.scheduler_series_codes()) == set(catalog_codes())


class StoredVintagesRepository(MacroRepository):
    def __init__(self, stored: list[VintagePoint]) -> None:
        self.stored = stored
        self.loads: list[tuple[str, date | None, date | None]] = []
        self.writes: list[tuple[str, list[FredVintage], datetime]] = []

    def list_vintages(
        self,
        series_code: str,
        *,
        start: date | None,
        end: date | None,
    ) -> list[VintagePoint] | None:
        self.loads.append((series_code, start, end))
        return self.stored

    def upsert_vintages(
        self,
        series_code: str,
        vintages: tuple[FredVintage, ...] | list[FredVintage],
        *,
        fetched_at: datetime,
    ) -> int:
        self.writes.append((series_code, list(vintages), fetched_at))
        return len(vintages)


def _stored_ism(value: str) -> VintagePoint:
    return VintagePoint(
        observation_date=date(2026, 9, 1),
        value=Decimal(value),
        unit="Index",
        realtime_start=date(2026, 10, 5),
        realtime_end=date(9999, 12, 31),
        revision_no=1,
    )


def test_latest_values_skip_unchanged_value_and_load_full_history() -> None:
    repository = StoredVintagesRepository([_stored_ism("54.900000")])
    written = repository.upsert_latest_values(
        "ISM_SVC",
        [LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("54.9"))],
        fetched_at=datetime(2026, 10, 18, 2, tzinfo=UTC),
    )
    assert written == 0
    assert repository.writes == []
    assert repository.loads == [("ISM_SVC", None, None)]


def test_latest_values_revision_uses_the_new_york_fetch_day() -> None:
    repository = StoredVintagesRepository([_stored_ism("54.900000")])
    fetched_at = datetime(2026, 10, 18, 2, tzinfo=UTC)
    written = repository.upsert_latest_values(
        "ISM_SVC",
        [LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("55.2"))],
        fetched_at=fetched_at,
    )
    assert written == 2
    assert repository.loads == [("ISM_SVC", None, None)]
    assert repository.writes == [
        (
            "ISM_SVC",
            [
                FredVintage(
                    observation_date=date(2026, 9, 1),
                    value=Decimal("54.900000"),
                    realtime_start=date(2026, 10, 5),
                    realtime_end=date(2026, 10, 17),
                ),
                FredVintage(
                    observation_date=date(2026, 9, 1),
                    value=Decimal("55.2"),
                    realtime_start=date(2026, 10, 17),
                    realtime_end=date(9999, 12, 31),
                ),
            ],
            fetched_at,
        )
    ]
