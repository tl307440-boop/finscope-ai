import json
from datetime import date
from decimal import Decimal

import pytest
from app.providers.china_macro_sources import (
    EASTMONEY_SPECS,
    PUBLISH_RULES,
    parse_eastmoney,
    parse_mofcom,
    published_upper_bound,
)
from app.providers.errors import ProviderError
from app.services.china_macro import china_series_catalog

CPI = next(spec for spec in EASTMONEY_SPECS if spec.series_code == "CPI_CN")


def _eastmoney(rows: list[dict[str, object]]) -> bytes:
    return json.dumps({"success": True, "code": 0, "result": {"data": rows}}).encode()


def test_every_catalog_series_has_a_publish_rule_and_a_source() -> None:
    codes = {spec.series_code for spec in china_series_catalog()}
    assert codes == set(PUBLISH_RULES)
    covered = {spec.series_code for spec in EASTMONEY_SPECS} | {"SOCIAL_FINANCING"}
    assert covered == codes


def test_published_upper_bound_is_after_observation_month() -> None:
    assert published_upper_bound("CPI_CN", date(2026, 9, 1)) == date(2026, 10, 15)
    assert published_upper_bound("PMI_CN", date(2026, 9, 1)) == date(2026, 10, 1)
    assert published_upper_bound("M2_CN", date(2026, 12, 1)) == date(2027, 1, 20)
    assert published_upper_bound("SOCIAL_FINANCING", date(2026, 4, 1)) == date(2026, 5, 20)


def test_parse_eastmoney_reads_value_field_and_skips_bad_rows() -> None:
    payload = _eastmoney(
        [
            {"REPORT_DATE": "2026-09-01 00:00:00", "NATIONAL_SAME": 0.9, "CITY_SAME": 9.9},
            {"REPORT_DATE": "2026-08-01 00:00:00", "NATIONAL_SAME": None},
            {"REPORT_DATE": "garbage", "NATIONAL_SAME": 1.0},
            {"REPORT_DATE": "2026-07-01 00:00:00", "NATIONAL_SAME": True},
            {"REPORT_DATE": "2026-06-01 00:00:00", "NATIONAL_SAME": -0.1},
        ]
    )
    rows = parse_eastmoney(payload, CPI)
    assert [(row.observation_date, row.value) for row in rows] == [
        (date(2026, 9, 1), Decimal("0.9")),
        (date(2026, 6, 1), Decimal("-0.1")),
    ]
    assert rows[0].unit == "Percent"
    assert rows[0].published_at == date(2026, 10, 15)
    assert "国家统计局" in rows[0].source
    assert all(row.published_at >= row.observation_date for row in rows)


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps({"success": False}).encode(),
        json.dumps({"success": True, "result": None}).encode(),
        json.dumps({"success": True, "result": {"data": "x"}}).encode(),
    ],
)
def test_parse_eastmoney_rejects_bad_payloads(payload: bytes) -> None:
    with pytest.raises(ProviderError):
        parse_eastmoney(payload, CPI)


def test_parse_mofcom_uses_total_flow_in_yi_yuan() -> None:
    payload = json.dumps(
        [
            {"date": "202604", "tiosfs": 6245, "rmblaon": -4006},
            {"date": "202603", "tiosfs": 52240},
            {"date": "20260", "tiosfs": 1},
            {"date": "202613", "tiosfs": 1},
            {"date": "202602", "tiosfs": None},
        ]
    ).encode()
    rows = parse_mofcom(payload)
    assert [(row.observation_date, row.value) for row in rows] == [
        (date(2026, 4, 1), Decimal("6245")),
        (date(2026, 3, 1), Decimal("52240")),
    ]
    assert rows[0].unit == "亿元"
    assert rows[0].series_code == "SOCIAL_FINANCING"
    assert rows[0].published_at == date(2026, 5, 20)


def test_parse_mofcom_rejects_non_list() -> None:
    with pytest.raises(ProviderError):
        parse_mofcom(b'{"a": 1}')
    with pytest.raises(ProviderError):
        parse_mofcom(b"oops")
