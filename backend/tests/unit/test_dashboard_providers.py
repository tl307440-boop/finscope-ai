"""驾驶舱新增数据源的 URL 与解析：腾讯、新浪、东财涨跌分布 / 数据中心、巨潮全市场。不联网。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from app.providers import eastmoney_rates as rates
from app.providers import eastmoney_sentiment as es
from app.providers import sina_market as sina
from app.providers import tencent_market as tx
from app.providers.disclosures import cninfo_market_form, parse_cninfo_market
from app.providers.errors import ProviderError


def _tencent_line(symbol: str, overrides: dict[int, str]) -> str:
    fields = ["-"] * 60
    for index, value in overrides.items():
        fields[index] = value
    return f'v_{symbol}="{"~".join(fields)}";'


def test_tencent_quote_parse_scales_amount_and_market_cap() -> None:
    line = _tencent_line(
        "sh000001",
        {
            1: "上证指数",
            3: "3716.28",
            4: "3707.48",
            5: "3723.80",
            30: "20261016161403",
            32: "0.24",
            33: "3750.77",
            34: "3681.28",
            37: "73247519",
            39: "",
            45: "580000.5",
        },
    )
    quotes = tx.parse_quotes((line + "\n" + 'v_whUSDCNY="1~x";').encode("gbk"))
    quote = quotes["sh000001"]
    assert quote.name == "上证指数" and quote.last == Decimal("3716.28")
    assert quote.amount == Decimal("732475190000")
    assert quote.market_cap == Decimal("58000050000000.0")
    assert quote.pe_ttm is None and quote.change_pct == Decimal("0.24")
    assert quote.quote_date == date(2026, 10, 16)
    assert "whUSDCNY" not in quotes


def test_tencent_fx_and_symbol_rules() -> None:
    payload = (
        'v_whUSDCNY="0~美元人民币~USDCNY~6.6860~6.6907~0~0~0~0~0~0~0~-0.0047~-0.07~2026-10-17";'
    )
    fx = tx.parse_fx(payload.encode("gbk"), "whUSDCNY")
    assert fx.last == Decimal("6.6860") and fx.change_pct == Decimal("-0.07")
    assert fx.quote_date == "2026-10-17"
    assert tx.a_share_symbol("600519") == "sh600519"
    assert tx.a_share_symbol("300750") == "sz300750"
    assert tx.a_share_symbol("830799") == "bj830799"
    with pytest.raises(ProviderError):
        tx.a_share_symbol("60051")
    with pytest.raises(ProviderError):
        tx.kline_url("000001", 10)


def test_tencent_klines_and_minutes() -> None:
    kline = {
        "code": 0,
        "data": {
            "sh000001": {
                "week": [
                    ["2026-10-16", "3806.98", "3716.28", "3828.22", "3674.82", "2373512791"]
                    + [{}, "4.88", "366357120.29"],
                    ["2026-10-09", "3838.98", "3813.79", "3864.12", "3754.14", "1017351606"] + [{}],
                ]
            }
        },
    }
    bars = tx.parse_klines(json.dumps(kline).encode(), "sh000001", "week")
    assert [bar.trade_date for bar in bars] == [date(2026, 10, 9), date(2026, 10, 16)]
    assert bars[0].amount is None
    assert bars[1].amount == Decimal("3663571202900.00")
    assert tx.parse_klines(json.dumps({"data": {}}).encode(), "sh000001") == ()
    lines = ["0930 2780.10 100 1000.5", "0931 2781.00 300 3000.5"]
    minute = {"data": {"sz399006": {"data": {"data": lines}}}}
    points = tx.parse_minutes(json.dumps(minute).encode(), "sz399006")
    assert [point.clock for point in points] == ["09:30", "09:31"]
    assert points[1].cumulative_amount == Decimal("3000.5")
    with pytest.raises(ProviderError):
        tx.parse_minutes(b"not json", "sz399006")


def test_eastmoney_fenbu_parse() -> None:
    buckets = [{"-11": 10}, {"0": 115}, {"1": 830}, {"11": 61}, {"x": 1}]
    payload = {"data": {"qdate": 20261016, "fenbu": buckets}}
    found = es.parse_fenbu(json.dumps(payload).encode())
    assert found.trade_date == date(2026, 10, 16)
    assert found.buckets == {-11: 10, 0: 115, 1: 830, 11: 61}
    assert es.parse_fenbu(b'{"data": null}').buckets == {}
    assert "getTopicZDFenBu" in es.fenbu_url()


def test_eastmoney_rates_parse_and_filters() -> None:
    query = parse_qs(urlparse(rates.shibor_url("1W")).query)
    assert '(INDICATOR_ID="101")' in query["filter"][0]
    assert query["source"] == ["WEB"]
    shibor = {
        "result": {
            "data": [
                {"REPORT_DATE": "2026-10-16 00:00:00", "IR_RATE": 1.264},
                {"REPORT_DATE": "2026-10-15 00:00:00", "IR_RATE": None},
                {"REPORT_DATE": "2026-10-14 00:00:00", "IR_RATE": 1.3},
            ]
        }
    }
    rows = rates.parse_dated(json.dumps(shibor).encode(), "IR_RATE")
    assert [row.day for row in rows] == [date(2026, 10, 14), date(2026, 10, 16)]
    treasury = {"result": {"data": [{"SOLAR_DATE": "2026-10-16 00:00:00", "EMM00166466": 1.648}]}}
    (row,) = rates.parse_treasury(json.dumps(treasury).encode())
    assert row.cn10y == Decimal("1.648") and row.us10y is None
    assert rates.parse_treasury(b'{"result": null}') == ()


def test_sina_boards_rank_and_hq() -> None:
    boards = (
        'var S_Finance_bankuai_sinaindustry = {"new_fjzz":"new_fjzz,飞机制造,13,25.1,1.0,4.2,100,'
        '2500000000,sh600893,10.01,40.1,3.6,航发动力","bad":"x,y"};'
    )
    (board,) = sina.parse_boards(boards.encode("gbk"))
    assert board.name == "飞机制造" and board.members == 13
    assert board.change_pct == Decimal("4.2") and board.leader_code == "600893"
    assert board.leader_name == "航发动力"
    with pytest.raises(ProviderError):
        sina.parse_boards(b"oops")
    rank = [{"code": "300001", "name": "N 新股", "trade": "10", "changepercent": 50, "amount": 1}]
    (stock,) = sina.parse_rank(json.dumps(rank).encode())
    assert stock.name == "N新股" and stock.change_pct == Decimal("50")
    hq = (
        'var hq_str_znb_NKY="日经225,65324.07,894.5,1.39,0,0,2026-10-16,15:15:00,x";\n'
        'var hq_str_hf_GC="4184.855,,0,0,0,0,05:59:59,4170.1,0,0,0,0,2026-10-17,黄金";'
    )
    quotes = sina.parse_hq(hq.encode("gbk"))
    assert quotes["znb_NKY"].prev_close == Decimal("64429.57")
    assert quotes["hf_GC"].prev_close == Decimal("4170.1")
    with pytest.raises(ProviderError):
        sina.rank_url("volume", 10)


def test_cninfo_market_form_and_parse() -> None:
    form = cninfo_market_form(
        start=date(2026, 10, 16), end=date(2026, 10, 17), page=2, category="category_fxts_szsh"
    )
    assert form["stock"] == "" and form["column"] == "szse" and form["pageSize"] == "30"
    assert form["category"] == "category_fxts_szsh;"
    assert form["seDate"] == "2026-10-16~2026-10-17"
    payload = {
        "announcements": [
            {
                "announcementId": "1224",
                "secCode": "600999",
                "secName": "招商<em>证券</em>",
                "announcementTitle": "关于<em>回购</em>股份的公告",
                "adjunctUrl": "finalpage/2026-10-17/1224.PDF",
                "announcementTime": 1792195200000,
            },
            {"secCode": "000001", "announcementTitle": "缺链接"},
        ]
    }
    (row,) = parse_cninfo_market(json.dumps(payload).encode())
    assert row.name == "招商证券" and row.title == "关于回购股份的公告"
    assert row.url == "https://static.cninfo.com.cn/finalpage/2026-10-17/1224.PDF"
    assert row.published_at.tzinfo is not None
    assert parse_cninfo_market(b'{"announcements": null}') == ()
