"""东财 secid 的解析、分类与展示名。"""

from __future__ import annotations

import pytest
from app.providers import eastmoney_boards as em
from app.utils.watch_secid import (
    a_share_secid,
    display_symbol,
    fund_market,
    kind_label,
    kind_of,
    kind_of_secid,
    parse_secid,
)


@pytest.mark.parametrize(
    ("secid", "kind"),
    [
        ("1.600519", "cn_stock"),
        ("0.000001", "cn_stock"),
        ("0.300308", "cn_stock"),
        ("0.920438", "cn_stock"),
        ("1.513120", "fund"),
        ("1.588200", "fund"),
        ("0.159516", "fund"),
        ("1.000001", "index"),
        ("0.399006", "index"),
        ("1.113050", "bond"),
        ("90.BK1630", "board"),
        ("100.NDX", "index"),
        ("124.HSTECH", "index"),
        ("251.SOX", "index"),
        ("105.NVDA", "us_stock"),
        ("106.TSM", "us_stock"),
        ("171.US10Y", "bond_yield"),
        ("101.GC00Y", "futures"),
        ("112.B00Y", "futures"),
        ("133.USDCNH", "fx"),
        ("116.00700", "hk_stock"),
        ("177.000660", "other"),
        ("999.XYZ", "other"),
    ],
)
def test_kind_of_secid(secid: str, kind: str) -> None:
    assert kind_of_secid(secid) == kind
    assert kind_label(kind)


def test_unparseable_secid_is_other() -> None:
    assert kind_of_secid("not-a-secid") == "other"
    assert kind_of(0, "ABC") == "other"


def test_parse_secid_normalises_case_and_rejects_garbage() -> None:
    assert parse_secid("105.nvda") == (105, "NVDA")
    assert parse_secid(" 90.BK1630 ") == (90, "BK1630")
    for bad in ("NVDA", "105.", ".NVDA", "1234.AAPL", "105.A B"):
        assert parse_secid(bad) is None


def test_display_symbol_uses_exchange_suffix_only_for_a_share_codes() -> None:
    assert display_symbol("1.513120") == "513120.SH"
    assert display_symbol("0.159516") == "159516.SZ"
    assert display_symbol("0.920438") == "920438.BJ"
    assert display_symbol("90.BK0473") == "BK0473"
    assert display_symbol("171.US10Y") == "US10Y"
    assert display_symbol("garbage") == "garbage"


def test_fund_market_infers_exchange_from_code_range() -> None:
    assert fund_market("513120") == 1
    assert fund_market("159516") == 0
    assert fund_market("110059") == 1
    assert fund_market("600519") is None
    assert fund_market("5131") is None


def test_a_share_secid_maps_exchange_to_market_number() -> None:
    assert a_share_secid("SSE", "600519") == "1.600519"
    assert a_share_secid("SZSE", "000001") == "0.000001"
    assert a_share_secid("BSE", "830799") == "0.830799"


def test_secid_quotes_keep_same_code_in_different_markets_apart() -> None:
    payload = (
        b'{"data":{"diff":['
        b'{"f12":"000660","f13":0,"f14":"\xe5\x8d\x8e\xe6\x9c\xac","f2":10.5,"f3":1.0},'
        b'{"f12":"000660","f13":177,"f14":"SK\xe6\xb5\xb7\xe5\x8a\x9b\xe5\xa3\xab","f2":1272000,"f3":0.79},'
        b'{"f12":"BK0473","f14":"\xe7\xbc\xba\xe5\xb8\x82\xe5\x9c\xba\xe7\xbc\x96\xe5\x8f\xb7","f2":1}'
        b"]}}"
    )
    quotes = em.parse_secid_quotes(payload)
    assert set(quotes) == {"0.000660", "177.000660"}
    assert quotes["177.000660"].name == "SK海力士"
