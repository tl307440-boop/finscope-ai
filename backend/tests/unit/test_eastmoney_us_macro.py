import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.providers.eastmoney_us_macro import LatestValue, indicator_url, parse_indicator
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.providers.latest_revisions import OPEN_ENDED, plan_latest_revisions
from app.providers.vintage import VintagePoint, select_visible

MFG = "EMG00002790"


def _payload(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"success": True, "message": "ok", "result": {"count": len(rows), "data": rows}}
    ).encode()


def _row(report: str, publish: str, value: object, indicator: str = MFG) -> dict[str, object]:
    return {
        "INDICATOR_ID": indicator,
        "REPORT_DATE": f"{report} 00:00:00",
        "PUBLISH_DATE": f"{publish} 00:00:00",
        "VALUE": value,
    }


def test_url_filters_one_indicator_of_the_us_report() -> None:
    url = indicator_url(MFG)
    assert "reportName=RPT_ECONOMICVALUE_USA" in url
    assert "EMG00002790" in url
    assert "PUBLISH_DATE" in url


def test_parse_keeps_published_values_in_month_order() -> None:
    rows = parse_indicator(
        _payload(
            [
                _row("2026-09-01", "2026-10-01", 54.5),
                _row("2026-08-01", "2026-09-01", 54.6),
            ]
        ),
        MFG,
    )
    assert rows == (
        LatestValue(date(2026, 8, 1), date(2026, 9, 1), Decimal("54.6")),
        LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.5")),
    )


def test_parse_skips_calendar_placeholders_and_other_indicators() -> None:
    rows = parse_indicator(
        _payload(
            [
                _row("2026-10-01", "2026-11-02", None),
                _row("2026-09-01", "2026-10-05", 54.9, indicator="EMG00002791"),
                _row("2026-09-01", "2026-10-01", "54.5"),
                {"INDICATOR_ID": MFG, "REPORT_DATE": None, "PUBLISH_DATE": None, "VALUE": 1},
            ]
        ),
        MFG,
    )
    assert [row.value for row in rows] == [Decimal("54.5")]


def test_parse_keeps_the_latest_published_row_of_a_month() -> None:
    rows = parse_indicator(
        _payload(
            [
                _row("2026-09-01", "2026-10-01", 54.5),
                _row("2026-09-15", "2026-10-08", 54.2),
                _row("2026-09-01", "2026-09-30", 53.9),
            ]
        ),
        MFG,
    )
    assert rows == (LatestValue(date(2026, 9, 1), date(2026, 10, 8), Decimal("54.2")),)


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>blocked</html>",
        json.dumps({"success": False, "message": "返回数据为空", "result": None}).encode(),
        json.dumps({"success": True, "result": {"data": "oops"}}).encode(),
        json.dumps({"success": True, "result": {"data": None}}).encode(),
        _payload([]),
        _payload([_row("2026-10-01", "2026-11-02", None), _row("2026-11-01", "2026-12-01", None)]),
        _payload([{"INDICATOR": MFG, "REPORT": "2026-09-01", "PUBLISH": "2026-10-01", "V": 1}]),
    ],
)
def test_parse_rejects_bad_payloads_without_echoing_them(payload: bytes) -> None:
    with pytest.raises(ProviderError) as caught:
        parse_indicator(payload, MFG)
    assert caught.value.code == "UPSTREAM_PAYLOAD"
    assert "blocked" not in caught.value.message


def _held(value: str, start: date, end: date = OPEN_ENDED) -> VintagePoint:
    return VintagePoint(
        observation_date=date(2026, 9, 1),
        value=Decimal(value),
        unit="Index",
        realtime_start=start,
        realtime_end=end,
        revision_no=1,
    )


FRESH = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.5"))


def test_first_sight_opens_a_version_from_the_publish_day() -> None:
    assert plan_latest_revisions([], [FRESH], fetched_on=date(2026, 10, 17)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.5"),
            realtime_start=date(2026, 10, 1),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_same_value_writes_nothing() -> None:
    held = _held("54.500000", date(2026, 10, 1))
    assert plan_latest_revisions([held], [FRESH], fetched_on=date(2026, 10, 17)) == ()


def test_changed_value_closes_old_version_and_opens_new_one() -> None:
    held = _held("54.5", date(2026, 10, 1))
    revised = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.2"))
    assert plan_latest_revisions([held], [revised], fetched_on=date(2027, 1, 10)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.5"),
            realtime_start=date(2026, 10, 1),
            realtime_end=date(2027, 1, 10),
        ),
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.2"),
            realtime_start=date(2027, 1, 10),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_revision_stays_visible_every_day_around_the_fetch() -> None:
    held = _held("54.5", date(2026, 10, 1))
    revised = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.2"))
    fetched_on = date(2027, 1, 10)
    stored = {(held.observation_date, held.realtime_start): held}
    for row in plan_latest_revisions([held], [revised], fetched_on=fetched_on):
        stored[(row.observation_date, row.realtime_start)] = VintagePoint(
            observation_date=row.observation_date,
            value=row.value,
            unit="Index",
            realtime_start=row.realtime_start,
            realtime_end=row.realtime_end,
            revision_no=1,
        )
    seen = [
        [point.value for point in select_visible(list(stored.values()), fetched_on + shift)]
        for shift in (timedelta(days=-1), timedelta(0), timedelta(days=1))
    ]
    assert seen == [[Decimal("54.5")], [Decimal("54.5")], [Decimal("54.2")]]


def test_change_on_or_before_the_open_day_overwrites_in_place() -> None:
    held = _held("54.5", date(2026, 10, 1))
    revised = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.4"))
    assert plan_latest_revisions([held], [revised], fetched_on=date(2026, 10, 1)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.4"),
            realtime_start=date(2026, 10, 1),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_closed_versions_are_not_treated_as_current() -> None:
    closed = _held("53.0", date(2026, 10, 1), date(2026, 10, 9))
    current = _held("54.5", date(2026, 10, 10))
    assert plan_latest_revisions([closed, current], [FRESH], fetched_on=date(2026, 10, 17)) == ()
