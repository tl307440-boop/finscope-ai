from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from app.catalog.macro_series import fred_series_catalog
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint
from app.schemas.macro import build_catalog
from app.services.macro_batch import build_observation_batch, limit_for, parse_codes

OPEN = date(9999, 12, 31)


class FakeSource:
    def __init__(self, rows: dict[str, list[VintagePoint]]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []

    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]:
        self.calls.append(list(series_codes))
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
    assert limit_for("DGS10") == 1000
    assert limit_for("ICSA") == 260
    assert limit_for("UNRATE") == 120
    assert limit_for("GDPC1") == 120


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


def test_catalog_items_carry_provider_and_source_key() -> None:
    catalog = build_catalog(list(fred_series_catalog()), availability="public_csv")
    items = {item.series_code: item for item in catalog.series}
    assert (items["ISM_MFG"].provider, items["ISM_MFG"].source_key) == ("eastmoney", "EMG00002790")
    assert (items["DGS5"].provider, items["DGS5"].source_key) == ("fred", "")
