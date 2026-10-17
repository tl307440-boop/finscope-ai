from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.catalog.macro_series import fred_series_catalog
from app.core.config import Settings
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint
from app.schemas.macro import build_catalog
from app.services.macro_batch import build_observation_batch, limit_for, parse_codes, read_floor

OPEN = date(9999, 12, 31)


class FakeSource:
    def __init__(self, rows: dict[str, list[VintagePoint]]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []
        self.floors: list[Mapping[str, date] | None] = []

    def list_vintages_many(
        self,
        series_codes: Sequence[str],
        *,
        since: Mapping[str, date] | None = None,
    ) -> dict[str, list[VintagePoint]]:
        self.calls.append(list(series_codes))
        self.floors.append(since)
        return {code: self.rows.get(code, []) for code in series_codes}


def _point(observed: date, start: date, value: str) -> VintagePoint:
    return VintagePoint(
        observation_date=observed,
        value=Decimal(value),
        unit="Index",
        realtime_start=start,
        realtime_end=OPEN,
        revision_no=1,
    )


def test_parse_codes_dedupes_and_uppercases_in_order() -> None:
    assert parse_codes(" ism_mfg,UNRATE,,ISM_MFG ") == ["ISM_MFG", "UNRATE"]


def test_parse_codes_lists_unknown_codes() -> None:
    with pytest.raises(AppError) as caught:
        parse_codes("UNRATE,NOPE")
    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.status_code == 422
    assert caught.value.details == {"unknown_codes": ["NOPE"]}


@pytest.mark.parametrize("raw", [" , ", ",".join(f"X{index}" for index in range(21))])
def test_parse_codes_rejects_empty_or_too_many(raw: str) -> None:
    with pytest.raises(AppError) as caught:
        parse_codes(raw)
    assert caught.value.code == "VALIDATION_ERROR"
    assert "1–20" in caught.value.message


def test_limit_follows_frequency() -> None:
    assert limit_for("DGS10") == 2500
    assert limit_for("ICSA") == 600
    assert limit_for("UNRATE") == 240
    assert limit_for("GDPC1") == 240


def test_limits_are_configurable() -> None:
    settings = Settings(
        macro_batch_limit_daily=4000,
        macro_batch_limit_weekly=900,
        macro_batch_limit_other=480,
    )
    assert limit_for("DGS10", settings) == 4000
    assert limit_for("ICSA", settings) == 900
    assert limit_for("UNRATE", settings) == 480


@pytest.mark.parametrize(
    "overrides",
    [
        {"macro_batch_limit_daily": 0},
        {"macro_batch_limit_weekly": 20001},
        {"macro_sync_max_window_days": 364},
        {"macro_sync_max_window_days": 73001},
    ],
)
def test_limit_settings_reject_out_of_range(overrides: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Settings(**overrides)


def test_read_floor_covers_the_limit_for_every_frequency() -> None:
    pit = date(2026, 10, 17)
    settings = Settings()
    # 日度 2500 条按 1.6 天/条再加余量；周度、月度、季度同理，且下界不会晚于「limit 条」所需的跨度。
    assert read_floor("DGS10", pit, settings) == pit - timedelta(days=4030)
    assert read_floor("ICSA", pit, settings) == pit - timedelta(days=4830)
    assert read_floor("UNRATE", pit, settings) == pit - timedelta(days=8430)
    assert read_floor("GDPC1", pit, settings) == pit - timedelta(days=24030)


def test_read_floor_never_overflows() -> None:
    settings = Settings(macro_batch_limit_other=20000)
    assert read_floor("GDPC1", date(50, 1, 1), settings) == date.min


def test_batch_keeps_request_order_and_hides_release_day() -> None:
    source = FakeSource({"ISM_MFG": [_point(date(2026, 9, 1), date(2026, 10, 1), "54.5")]})
    release_day = build_observation_batch(source, ["UNRATE", "ISM_MFG"], date(2026, 10, 1))
    assert [item.series_code for item in release_day.items] == ["UNRATE", "ISM_MFG"]
    assert release_day.items[1].observations == []
    assert release_day.items[0].empty_message is not None
    next_day = build_observation_batch(source, ["ISM_MFG"], date(2026, 10, 2))
    assert [item.value for item in next_day.items[0].observations] == ["54.5"]
    assert next_day.pit_date == date(2026, 10, 2)
    assert source.calls == [["UNRATE", "ISM_MFG"], ["ISM_MFG"]]
    first_floors = source.floors[0]
    assert first_floors is not None
    assert set(first_floors) == {"UNRATE", "ISM_MFG"}
    assert first_floors["UNRATE"] == date(2026, 10, 1) - timedelta(days=8430)


def test_catalog_items_carry_provider_and_source_key() -> None:
    catalog = build_catalog(list(fred_series_catalog()), availability="public_csv")
    items = {item.series_code: item for item in catalog.series}
    assert (items["ISM_MFG"].provider, items["ISM_MFG"].source_key) == ("eastmoney", "EMG00002790")
    assert (items["DGS5"].provider, items["DGS5"].source_key) == ("fred", "")
