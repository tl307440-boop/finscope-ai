"""全球指数 / 商品详情（分时 + 日K）：预置字节样本，不连网。

覆盖：东财优先、屏蔽 push2 / push2his 后回退腾讯（模拟生产服务器）、商品分时留空并写明原因、
未知代码 404。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime

import pytest
from app.api.v1.ashare_panorama import _panorama
from app.main import create_app
from app.providers import sina_market as sina
from app.providers import tencent_quote as tq
from app.providers.errors import ProviderError
from app.schemas.ashare_panorama import IndexDetailPayload
from app.services.ashare_panorama import ASharePanoramaService
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI
from fastapi.testclient import TestClient

# ───────────── 样本 ─────────────

EM_QUOTE = "push2.eastmoney.com/api/qt/ulist.np"
EM_TREND = "push2his.eastmoney.com/api/qt/stock/trends2"
EM_KLINE = "push2his.eastmoney.com/api/qt/stock/kline"
TX_QUOTE = "qt.gtimg.cn/q="
TX_MINUTE = "appstock/app/minute/query"
TX_HK_MINUTE = "appstock/app/hkMinute/query"
TX_US_MINUTE = "appstock/app/UsMinute/query"
TX_KLINE = "appstock/app/newfqkline/get"
SINA_DAILY = "GlobalFuturesService.getGlobalFuturesDailyKLine"
PUSH2 = (EM_QUOTE, EM_TREND, EM_KLINE)

SH_QUOTE_EM = {
    "data": {
        "diff": [
            {
                "f2": 3649.18,
                "f3": -1.1,
                "f4": -40.73,
                "f6": 772634566231.1,
                "f12": "000001",
                "f13": 1,
                "f14": "上证指数",
                "f15": 3660.49,
                "f16": 3610.2,
                "f17": 3612.15,
                "f18": 3689.91,
            }
        ]
    }
}
SH_TREND_EM = {
    "data": {
        "name": "上证指数",
        "preClose": 3689.91,
        "trends": [
            "2026-11-06 09:30,3612.15,3612.15,3612.15,3612.15,9008737,13800185088.00,0.000",
            "2026-11-06 09:31,3610.75,3623.32,3624.02,3610.20,25228552,38893627648.00,3611.314",
            "2026-11-06 15:00,3649.18,3649.18,3649.18,3649.18,6300270,9996000000.00,3624.99",
        ],
    }
}
SH_KLINE_EM = {
    "data": {
        "klines": [
            "2026-11-04,3605.39,3686.17,3686.17,3590.80,599394361,953265155867.10,2.64,2.16,78.07,1.23",
            "2026-11-05,3646.57,3689.91,3701.59,3646.57,571291638,979711778222.00,1.49,0.10,3.74,1.17",
            "2026-11-06,3612.15,3649.18,3660.49,3610.20,477042561,772634566231.00,1.36,-1.10,-40.73,0.98",
        ]
    }
}
SH_KLINE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "sh000001": {
            "day": [
                ["2026-11-04", "3605.39", "3686.17", "3686.17", "3590.80", "599394361.00", {}],
                ["2026-11-05", "3646.57", "3689.91", "3701.59", "3646.57", "571291638.00", {}],
                ["2026-11-06", "3612.15", "3649.18", "3660.49", "3610.20", "477042561.00", {}],
            ]
        }
    },
}
SH_MINUTE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "sh000001": {
            "data": {
                "date": "20261106",
                "data": [
                    "0930 3612.15 9008737 13800185108.80",
                    "0931 3623.32 34237289 52693812837.10",
                    "1500 3649.18 477042561 772634566231.10",
                    "1530 3649.18 477042561 772634566231.10",
                ],
            }
        }
    },
}
HK_MINUTE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "hkHSI": {
            "data": {
                "date": "20261106",
                "data": [
                    "0930 24404.620 428095 4280946995.958",
                    "0931 24462.940 1151420 11514195449.458",
                    "1559 24495.170 23763485 237634851606.411",
                    "1805 24481.860 24742771 247427708160.001",
                ],
            }
        }
    },
}
HK_KLINE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "hkHSI": {
            "day": [
                ["2026-11-04", "23451.79", "23919.50", "23919.50", "23385.29", "15802796288.00"],
                ["2026-11-05", "24316.94", "24796.98", "24871.92", "24281.77", "17585055288.00"],
                ["2026-11-06", "24404.62", "24481.86", "24740.18", "24349.30", "24742771000.00"],
            ]
        }
    },
}
US_MINUTE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "usIXIC.OQ": {
            "data": {
                "date": "20261105",
                "data": [
                    "0930 25493.43 0",
                    "0931 25493.43 836293878",
                    "0932 25453.94 891086409",
                    "1600 24315.00 10902980864",
                ],
            }
        }
    },
}
US_KLINE_TX = {
    "code": 0,
    "msg": "",
    "data": {
        "usIXIC": {
            "day": [
                ["2026-11-03", "25246.64", "25372.01", "25474.46", "25246.64", "7511455159.00"],
                ["2026-11-04", "25652.78", "25736.51", "26048.83", "25602.14", "8599012158.00"],
                ["2026-11-05", "25558.11", "24315.00", "25559.03", "24262.14", "10902980864.00"],
            ]
        }
    },
}
SINA_GC = (
    "/*<script>location.href='//sina.com';</script>*/\n"
    'var t=([{"date":"2026-11-04","open":"3940.800","high":"4081.200","low":"3928.200",'
    '"close":"4072.000","volume":"0","position":"0","s":"0.000","settlement":"0"},'
    '{"date":"2026-11-05","open":"4072.000","high":"4155.800","low":"4067.000",'
    '"close":"4097.800","volume":"0","position":"0","s":"0.000","settlement":"0"},'
    '{"date":"2026-11-06","open":"4098.500","high":"4116.200","low":"4044.500",'
    '"close":"4081.400","volume":"0","position":"0","s":"0.000","settlement":"0"}]);'
)


def _qt(local: str, name: str, code: str, **values: str) -> bytes:
    """腾讯报价行（A 股 / 港美指数格式）。

    字段：3 现价、4 昨收、5 今开、31 涨跌、32 涨幅、33 高、34 低、37 成交额（万元）。
    """

    fields = ["0"] * 50
    fields[:3] = ["1", name, code]
    positions = {
        "price": 3,
        "prev": 4,
        "open": 5,
        "change": 31,
        "pct": 32,
        "high": 33,
        "low": 34,
        "amount": 37,
    }
    for key, value in values.items():
        fields[positions[key]] = value
    return f'v_{local}="{"~".join(fields)}";\n'.encode("gbk")


SH_QUOTE_TX = _qt(
    "sh000001",
    "上证指数",
    "000001",
    price="3649.18",
    prev="3689.91",
    open="3612.15",
    change="-40.73",
    pct="-1.10",
    high="3660.49",
    low="3610.20",
    amount="77263457",
)
HK_QUOTE_TX = _qt(
    "hkHSI",
    "恒生指数",
    "HSI",
    price="24481.860",
    prev="24796.980",
    open="24404.620",
    change="-315.120",
    pct="-1.27",
    high="24740.180",
    low="24349.300",
)
US_QUOTE_TX = _qt(
    "usIXIC",
    "纳斯达克",
    ".IXIC",
    price="24315.00",
    prev="25736.51",
    open="25558.11",
    change="-1421.51",
    pct="-5.52",
    high="25559.03",
    low="24262.14",
)
GOLD_QUOTE_TX = (
    'v_hf_GC="4075.29,-0.93,4079.00,4079.20,4116.20,4044.50,18:05:01,4113.50,4098.50,0,2,1,'
    '2026-11-06,COMEX黄金";\n'
).encode("gbk")


class _Gateway:
    """按 URL 片段返回预置字节；`fail` 里的片段模拟上游不可达。"""

    def __init__(self, routes: Mapping[str, object], fail: tuple[str, ...] = ()) -> None:
        self.routes = routes
        self.fail = fail
        self.calls: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        for marker in self.fail:
            if marker in url:
                raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
        for marker, body in self.routes.items():
            if marker in url:
                if isinstance(body, bytes):
                    return body
                if isinstance(body, str):
                    return body.encode()
                return json.dumps(body).encode()
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        return await self.get(url, headers)


def _service(
    gateway: _Gateway, now: datetime | None = None
) -> tuple[ASharePanoramaService, _Gateway]:
    moment = now or datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(gateway=gateway, cache=TtlCache(), now=lambda: moment), gateway


def _detail(
    code: str, routes: Mapping[str, object], fail: tuple[str, ...] = (), now: datetime | None = None
) -> IndexDetailPayload:
    service, _ = _service(_Gateway(routes, fail), now)
    return asyncio.run(service.index_detail(code))


# ───────────── A 股指数 ─────────────


def test_a_share_index_uses_eastmoney_first() -> None:
    payload = _detail(
        "000001.SH",
        {EM_QUOTE: SH_QUOTE_EM, EM_TREND: SH_TREND_EM, EM_KLINE: SH_KLINE_EM},
    )
    assert (payload.code, payload.name, payload.category) == ("000001.SH", "上证指数", "A股宽基")
    assert (payload.price, payload.change, payload.changePct) == (3649.18, -40.73, -1.1)
    assert (payload.open, payload.high, payload.low, payload.preClose) == (
        3612.15,
        3660.49,
        3610.2,
        3689.91,
    )
    assert payload.amountYuan == pytest.approx(772634566231.1)
    assert [p.time for p in payload.intraday] == ["09:30", "09:31", "15:00"]
    # 均价取 0 是东财「开盘第一分钟没有均价」，不能当成 0 画出来。
    assert payload.intraday[0].avgPrice is None
    assert payload.intraday[1].avgPrice == 3611.314
    assert payload.intraday[1].volume == 25228552
    assert [b.date for b in payload.dailyBars] == ["2026-11-04", "2026-11-05", "2026-11-06"]
    assert payload.dailyBars[-1].changePct == -1.1
    assert payload.dailyBars[-1].volume == 477042561
    assert payload.meta.sources == ["东方财富 行情", "东方财富 分时", "东方财富 日K"]
    assert payload.meta.unavailable == {}
    assert (payload.meta.tradeDate, payload.meta.session) == ("2026-11-06", "close")


def test_a_share_index_falls_back_to_tencent_when_push2_blocked() -> None:
    routes = {
        TX_QUOTE: SH_QUOTE_TX,
        TX_MINUTE: SH_MINUTE_TX,
        TX_KLINE: SH_KLINE_TX,
    }
    payload = _detail("000001.SH", routes, fail=PUSH2)
    assert payload.price == 3649.18 and payload.open == 3612.15
    assert (payload.high, payload.low, payload.preClose) == (3660.49, 3610.2, 3689.91)
    assert payload.amountYuan == pytest.approx(7.7263457e11)  # 腾讯成交额单位是万元
    # 收盘后腾讯补的 15:30 重复点要丢掉；成交量是累计值，逐分钟取差；指数没有均价。
    assert [p.time for p in payload.intraday] == ["09:30", "09:31", "15:00"]
    assert [p.volume for p in payload.intraday] == [
        9008737,
        34237289 - 9008737,
        477042561 - 34237289,
    ]
    assert all(p.avgPrice is None for p in payload.intraday)
    assert [b.changePct for b in payload.dailyBars] == [None, 0.1, -1.1]
    assert payload.meta.sources == ["腾讯财经 行情", "腾讯财经 分时", "腾讯财经 日K"]
    assert payload.meta.unavailable == {}


def test_a_share_index_reports_every_gap_when_all_upstreams_fail() -> None:
    payload = _detail("399001.SZ", {}, fail=(*PUSH2, TX_QUOTE, TX_MINUTE, TX_KLINE))
    assert payload.price is None and payload.intraday == [] and payload.dailyBars == []
    assert set(payload.meta.unavailable) == {"指数行情", "指数分时", "指数日K"}
    assert "腾讯财经分时也取不到" in payload.meta.unavailable["指数分时"]
    assert "上游超时" in payload.meta.unavailable["指数日K"]
    assert payload.meta.session == "unknown" and payload.meta.tradeDate is None


# ───────────── 港股 / 美股指数 ─────────────


def test_hong_kong_index_tencent_fallback_keeps_close_in_1600() -> None:
    routes = {TX_QUOTE: HK_QUOTE_TX, TX_HK_MINUTE: HK_MINUTE_TX, TX_KLINE: HK_KLINE_TX}
    payload = _detail("HSI.HK", routes, fail=PUSH2)
    assert (payload.category, payload.price, payload.open) == ("海外指数", 24481.86, 24404.62)
    assert payload.amountYuan is None  # 腾讯港股指数的成交额口径不同，不给
    # 收盘后腾讯最后一点的时钟是快照时间 18:05，它就是收盘价，记在 16:00。
    assert [p.time for p in payload.intraday] == ["09:30", "09:31", "15:59", "16:00"]
    assert payload.intraday[-1].price == 24481.86
    assert len(payload.dailyBars) == 3 and payload.dailyBars[1].changePct == 3.67
    assert payload.meta.unavailable == {}


def test_us_index_tencent_fallback_uses_beijing_time_and_composite_code() -> None:
    routes = {TX_QUOTE: US_QUOTE_TX, TX_US_MINUTE: US_MINUTE_TX, TX_KLINE: US_KLINE_TX}
    service, gateway = _service(_Gateway(routes, PUSH2))
    payload = asyncio.run(service.index_detail("NDX.US"))
    # 东财的 100.NDX 是纳斯达克综合指数，腾讯对应 usIXIC（不是纳指 100 的 usNDX）。
    assert any("q=usIXIC" in url for url in gateway.calls)
    assert any("code=usIXIC.OQ" in url for url in gateway.calls)
    assert not any("usNDX" in url for url in gateway.calls)
    assert payload.price == 24315.0
    # 11 月美股是美东标准时间（UTC-5）：09:30 → 北京 22:30，16:00 → 次日 05:00。
    assert [p.time for p in payload.intraday] == ["22:30", "22:31", "22:32", "05:00"]
    assert payload.intraday[0].volume == 0
    assert payload.intraday[1].volume == 836293878
    assert payload.intraday[-1].price == 24315.0
    assert all(p.avgPrice is None for p in payload.intraday)
    assert payload.meta.tradeDate == "2026-11-05"
    assert payload.meta.session == "close"
    assert payload.meta.unavailable == {}


def test_us_session_is_intraday_while_new_york_is_open() -> None:
    # 北京 2026-11-05 23:00 = 纽约 10:00，美股盘中；分时日期 = 美东交易日 2026-11-05。
    now = datetime(2026, 11, 5, 23, 0, tzinfo=SHANGHAI)
    routes = {TX_QUOTE: US_QUOTE_TX, TX_US_MINUTE: US_MINUTE_TX, TX_KLINE: US_KLINE_TX}
    payload = _detail("NDX.US", routes, fail=PUSH2, now=now)
    assert payload.meta.session == "intraday"


# ───────────── 商品 ─────────────


def test_commodity_has_no_intraday_but_reports_why_and_uses_sina_daily() -> None:
    routes = {TX_QUOTE: GOLD_QUOTE_TX, SINA_DAILY: SINA_GC}
    service, gateway = _service(_Gateway(routes, PUSH2))
    payload = asyncio.run(service.index_detail("COMEX黄金"))
    assert (payload.name, payload.category) == ("黄金", "大宗商品")
    assert (payload.price, payload.change, payload.changePct) == (4075.29, -38.21, -0.93)
    assert (payload.open, payload.high, payload.low, payload.preClose) == (
        4098.5,
        4116.2,
        4044.5,
        4113.5,
    )
    assert payload.intraday == []
    reason = payload.meta.unavailable["指数分时"]
    assert "上游超时" in reason and "腾讯财经没有 COMEX / NYMEX 期货分时接口" in reason
    # 没向腾讯请求不存在的期货分时 / 日K。
    assert not any(TX_MINUTE in url or TX_KLINE in url for url in gateway.calls)
    assert [b.date for b in payload.dailyBars] == ["2026-11-04", "2026-11-05", "2026-11-06"]
    assert all(b.volume is None for b in payload.dailyBars)  # 新浪期货日K恒为 0 → 视为没有
    assert [b.changePct for b in payload.dailyBars] == [None, 0.63, -0.4]
    assert "指数日K" not in payload.meta.unavailable
    assert "新浪财经 期货日K" in payload.meta.sources
    assert any("不含成交量" in note for note in payload.meta.notes)


def test_commodity_daily_gap_is_reported_when_sina_fails() -> None:
    payload = _detail("NYMEX原油", {}, fail=(*PUSH2, TX_QUOTE, SINA_DAILY))
    assert payload.dailyBars == [] and payload.intraday == []
    assert "新浪财经日K也取不到" in payload.meta.unavailable["指数日K"]
    assert "指数分时" in payload.meta.unavailable and "指数行情" in payload.meta.unavailable


def test_commodity_prefers_eastmoney_intraday_when_reachable() -> None:
    quote = {
        "data": {
            "diff": [{"f2": 4081.6, "f3": -0.78, "f4": -31.9, "f12": "GC00Y", "f13": 101}],
        }
    }
    trend = {
        "data": {
            "trends": [
                "2026-11-06 07:00,4098.5,4098.4,4098.5,4097.8,10,0.0,4098.27",
                "2026-11-06 07:01,4097.9,4098.7,4099.7,4096.7,85,0.0,4097.87",
            ]
        }
    }
    kline = {
        "data": {
            "klines": ["2026-11-06,4098.5,4084.4,4116.2,4044.5,63512,0.0,1.74,-0.71,-29.1,0.0"]
        }
    }
    payload = _detail("COMEX黄金", {EM_QUOTE: quote, EM_TREND: trend, EM_KLINE: kline})
    assert [p.price for p in payload.intraday] == [4098.4, 4098.7]
    assert payload.intraday[0].volume == 10 and payload.intraday[0].avgPrice == 4098.27
    assert payload.dailyBars[0].volume == 63512
    assert payload.meta.unavailable == {}


# ───────────── 代码解析 / 路由 ─────────────


def test_unknown_code_is_404_and_known_code_is_case_insensitive() -> None:
    service, gateway = _service(_Gateway({}))
    with pytest.raises(ProviderError) as info:
        asyncio.run(service.index_detail("000001.SZ"))  # 平安银行不是指数，不能被误认成上证指数
    assert info.value.status_code == 404 and info.value.code == "NOT_FOUND"
    assert gateway.calls == []
    routes = {EM_QUOTE: SH_QUOTE_EM, EM_TREND: SH_TREND_EM, EM_KLINE: SH_KLINE_EM}
    assert _detail(
        " hsi.hk ", {EM_QUOTE: {}, EM_TREND: SH_TREND_EM, EM_KLINE: SH_KLINE_EM}
    ).code == ("HSI.HK")
    assert _detail("000001.sh", routes).code == "000001.SH"


def test_route_returns_camel_case_payload_and_404() -> None:
    routes = {EM_QUOTE: SH_QUOTE_EM, EM_TREND: SH_TREND_EM, EM_KLINE: SH_KLINE_EM}
    service, _ = _service(_Gateway(routes))
    application = create_app()
    application.dependency_overrides[_panorama] = lambda: service
    client = TestClient(application)
    ok = client.get("/api/v1/ashare/index-detail", params={"code": "000001.SH"})
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert {
        "code",
        "name",
        "category",
        "price",
        "change",
        "changePct",
        "open",
        "high",
        "low",
    } <= set(data)
    assert {"preClose", "amountYuan", "intraday", "dailyBars", "meta"} <= set(data)
    assert set(data["dailyBars"][0]) == {
        "date",
        "open",
        "close",
        "low",
        "high",
        "volume",
        "changePct",
    }
    assert client.get("/api/v1/ashare/index-detail", params={"code": "XYZ"}).status_code == 404
    assert client.get("/api/v1/ashare/index-detail").status_code == 422


# ───────────── 适配器 ─────────────


def test_sina_futures_daily_parses_jsonp_and_skips_broken_rows() -> None:
    bars = sina.parse_futures_daily(SINA_GC.encode())
    assert [(b.day, b.close) for b in bars] == [
        ("2026-11-04", 4072.0),
        ("2026-11-05", 4097.8),
        ("2026-11-06", 4081.4),
    ]
    with pytest.raises(ProviderError):
        sina.parse_futures_daily(b"<html>blocked</html>")


def test_tencent_quote_open_for_stock_format_and_futures() -> None:
    quotes = tq.parse_quotes(SH_QUOTE_TX + GOLD_QUOTE_TX)
    assert quotes["sh000001"].open == 3612.15
    assert quotes["hf_GC"].open == 4098.5
    assert tq.as_em_row(quotes["hf_GC"], "101.GC00Y")["f17"] == 4098.5
    assert tq.tencent_code("100.NDX") == "usIXIC"
    assert "usIXIC.OQ" in tq.minute_url("usIXIC")
