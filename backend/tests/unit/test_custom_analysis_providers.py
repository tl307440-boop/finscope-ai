"""交易所期权统计、中证指数行情、东财估值截面 / GDP 的解析。载荷样例取自 2026-10 的真实响应。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from app.providers import csindex
from app.providers import eastmoney_valuation as valuation
from app.providers import exchange_options as options
from app.providers.errors import ProviderError

DAY = date(2026, 10, 16)
D = Decimal


def _sse_row(code: str, put: str, call: str, day: str = "2026-10-16") -> dict[str, str]:
    return {
        "SECURITY_CODE": code,
        "SECURITY_ABBR": "标的",
        "TRADE_DATE": day,
        "PUT_VOLUME": put,
        "CALL_VOLUME": call,
        "LEAVES_PUT_QTY": "539,435",
        "LEAVES_CALL_QTY": "885,490",
        "CP_RATE": "68.62",
        "TOTAL_VOLUME": "1,047,938",
    }


def _jsonp(rows: list[dict[str, str]]) -> bytes:
    return ("cb(" + json.dumps({"result": rows}, ensure_ascii=False) + ")").encode()


# ---------------------------------------------------------------- 上交所


def test_sse_url_uses_trade_date_parameter_that_the_server_honours() -> None:
    url = options.sse_url(DAY)
    assert "tradeDate=20261016" in url
    assert "sqlId=COMMON_SSE_ZQPZ_YSP_QQ_SJTJ_MRTJ_CX" in url


def test_parse_sse_strips_commas_and_keeps_reconciliation_fields() -> None:
    stats = options.parse_sse(_jsonp([_sse_row("510050", "426,473", "621,465")]), DAY)
    assert len(stats) == 1
    stat = stats[0]
    assert (stat.exchange, stat.underlying_code) == ("SSE", "510050")
    assert (stat.put_volume, stat.call_volume) == (426_473, 621_465)
    assert (stat.put_oi, stat.call_oi) == (539_435, 885_490)
    assert stat.reported_ratio == D("68.62")
    assert stat.reported_ratio_kind == "volume_pcr"
    assert stat.reported_total_volume == 1_047_938


def test_parse_sse_returns_nothing_when_upstream_silently_returns_another_day() -> None:
    payload = _jsonp([_sse_row("510050", "1", "1", day="2026-10-15")])
    assert options.parse_sse(payload, DAY) == ()


def test_parse_sse_handles_empty_result_on_non_trading_day() -> None:
    assert options.parse_sse(_jsonp([]), date(2026, 10, 17)) == ()


def test_parse_sse_rejects_garbage_and_missing_numbers() -> None:
    with pytest.raises(ProviderError):
        options.parse_sse(b"<html>blocked</html>", DAY)
    row = _sse_row("510050", "426,473", "621,465")
    del row["CALL_VOLUME"]
    with pytest.raises(ProviderError):
        options.parse_sse(_jsonp([row]), DAY)


# ---------------------------------------------------------------- 深交所


def _szse(rows: list[dict[str, str]], error: str | None = None) -> bytes:
    return json.dumps([{"metadata": {}, "data": rows, "error": error}]).encode()


def test_parse_szse_maps_columns_and_marks_ratio_as_open_interest() -> None:
    row = {
        "bddm": "159919",
        "bdmc": "沪深300ETF嘉实",
        "cjl": "161,380",
        "rccjl": "91,902",
        "rpcjl": "69,478",
        "rcrpccb": "53.55",
        "wpchyzs": "244,762",
        "wpcrchys": "159,399",
        "wpcrphys": "85,363",
    }
    stats = options.parse_szse(_szse([row]), DAY)
    assert len(stats) == 1
    stat = stats[0]
    assert (stat.exchange, stat.underlying_code) == ("SZSE", "159919")
    assert (stat.call_volume, stat.put_volume) == (91_902, 69_478)
    assert (stat.call_oi, stat.put_oi) == (159_399, 85_363)
    assert stat.reported_ratio == D("53.55")
    assert stat.reported_ratio_kind == "oi_pcr"
    assert stat.reported_total_volume == 161_380


def test_parse_szse_treats_error_or_empty_data_as_no_data() -> None:
    assert options.parse_szse(_szse([], error="日期不合法"), DAY) == ()
    assert options.parse_szse(b"[]", DAY) == ()


# ---------------------------------------------------------------- 中金所


_CFFEX_HEADER = (
    "合约代码,今开盘,最高价,最低价,成交量,成交金额,持仓量,持仓变化,"
    "今收盘,今结算,前结算,涨跌1,涨跌2,Delta"
)


def _cffex(lines: list[str]) -> bytes:
    return ("\n".join([_CFFEX_HEADER, *lines]) + "\n").encode("gbk")


def test_parse_cffex_sums_calls_and_puts_per_product_and_ignores_futures() -> None:
    payload = _cffex(
        [
            "MO2610-C-6000,1,1,1,100,1,40,0,1,1,1,0,0,--",
            "MO2610-C-6200,1,1,1,50,1,10,0,1,1,1,0,0,--",
            "MO2611-P-6000,1,1,1,30,1,20,0,1,1,1,0,0,--",
            "IO2610-C-4000,1,1,1,7,1,3,0,1,1,1,0,0,--",
            "IO2610-P-4000,1,1,1,9,1,5,0,1,1,1,0,0,--",
            "IC2610,1,1,1,999,1,999,0,1,1,1,0,0,--",
            "小计,,,,1000,,,,,,,,,",
        ]
    )
    stats = {stat.underlying_code: stat for stat in options.parse_cffex(payload, DAY)}
    assert set(stats) == {"MO", "IO"}
    mo = stats["MO"]
    assert (mo.call_volume, mo.put_volume, mo.call_oi, mo.put_oi) == (150, 30, 50, 20)
    io = stats["IO"]
    assert (io.call_volume, io.put_volume, io.call_oi, io.put_oi) == (7, 9, 3, 5)
    assert mo.reported_ratio is None


def test_parse_cffex_tolerates_padded_contract_codes_in_old_files() -> None:
    payload = _cffex(["IO2210-C-4000      ,1,1,1,7,1,3,0,1,1,1,0,0,--"])
    stats = options.parse_cffex(payload, DAY)
    assert [stat.underlying_code for stat in stats] == ["IO"]
    assert stats[0].put_volume == 0


def test_parse_cffex_rejects_unexpected_layout() -> None:
    with pytest.raises(ProviderError):
        options.parse_cffex("a,b,c\n1,2,3\n".encode("gbk"), DAY)
    with pytest.raises(ProviderError):
        options.parse_cffex(b"\xff\xfe\x00bad", DAY)


def test_cffex_url_layout() -> None:
    assert options.cffex_url(DAY) == (
        "http://www.cffex.com.cn/sj/hqsj/rtj/202610/16/20261016_1.csv"
    )


# ---------------------------------------------------------------- 中证指数


def _csindex(rows: list[dict[str, object]]) -> bytes:
    return json.dumps({"code": "200", "msg": "Success", "data": rows}).encode()


def test_parse_index_perf_reads_close_and_rolling_pe() -> None:
    payload = _csindex(
        [
            {"tradeDate": "20261016", "indexCode": "000300", "close": 4170.61, "peg": 12.88},
            {"tradeDate": "20261015", "indexCode": "000300", "close": 4166.01, "peg": 12.87},
        ]
    )
    days = csindex.parse_index_perf(payload, "000300")
    assert [day.trade_date for day in days] == [date(2026, 10, 15), date(2026, 10, 16)]
    assert days[-1].close == D("4170.61")
    assert days[-1].pe_ttm == D("12.88")


def test_parse_index_perf_rejects_another_index_instead_of_mislabeling_it() -> None:
    payload = _csindex([{"tradeDate": "20261016", "indexCode": "000905", "close": 1, "peg": 1}])
    with pytest.raises(ProviderError):
        csindex.parse_index_perf(payload, "000300")


def test_parse_index_perf_keeps_missing_pe_as_none() -> None:
    payload = _csindex([{"tradeDate": "20261016", "indexCode": "000300", "close": 1, "peg": None}])
    assert csindex.parse_index_perf(payload, "000300")[0].pe_ttm is None


# ---------------------------------------------------------------- 东财估值截面 / GDP


def _em(rows: list[dict[str, object]], pages: int = 1, count: int | None = None) -> bytes:
    return json.dumps(
        {"result": {"pages": pages, "count": len(rows) if count is None else count, "data": rows}}
    ).encode()


def test_parse_cross_section_reads_cap_and_pb() -> None:
    page = valuation.parse_cross_section(
        _em(
            [
                {
                    "SECURITY_CODE": "000001",
                    "SECURITY_NAME_ABBR": "平安银行",
                    "TOTAL_MARKET_CAP": 223168059277,
                    "PB_MRQ": 0.47663688,
                    "TRADE_DATE": "2026-10-16 00:00:00",
                },
                {
                    "SECURITY_CODE": "000002",
                    "SECURITY_NAME_ABBR": "万科A",
                    "TOTAL_MARKET_CAP": None,
                    "PB_MRQ": None,
                    "TRADE_DATE": "2026-10-16 00:00:00",
                },
            ],
            pages=12,
            count=5577,
        ),
        DAY,
    )
    assert (page.pages, page.count) == (12, 5577)
    assert page.rows[0].total_market_cap == D("223168059277")
    assert page.rows[0].pb_mrq == D("0.47663688")
    assert page.rows[1].pb_mrq is None


def test_parse_cross_section_rejects_page_from_another_day() -> None:
    payload = _em(
        [
            {
                "SECURITY_CODE": "000001",
                "SECURITY_NAME_ABBR": "平安银行",
                "TOTAL_MARKET_CAP": 1,
                "PB_MRQ": 1,
                "TRADE_DATE": "2026-10-15 00:00:00",
            }
        ]
    )
    with pytest.raises(ProviderError):
        valuation.parse_cross_section(payload, DAY)


def test_parse_cross_section_handles_null_result() -> None:
    page = valuation.parse_cross_section(json.dumps({"result": None}).encode(), DAY)
    assert page.rows == ()


def test_parse_listing_dates_skips_rows_without_a_date() -> None:
    found, pages = valuation.parse_listing_dates(
        _em(
            [
                {"SECURITY_CODE": "600519", "LISTING_DATE": "2001-08-27 00:00:00"},
                {"SECURITY_CODE": "900001", "LISTING_DATE": None},
            ],
            pages=50,
        )
    )
    assert found == {"600519": date(2001, 8, 27)}
    assert pages == 50


def test_parse_gdp_maps_report_month_to_quarter_end_and_keeps_cumulative_values() -> None:
    points = valuation.parse_gdp(
        _em(
            [
                {
                    "REPORT_DATE": "2026-06-01 00:00:00",
                    "TIME": "2026年第1-2季度",
                    "DOMESTICL_PRODUCT_BASE": 695704,
                },
                {
                    "REPORT_DATE": "2026-03-01 00:00:00",
                    "TIME": "2026年第1季度",
                    "DOMESTICL_PRODUCT_BASE": 334192.9,
                },
                {
                    "REPORT_DATE": "2026-05-01 00:00:00",
                    "TIME": "非季度月份",
                    "DOMESTICL_PRODUCT_BASE": 1,
                },
            ]
        )
    )
    assert [point.quarter_end for point in points] == [date(2026, 3, 31), date(2026, 6, 30)]
    assert points[1].cumulative_billion_cny == D("695704")
    assert points[0].label == "2026年第1季度"
