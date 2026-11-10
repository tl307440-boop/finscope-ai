"""腾讯行情备用通道：东财 push2 / push2his 连不上时，页面仍能拿到交易日历、报价、分时、
日 K、成交额历史、涨幅中位数、冲高回落 / 探底回升与概念板块资金。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from app.providers import tencent_quote as tq
from app.providers.errors import ProviderError
from app.services import ashare_calc as calc
from app.services.ashare_panorama import ASharePanoramaService, _Acc
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI


def _quote_line(
    local: str,
    name: str,
    code: str,
    price: str,
    change: str,
    pct: str,
    amount_wan: str = "0",
    *,
    prev: str = "0",
    high: str = "0",
    low: str = "0",
    amplitude: str = "0",
) -> str:
    fields = ["0"] * 50
    fields[1], fields[2], fields[3], fields[4] = name, code, price, prev
    fields[31], fields[32], fields[33], fields[34] = change, pct, high, low
    fields[37], fields[38], fields[43], fields[44] = amount_wan, "1.5", amplitude, "100"
    return f'v_{local}="{"~".join(fields)}";'


def _gbk(*lines: str) -> bytes:
    return "\n".join(lines).encode("gbk")


def _kline_json(code: str, rows: list[tuple[str, str, str, str, str, str, str | None]]) -> bytes:
    """rows: (日期, 开, 收, 高, 低, 成交量, 成交额万元)；成交额为 None 时只给 6 列（前复权行）。"""

    day_rows: list[list[Any]] = []
    for day, o, c, h, low, vol, amount in rows:
        row: list[Any] = [day, o, c, h, low, vol]
        if amount is not None:
            row += [{}, "1.0", amount, "0.00", "0.00"]
        day_rows.append(row)
    key = "day" if any(r[6] is not None for r in rows) else "qfqday"
    return json.dumps({"code": 0, "data": {code: {key: day_rows}}}).encode()


def _minute_json(code: str, day: str, rows: list[str]) -> bytes:
    return json.dumps({"code": 0, "data": {code: {"data": {"date": day, "data": rows}}}}).encode()


def _five_json(code: str, days: list[tuple[str, list[str]]]) -> bytes:
    data = [{"date": day, "data": rows, "prec": "1"} for day, rows in days]
    return json.dumps({"code": 0, "data": {code: {"data": data}}}).encode()


def _rank_json(total: int, rows: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {"code": 0, "data": {"rank_list": rows, "offset": 0, "total": total}}
    ).encode()


_GOLD_LINE = (
    'v_hf_GC="4075.14,-0.93,4077.50,4077.90,4116.20,4044.50,16:21:08,'
    '4113.50,4098.50,0,2,1,2026-11-06,纽约黄金";'
)
_OIL_LINE = (
    'v_hf_CL="90.14,-0.42,90.15,90.17,92.53,90.08,16:21:06,90.52,91.20,0,5,4,2026-11-06,纽约原油";'
)

# ───────────── 解析 ─────────────


def test_tencent_code_mapping() -> None:
    assert tq.tencent_code("1.000001") == "sh000001"
    assert tq.tencent_code("0.399001") == "sz399001"
    assert tq.tencent_code("0.899050") == "bj899050"
    assert tq.tencent_code("0.920001") == "bj920001"
    assert tq.tencent_code("0.000001") == "sz000001"
    assert tq.tencent_code("100.HSI") == "hkHSI"
    assert tq.tencent_code("100.SPX") == "usINX"
    assert tq.tencent_code("101.GC00Y") == "hf_GC"
    assert tq.tencent_code("102.CL00Y") == "hf_CL"
    assert tq.tencent_code("116.00700") is None
    assert tq.a_share_code("600519") == "sh600519"
    assert tq.a_share_code("300750") == "sz300750"
    assert tq.a_share_code("920001") == "bj920001"


def test_parse_quotes_units_and_em_row_shape() -> None:
    payload = _gbk(
        _quote_line(
            "sh000001",
            "上证指数",
            "000001",
            "3649.18",
            "-40.73",
            "-1.10",
            "77263457",
            prev="3689.91",
            high="3660.49",
            low="3610.20",
            amplitude="1.36",
        ),
        _quote_line("hkHSI", "恒生指数", "HSI", "24420.83", "-376.15", "-1.52", "999"),
    )
    quotes = tq.parse_quotes(payload)
    sh = quotes["sh000001"]
    assert sh.amount_yuan == pytest.approx(77263457e4)  # 万元 → 元
    assert sh.float_cap_yuan == pytest.approx(100e8)  # 亿元 → 元
    assert (sh.prev_close, sh.high, sh.low, sh.amplitude_pct) == (3689.91, 3660.49, 3610.2, 1.36)
    assert quotes["hkHSI"].amount_yuan is None  # 港股指数的成交额口径不同，不取
    row = tq.as_em_row(sh, "1.000001")
    assert (row["f2"], row["f3"], row["f4"]) == (3649.18, -1.10, -40.73)
    assert (row["f12"], row["f13"], row["f14"]) == ("000001", 1, "上证指数")
    assert (row["f15"], row["f16"], row["f18"], row["f7"]) == (3660.49, 3610.2, 3689.91, 1.36)


def test_parse_futures_quotes() -> None:
    line = _GOLD_LINE
    gold = tq.parse_quotes(line.encode("gbk"))["hf_GC"]
    assert gold.price == 4075.14
    assert gold.change_pct == -0.93
    assert gold.change == pytest.approx(-38.36)  # 现价 − 昨结
    assert gold.name == "纽约黄金"
    assert gold.amount_yuan is None
    row = tq.as_em_row(gold, "101.GC00Y")
    assert (row["f12"], row["f13"]) == ("GC00Y", 101)


def test_parse_quotes_ignores_garbage_lines() -> None:
    assert tq.parse_quotes(b'v_pv_none_match="1";\nnot a quote\nv_sh000001="1~x~"') == {}


def test_parse_daily_amounts_and_adjusted_rows() -> None:
    payload = _kline_json(
        "sh000001",
        [
            ("2026-11-06", "3612.15", "3649.18", "3660.49", "3610.20", "477042561", "77263457"),
            ("2026-11-05", "3646.57", "3689.91", "3701.59", "3646.57", "571291638", "97971177.82"),
        ],
    )
    bars = tq.parse_daily(payload, "sh000001")
    assert [b.day for b in bars] == ["2026-11-05", "2026-11-06"]  # 升序
    assert bars[1].amount_yuan == pytest.approx(77263457e4)
    assert tq.parse_kline_days(payload, "sh000001") == ["2026-11-05", "2026-11-06"]
    adjusted = tq.parse_daily(
        _kline_json("sz000001", [("2026-11-06", "11.0", "11.8", "11.9", "10.9", "100", None)]),
        "sz000001",
    )
    assert adjusted[0].close == 11.8
    assert adjusted[0].amount_yuan is None  # 前复权行没有成交额，不倒推


def test_parse_minutes_hk_us_and_five_day() -> None:
    today = tq.parse_minutes(
        _minute_json(
            "sh000001",
            "20261106",
            [
                "0930 3612.15 9008737 13800185108.80",
                "1130 3640.00 300000000 500000000000.00",
                "1500 3649.18 477042561 772634566231.10",
            ],
        ),
        "sh000001",
    )
    assert today.day == "2026-11-06"
    assert today.total_amount == pytest.approx(772634566231.10)
    assert today.amount_until("11:30") == pytest.approx(500000000000.0)
    assert today.amount_until("09:00") is None
    us = tq.parse_minutes(
        _minute_json("usNDX.OQ", "20261105", ["0930 27996.35 0", "0931 27949.52 91578103"]), "usNDX"
    )
    assert len(us.bars) == 2 and us.total_amount is None  # 美股分时没有成交额
    five = tq.parse_five_day(
        _five_json(
            "bj899050",
            [("20261106", ["0930 871.47 100"]), ("20261105", ["0930 880.47 90"])],
        ),
        "bj899050",
    )
    assert [m.day for m in five] == ["2026-11-05", "2026-11-06"]
    assert five[-1].total_amount is None  # 北证 50 的五日分时只有累计量


def test_minute_urls_by_market() -> None:
    assert "hkMinute/query" in tq.minute_url("hkHSI")
    assert "UsMinute/query" in tq.minute_url("usNDX") and "usNDX.OQ" in tq.minute_url("usNDX")
    assert "usINX.N" in tq.minute_url("usINX")
    assert "minute/query" in tq.minute_url("sh000001")


def test_parse_rank_and_concept_rank() -> None:
    total, rows = tq.parse_rank(
        _rank_json(4606, [{"code": "sz300686", "name": "智动力", "zdf": "20.00", "zf": "26.14"}])
    )
    assert total == 4606
    assert (rows[0].code, rows[0].change_pct, rows[0].amplitude_pct) == ("sz300686", 20.0, 26.14)
    assert rows[0].net_inflow_wan is None
    _, concepts = tq.parse_rank(
        _rank_json(
            1, [{"code": "pt02031398", "name": "昨日连板", "zdf": "2.12", "zljlr": "-53851.58"}]
        )
    )
    assert concepts[0].net_inflow_wan == pytest.approx(-53851.58)


def test_bad_payload_is_a_provider_error() -> None:
    with pytest.raises(ProviderError):
        tq.parse_kline_days(b"<html>", "sh000001")
    with pytest.raises(ProviderError):
        tq.parse_minutes(json.dumps({"code": 0, "data": {"sh000001": {"data": {}}}}).encode(), "x")
    with pytest.raises(ProviderError):
        tq.parse_rank(b'{"code": 1}')


# ───────────── 服务层：东财 push2 / push2his 全部连接失败 ─────────────

Route = bytes | Callable[[str], bytes]


class _Gateway:
    """东财 push2 / push2his 一律连接失败；腾讯按地址片段（先配先匹配）返回预置字节。"""

    def __init__(self, routes: Mapping[str, Route]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        if "push2.eastmoney.com" in url or "push2his.eastmoney.com" in url:
            raise ProviderError(
                code="UPSTREAM_UNREACHABLE", message="上游连接中断", status_code=502
            )
        for marker, body in self.routes.items():
            if marker in url:
                return body(url) if callable(body) else body
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        return await self.get(url, headers)


def _service(routes: Mapping[str, Route]) -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(gateway=_Gateway(routes), cache=TtlCache(), now=lambda: now)


def _query(url: str, key: str) -> str:
    return parse_qs(urlsplit(url).query)[key][0]


def _calendar_kline(url: str) -> bytes:
    code = _query(url, "param").split(",")[0]
    return _kline_json(
        code,
        [
            ("2026-11-05", "1", "1", "1", "1", "1", "97971177.82"),
            ("2026-11-06", "1", "1", "1", "1", "1", "77263457"),
        ],
    )


def test_calendar_falls_back_to_tencent_when_eastmoney_is_unreachable() -> None:
    payload = asyncio.run(_service({"newfqkline": _calendar_kline}).ladder())
    assert payload.tradeDate == "2026-11-06"
    assert payload.meta.session == "close"
    assert "交易日历" not in payload.meta.unavailable
    assert "腾讯财经 日K" in payload.meta.sources


def _minute_for(url: str) -> bytes:
    code = _query(url, "code")
    rows = ["0930 100.0 1 1000", "1130 101.5 2 2000", "1500 102.0 3 3000"]
    if code.startswith("hf_"):
        raise ProviderError(code="UPSTREAM_PAYLOAD", message="商品期货没有分时", status_code=502)
    return _minute_json(code, "20261106", rows)


def _sina_minute(url: str) -> bytes:
    """新浪期货分时样本：200 个 1 分钟点，价格线性递增；量恒为 0。"""

    base, step = (4000.0, 0.5) if "symbol=GC" in url else (90.0, 0.01)
    first = f"{base:.3f}"
    rows = [
        [
            "2026-11-06",
            "4113.500",
            "cme",
            "",
            "07:00",
            first,
            "0",
            "0",
            first,
            "2026-11-06 07:00:00",
        ]
    ]
    for i in range(1, 200):
        minute = 7 * 60 + i
        clock = f"{minute // 60:02d}:{minute % 60:02d}"
        price = f"{base + i * step:.3f}"
        rows.append([clock, price, "0", "0", price, f"2026-11-06 {clock}:00"])
    body = json.dumps({"minLine_1d": rows})
    return f"/*<script>location.href='//sina.com';</script>*/\nvar t=({body});".encode()


def test_global_quotes_use_tencent_with_futures_and_sparklines() -> None:
    qt = _gbk(
        _quote_line("sh000001", "上证指数", "000001", "3649.18", "-40.73", "-1.10", "77263457"),
        _quote_line("hkHSI", "恒生指数", "HSI", "24420.83", "-376.15", "-1.52"),
        _GOLD_LINE,
        _OIL_LINE,
    )
    routes: dict[str, Route] = {
        "minute/query": _minute_for,
        "hkMinute/query": _minute_for,
        "UsMinute/query": _minute_for,
        "qt.gtimg.cn": qt,
    }
    routes["getGlobalFuturesMinLine"] = _sina_minute
    service = _service(routes)
    payload = asyncio.run(service.global_quotes())
    by_code = {q.code: q for q in payload.quotes}
    assert by_code["000001"].price == 3649.18
    assert by_code["000001"].amountYuan == pytest.approx(77263457e4)
    assert by_code["HSI"].price == 24420.83
    assert by_code["GC00Y"].price == 4075.14 and by_code["GC00Y"].changePct == -0.93
    assert by_code["CL00Y"].price == 90.14
    assert by_code["000001"].spark == [100.0, 101.5, 102.0]  # 腾讯分时
    assert by_code["HSI"].spark == [100.0, 101.5, 102.0]
    # 黄金 / 原油：新浪 1 分钟分时，降采样到 40 点，首尾保留。
    gold = by_code["GC00Y"].spark
    assert len(gold) == 40 and gold[0] == 4000.0 and gold[-1] == 4000.0 + 199 * 0.5
    oil = by_code["CL00Y"].spark
    assert len(oil) == 40 and oil[0] == 90.0
    assert "全球行情" not in payload.meta.unavailable
    assert "分时 上证指数" not in payload.meta.unavailable
    assert "分时 黄金" not in payload.meta.unavailable
    assert "分时 原油" not in payload.meta.unavailable
    assert "新浪财经 分时" in payload.meta.sources
    gateway = service.gateway
    assert isinstance(gateway, _Gateway)
    # 黄金 / 原油各只请求一次新浪分时，不再去腾讯要不存在的期货分时。
    assert sum("getGlobalFuturesMinLine" in url for url in gateway.calls) == 2
    assert not any("code=hf_" in url for url in gateway.calls)


def test_global_quotes_commodity_spark_degrades_when_sina_minute_fails() -> None:
    qt = _gbk(
        _quote_line("sh000001", "上证指数", "000001", "3649.18", "-40.73", "-1.10", "77263457"),
        _GOLD_LINE,
        _OIL_LINE,
    )

    def broken(url: str) -> bytes:
        if "symbol=CL" in url:
            return _sina_minute(url)
        return b"<html>blocked</html>"

    routes: dict[str, Route] = {
        "minute/query": _minute_for,
        "hkMinute/query": _minute_for,
        "UsMinute/query": _minute_for,
        "qt.gtimg.cn": qt,
        "getGlobalFuturesMinLine": broken,
    }
    payload = asyncio.run(_service(routes).global_quotes())
    by_code = {q.code: q for q in payload.quotes}
    assert by_code["GC00Y"].spark == [] and by_code["GC00Y"].price == 4075.14
    assert "新浪财经分时也取不到" in payload.meta.unavailable["分时 黄金"]
    assert len(by_code["CL00Y"].spark) == 40  # 原油不受黄金失败影响
    assert "分时 原油" not in payload.meta.unavailable
    assert by_code["000001"].spark == [100.0, 101.5, 102.0]  # 其他品种不受影响


def _volume_routes() -> dict[str, Route]:
    amounts = {
        "sh000001": ("97971177.82", "77263457"),
        "sz399001": ("113824132.23", "88515990"),
        "bj899050": ("1737251.95", "1400044"),
        "sz399102": ("57611002.88", "44106834.69"),
        "sh000680": ("34396775.15", "25781260.25"),
    }

    def kline(url: str) -> bytes:
        code = _query(url, "param").split(",")[0]
        prev, today = amounts[code]
        return _kline_json(
            code,
            [
                ("2026-11-05", "1", "1", "1", "1", "1", prev),
                ("2026-11-06", "1", "1", "1", "1", "1", today),
            ],
        )

    def five(url: str) -> bytes:
        code = _query(url, "code")
        if code.startswith("bj"):  # 北证 50：只有累计量，没有成交额
            days = [("20261105", ["1130 1 10"]), ("20261106", ["1130 1 20"])]
        else:
            days = [
                ("20261105", ["1130 1 10 400000000000.00", "1500 1 20 900000000000.00"]),
                ("20261106", ["1130 1 10 500000000000.00", "1500 1 20 700000000000.00"]),
            ]
        return _five_json(code, days)

    return {"newfqkline": kline, "day/query": five}


def test_volume_history_and_noon_comparison_from_tencent() -> None:
    payload = asyncio.run(_service(_volume_routes()).volume_margin())
    by_key = {v.key: v for v in payload.volumes}
    sh = by_key["sh"]
    assert sh.historyDates == ["11-05", "11-06"]  # 历史日成交额来自腾讯日 K，不止一个点
    assert sh.historyAmountYi == [
        pytest.approx(9797.12, abs=0.01),
        pytest.approx(7726.35, abs=0.01),
    ]
    assert sh.todayAmountYi == pytest.approx(7726.35, abs=0.01)
    assert sh.noonAmountYi == pytest.approx(5000.0)  # 今日 11:30 累计（元 → 亿）
    assert sh.noonDeltaYi == pytest.approx(1000.0)  # 对比上一交易日 11:30 的 4000 亿
    assert sh.noonDeltaPct == pytest.approx(25.0)
    # 北证 50：日 K 有成交额，分时没有 → 午盘对比为空；全市场午盘对比也因此为空
    assert by_key["bj"].todayAmountYi == pytest.approx(140.0, abs=0.01)
    assert by_key["bj"].noonAmountYi is None
    assert by_key["all"].todayAmountYi == pytest.approx(7726.35 + 8851.6 + 140.0, abs=0.05)
    assert by_key["all"].noonAmountYi is None
    assert any("腾讯" in note for note in payload.meta.notes)


def test_volume_without_kline_falls_back_to_quote_amount() -> None:
    quote = _gbk(
        _quote_line("bj899050", "北证50", "899050", "888.48", "-21.59", "-2.37", "1400044")
    )
    routes = {"newfqkline": _calendar_kline, "qt.gtimg.cn": quote}
    gateway = _Gateway(routes)

    async def broken_kline(url: str, headers: Mapping[str, str]) -> bytes:
        if "bj899050" in url and ("newfqkline" in url or "day/query" in url):
            raise ProviderError(code="UPSTREAM_PAYLOAD", message="无数据", status_code=502)
        return await _Gateway.get(gateway, url, headers)

    gateway.get = broken_kline  # type: ignore[method-assign]
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    service = ASharePanoramaService(gateway=gateway, cache=TtlCache(), now=lambda: now)
    by_key = {v.key: v for v in asyncio.run(service.volume_margin()).volumes}
    assert by_key["bj"].todayAmountYi == pytest.approx(140.0, abs=0.01)  # 报价里的当日成交额
    assert by_key["bj"].historyDates == ["11-06"]


def test_median_from_tencent_rank_middle() -> None:
    def rank(url: str) -> bytes:
        offset = int(_query(url, "offset"))
        pcts = ["9.0", "3.0", "1.0", "-1.0", "-3.0"]
        rows = (
            [] if offset >= 5 else [{"code": f"sz00000{offset}", "name": "x", "zdf": pcts[offset]}]
        )
        return _rank_json(5, rows)

    service = _service({"getBoardRankList": rank})
    acc = _Acc()
    assert asyncio.run(service._median_tencent(acc)) == 1.0  # 5 只取第 3 名
    assert any("腾讯" in note for note in acc.notes)


def test_swings_from_tencent_amplitude_candidates_and_quotes() -> None:
    def rank(url: str) -> bytes:
        rows = [
            {"code": "sz300001", "name": "冲高回落", "zdf": "8.00", "zf": "25.00"},
            {"code": "sz300002", "name": "探底回升", "zdf": "0.00", "zf": "12.00"},
            {"code": "sz300003", "name": "窄幅", "zdf": "1.00", "zf": "4.00"},
        ]
        return _rank_json(3, rows)

    qt = _gbk(
        # 昨收 10：最高 12（+20%）、收盘 10.8（+8%）→ 回落 12 个点
        _quote_line(
            "sz300001",
            "冲高回落",
            "300001",
            "10.8",
            "0.8",
            "8.00",
            prev="10",
            high="12",
            low="10.5",
            amplitude="15",
        ),
        # 昨收 10：最低 8.8（−12%）、收盘 10（0%）→ 回升 12 个点
        _quote_line(
            "sz300002",
            "探底回升",
            "300002",
            "10",
            "0",
            "0.00",
            prev="10",
            high="10.3",
            low="8.8",
            amplitude="15",
        ),
    )
    service = _service({"getBoardRankList": rank, "qt.gtimg.cn": qt})
    acc = _Acc()
    swings = asyncio.run(service._swings_tencent(acc))
    assert swings is not None
    assert [s.code for s in calc.pullbacks(swings)] == ["300001"]
    assert [s.code for s in calc.rebounds(swings)] == ["300002"]
    assert calc.pullbacks(swings)[0].peak_pct - calc.pullbacks(swings)[
        0
    ].change_pct == pytest.approx(12.0)
    assert all(s.code != "300003" for s in swings)  # 振幅 < 9% 的不进候选


def test_concept_flows_from_tencent_wan_to_yi() -> None:
    def concepts(url: str) -> bytes:
        rows = [
            {"code": "pt1", "name": "机器人概念", "zdf": "3.1", "zljlr": "123456.78"},
            {"code": "pt2", "name": "昨日连板", "zdf": "2.1", "zljlr": "-53851.58"},
        ]
        return _rank_json(2, rows)

    acc = _Acc()
    flows = asyncio.run(_service({"rank/pt/getRank": concepts})._concept_flows_tencent(acc))
    assert flows["机器人概念"] == pytest.approx(12.35)  # 万元 → 亿元
    assert flows["昨日连板"] == pytest.approx(-5.39)


def test_stock_detail_uses_tencent_quote_intraday_and_adjusted_daily() -> None:
    qt = _gbk(
        _quote_line(
            "sz000001", "平安银行", "000001", "11.81", "0.17", "1.46", "192731", prev="11.64"
        )
    )

    def kline(url: str) -> bytes:
        if "qfq" not in url:  # 交易日历（上证指数，不复权）
            return _calendar_kline(url)
        # 个股用前复权，涨跌幅才不会被除权日搞错
        return _kline_json(
            "sz000001",
            [
                ("2026-11-04", "10.0", "10.0", "10.2", "9.9", "100", None),
                ("2026-11-05", "10.0", "11.0", "11.1", "10.0", "200", None),
                ("2026-11-06", "11.0", "11.55", "11.6", "10.9", "300", None),
            ],
        )

    minute = _minute_json(
        "sz000001", "20261106", ["0930 11.60 10 11600000", "0931 11.70 30 35000000"]
    )
    routes: dict[str, Route] = {
        "newfqkline": kline,
        "minute/query": minute,
        "qt.gtimg.cn": qt,
    }
    payload = asyncio.run(_service(routes).stock_detail("000001"))
    assert payload.name == "平安银行"
    assert (payload.price, payload.preClose, payload.changePct) == (11.81, 11.64, 1.46)
    assert payload.floatCapYi == pytest.approx(100.0)
    assert [b.volume for b in payload.dailyBars] == [200, 300]  # 多取的第一根只用来算涨跌幅
    assert [b.changePct for b in payload.dailyBars] == [10.0, 5.0]
    first, second = payload.intraday
    assert first.volume == 10 and second.volume == 20  # 累计量差分
    assert first.avgPrice == pytest.approx(11600000 / (10 * 100))
    assert second.avgPrice == pytest.approx(35000000 / (30 * 100))
    assert payload.sectors == []  # 腾讯没有行业与概念，不倒推
    assert "个股详情" not in payload.meta.unavailable
    assert "个股分时" not in payload.meta.unavailable
    assert "个股日K" not in payload.meta.unavailable


def test_stock_detail_unknown_code_is_not_found_when_tencent_has_no_line() -> None:
    routes: dict[str, Route] = {
        "newfqkline": _calendar_kline,
        "qt.gtimg.cn": b'v_pv_none_match="1";',
    }
    with pytest.raises(ProviderError) as info:
        asyncio.run(_service(routes).stock_detail("000009"))
    assert info.value.status_code == 404
