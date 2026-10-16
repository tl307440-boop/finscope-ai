import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.catalog.macro_series import fred_series_catalog
from app.providers.errors import ProviderError
from app.providers.fred import FredAdapter, FredVintage, parse_observations
from app.providers.fred_public import fetch_public_csv, parse_graph_csv, public_realtime_start
from app.providers.http_client import OutboundRequest, RawResponse, ResilientHttpClient, Transport
from app.providers.vintage import VintagePoint, pit_date, select_visible, vintage_visible
from app.repositories.macro import MacroRepository
from app.schemas.macro import build_observations
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
    for seed in seeds.values():
        assert len(seed.name_en) <= 128
        assert len(seed.name_cn) <= 128
        assert len(seed.base_unit) <= 32
        assert len(seed.seasonal_adjustment) <= 32
        assert len(seed.frequency) <= 16


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
