import json
from datetime import date
from decimal import Decimal

import pytest
from app.providers.errors import ProviderError
from app.providers.nbs_macro import (
    MONTH_ROOT_ID,
    NBS_REQUESTS,
    QUARTER_ROOT_ID,
    NbsIndicator,
    NbsRequest,
    parse_es_data,
    request_body,
    request_codes,
)

CORE_ID = "71be3d43d2fb44188199840272463ae0"
CORE_NAME = "不包括食品和能源居民消费价格指数 (上年同月=100) "
PMI_REQUEST = next(
    request
    for request in NBS_REQUESTS
    if {item.series_code for item in request.indicators}
    == {"PMI_NEW_ORDERS_CN", "PMI_NEW_EXPORT_ORDERS_CN"}
)
CORE_REQUEST = NBS_REQUESTS[0]
GDP_REQUEST = next(request for request in NBS_REQUESTS if request.frequency == "quarterly")


def _payload(periods: list[dict[str, object]]) -> bytes:
    return json.dumps({"success": True, "state": 20000, "data": periods}).encode()


def _period(code: str, entries: list[tuple[str, str, str]]) -> dict[str, object]:
    return {
        "code": code,
        "name": code,
        "values": [
            {"_id": _id, "i_showname": name, "value": value} for _id, name, value in entries
        ],
    }


def test_core_cpi_index_is_converted_to_year_on_year_percent() -> None:
    payload = _payload(
        [
            _period("202610MM", [(CORE_ID, CORE_NAME, "")]),  # 尚未公布
            _period("202609MM", [(CORE_ID, CORE_NAME, "101.0")]),
            _period("202608MM", [(CORE_ID, CORE_NAME, "100.9")]),
        ]
    )
    rows = parse_es_data(payload, CORE_REQUEST)
    assert [(row.observation_date, row.value) for row in rows] == [
        (date(2026, 9, 1), Decimal("1.0")),
        (date(2026, 8, 1), Decimal("0.9")),
    ]
    assert rows[0].series_code == "CORE_CPI_CN"
    assert rows[0].unit == "Percent"
    assert rows[0].published_at == date(2026, 10, 15)
    assert all(row.published_at > row.observation_date for row in rows)


def test_one_request_can_carry_two_indicators_and_keeps_pmi_as_index_points() -> None:
    new_orders, export_orders = PMI_REQUEST.indicators
    payload = _payload(
        [
            _period(
                "202609MM",
                [
                    (new_orders.indicator_id, "新订单指数 (%) ", "50.5"),
                    (export_orders.indicator_id, "新出口订单指数 (%) ", "50.0"),
                ],
            )
        ]
    )
    rows = {row.series_code: row for row in parse_es_data(payload, PMI_REQUEST)}
    assert rows["PMI_NEW_ORDERS_CN"].value == Decimal("50.5")
    assert rows["PMI_NEW_EXPORT_ORDERS_CN"].value == Decimal("50.0")
    assert rows["PMI_NEW_ORDERS_CN"].unit == "Index"
    assert rows["PMI_NEW_ORDERS_CN"].published_at == date(2026, 10, 1)


def test_quarterly_gdp_uses_quarter_start_and_conservative_publish_bound() -> None:
    indicator = GDP_REQUEST.indicators[0]
    payload = _payload(
        [
            _period(
                "202602SS",
                [(indicator.indicator_id, "国内生产总值指数 (上年同期=100) 当季值", "104.3")],
            ),
            _period(
                "202601SS",
                [(indicator.indicator_id, "国内生产总值指数 (上年同期=100) 当季值", "105.0")],
            ),
        ]
    )
    rows = parse_es_data(payload, GDP_REQUEST)
    assert [(row.observation_date, row.value) for row in rows] == [
        (date(2026, 4, 1), Decimal("4.3")),
        (date(2026, 1, 1), Decimal("5.0")),
    ]
    assert rows[0].published_at == date(2026, 7, 20)


def test_unexpected_indicator_name_stops_the_write_instead_of_mislabeling() -> None:
    payload = _payload(
        [_period("202609MM", [(CORE_ID, "居民消费价格指数 (上年同月=100)", "100.4")])]
    )
    with pytest.raises(ProviderError) as info:
        parse_es_data(payload, CORE_REQUEST)
    assert info.value.code == "BAD_PAYLOAD"
    assert "CORE_CPI_CN" in info.value.message


def test_bad_period_codes_and_values_are_skipped() -> None:
    payload = _payload(
        [
            _period("garbage", [(CORE_ID, CORE_NAME, "101.0")]),
            _period("202613MM", [(CORE_ID, CORE_NAME, "101.0")]),
            _period("202609MM", [(CORE_ID, CORE_NAME, "n/a")]),
            _period("202608MM", [("other-id", CORE_NAME, "101.0")]),
            _period("202607MM", [(CORE_ID, CORE_NAME, "100.8")]),
        ]
    )
    rows = parse_es_data(payload, CORE_REQUEST)
    assert [row.observation_date for row in rows] == [date(2026, 7, 1)]


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>302</html>",
        json.dumps({"success": False, "data": []}).encode(),
        json.dumps({"success": True, "data": None}).encode(),
        json.dumps([1, 2]).encode(),
    ],
)
def test_bad_payloads_raise_provider_error(payload: bytes) -> None:
    with pytest.raises(ProviderError):
        parse_es_data(payload, CORE_REQUEST)


def test_request_body_covers_history_up_to_the_current_period() -> None:
    monthly = json.loads(request_body(CORE_REQUEST, date(2026, 10, 18)))
    assert monthly["rootId"] == MONTH_ROOT_ID
    assert monthly["dts"] == ["201801MM-202610MM"]
    assert monthly["indicatorIds"] == [CORE_ID]
    assert monthly["das"] == [{"text": "全国", "value": "000000000000"}]
    quarterly = json.loads(request_body(GDP_REQUEST, date(2026, 10, 18)))
    assert quarterly["rootId"] == QUARTER_ROOT_ID
    assert quarterly["dts"] == ["201801SS-202604SS"]


def test_every_nbs_series_is_requested_once_per_catalog_and_has_an_expectation() -> None:
    assert request_codes() == {
        "CORE_CPI_CN",
        "PMI_NEW_ORDERS_CN",
        "PMI_NEW_EXPORT_ORDERS_CN",
        "INDUS_PROFIT_CN",
        "PRIVATE_INVEST_CN",
        "MFG_INVEST_CN",
        "HOUSE_SALES_CN",
        "FISCAL_SPEND_CN",
        "GDP_CN",
    }
    for request in NBS_REQUESTS:
        assert isinstance(request, NbsRequest)
        for indicator in request.indicators:
            assert isinstance(indicator, NbsIndicator)
            assert indicator.expect
            assert len(indicator.indicator_id) == 32
