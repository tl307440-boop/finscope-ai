"""腾讯行情备用通道：东财 push2 / push2his 连不上时，页面仍能拿到交易日历、报价与当日成交额。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime

import pytest
from app.providers import tencent_quote as tq
from app.providers.errors import ProviderError
from app.services.ashare_panorama import ASharePanoramaService
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI


def _quote_line(
    local: str, name: str, code: str, price: str, change: str, pct: str, amount_wan: str = "0"
) -> str:
    fields = ["0"] * 50
    fields[1], fields[2], fields[3] = name, code, price
    fields[31], fields[32], fields[37], fields[38], fields[44] = (
        change,
        pct,
        amount_wan,
        "1.5",
        "100",
    )
    return f'v_{local}="{"~".join(fields)}";'


def test_tencent_code_mapping() -> None:
    assert tq.tencent_code("1.000001") == "sh000001"
    assert tq.tencent_code("0.399001") == "sz399001"
    assert tq.tencent_code("0.899050") == "bj899050"
    assert tq.tencent_code("0.920001") == "bj920001"
    assert tq.tencent_code("0.000001") == "sz000001"
    assert tq.tencent_code("100.HSI") == "hkHSI"
    assert tq.tencent_code("100.SPX") == "usINX"
    assert tq.tencent_code("101.GC00Y") is None  # 商品期货腾讯没有


def test_parse_quotes_units_and_em_row_shape() -> None:
    payload = "\n".join(
        [
            _quote_line("sh000001", "上证指数", "000001", "3649.18", "-40.73", "-1.10", "77263457"),
            _quote_line("hkHSI", "恒生指数", "HSI", "24420.83", "-376.15", "-1.52", "999"),
        ]
    ).encode("gbk")
    quotes = tq.parse_quotes(payload)
    sh = quotes["sh000001"]
    assert sh.amount_yuan == pytest.approx(77263457e4)  # 万元 → 元
    assert sh.float_cap_yuan == pytest.approx(100e8)  # 亿元 → 元
    assert quotes["hkHSI"].amount_yuan is None  # 港股指数的成交额口径不同，不取
    row = tq.as_em_row(sh, "1.000001")
    assert (row["f2"], row["f3"], row["f4"]) == (3649.18, -1.10, -40.73)
    assert (row["f12"], row["f13"], row["f14"]) == ("000001", 1, "上证指数")


def test_parse_quotes_ignores_garbage_lines() -> None:
    assert tq.parse_quotes(b'v_pv_none_match="1";\nnot a quote\nv_sh000001="1~x~"') == {}


def test_parse_kline_days_and_minutes() -> None:
    kline = {"code": 0, "data": {"sh000001": {"day": [["2026-11-05", "1"], ["2026-11-06", "1"]]}}}
    assert tq.parse_kline_days(json.dumps(kline).encode(), "sh000001") == [
        "2026-11-05",
        "2026-11-06",
    ]
    minute = {
        "code": 0,
        "data": {
            "sh000001": {
                "data": {
                    "date": "20261106",
                    "data": [
                        "0930 3612.15 9008737 13800185108.80",
                        "1130 3640.00 300000000 500000000000.00",
                        "1500 3649.18 477042561 772634566231.10",
                    ],
                }
            }
        },
    }
    minutes = tq.parse_minutes(json.dumps(minute).encode(), "sh000001")
    assert minutes.day == "2026-11-06"
    assert minutes.total_amount == pytest.approx(772634566231.10)
    assert minutes.amount_until("11:30") == pytest.approx(500000000000.0)
    assert minutes.amount_until("09:00") is None


def test_bad_payload_is_a_provider_error() -> None:
    with pytest.raises(ProviderError):
        tq.parse_kline_days(b"<html>", "sh000001")
    with pytest.raises(ProviderError):
        tq.parse_minutes(json.dumps({"code": 0, "data": {"sh000001": {"data": {}}}}).encode(), "x")


class _Gateway:
    """东财 push2 / push2his 一律连接失败；腾讯按地址片段返回预置字节。"""

    def __init__(self, routes: Mapping[str, bytes]) -> None:
        self.routes = routes

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        if "push2.eastmoney.com" in url or "push2his.eastmoney.com" in url:
            raise ProviderError(
                code="UPSTREAM_UNREACHABLE", message="上游连接中断", status_code=502
            )
        for marker, body in self.routes.items():
            if marker in url:
                return body
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        return await self.get(url, headers)


def _service(routes: Mapping[str, bytes]) -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(gateway=_Gateway(routes), cache=TtlCache(), now=lambda: now)


_KLINE = json.dumps(
    {"code": 0, "data": {"sh000001": {"day": [["2026-11-05", "1"], ["2026-11-06", "1"]]}}}
).encode()


def test_calendar_falls_back_to_tencent_when_eastmoney_is_unreachable() -> None:
    service = _service({"fqkline/get": _KLINE})
    payload = asyncio.run(service.ladder())  # 涨停池等取不到，但日历来自腾讯
    assert payload.tradeDate == "2026-11-06"
    assert payload.meta.session == "close"
    assert "交易日历" not in payload.meta.unavailable
    assert "腾讯财经 日K" in payload.meta.sources


def test_global_quotes_use_tencent_and_flag_missing_commodities() -> None:
    qt = "\n".join(
        [
            _quote_line("sh000001", "上证指数", "000001", "3649.18", "-40.73", "-1.10", "77263457"),
            _quote_line("hkHSI", "恒生指数", "HSI", "24420.83", "-376.15", "-1.52"),
        ]
    ).encode("gbk")
    payload = asyncio.run(_service({"qt.gtimg.cn": qt}).global_quotes())
    by_code = {q.code: q for q in payload.quotes}
    assert by_code["000001"].price == 3649.18
    assert by_code["000001"].changePct == -1.10
    assert by_code["000001"].amountYuan == pytest.approx(77263457e4)
    assert by_code["HSI"].price == 24420.83
    # 腾讯没有商品期货：价格留空并点名，不用别的数顶替
    assert by_code["GC00Y"].price is None
    assert "黄金" in payload.meta.unavailable
    assert "全球行情" not in payload.meta.unavailable


def test_volume_uses_tencent_minutes_for_today_only() -> None:
    minute = json.dumps(
        {
            "code": 0,
            "data": {
                "x": {
                    "data": {
                        "date": "20261106",
                        "data": [
                            "1130 3640.00 1 500000000000.00",
                            "1500 3649.18 2 772634566231.10",
                        ],
                    }
                }
            },
        }
    ).encode()

    # 每个指数的 code 不同，响应里的键要跟请求一致：用一个能按 code 改写的网关
    class PerCode(_Gateway):
        async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
            if "minute/query" in url:
                code = url.rsplit("code=", 1)[1]
                if code.startswith("bj"):  # 腾讯分时不覆盖北交所
                    raise ProviderError(code="UPSTREAM_PAYLOAD", message="无数据", status_code=502)
                return minute.replace(b'"x"', f'"{code}"'.encode())
            return await super().get(url, headers)

    bj_quote = _quote_line(
        "bj899050", "北证50", "899050", "888.48", "-21.59", "-2.37", "1400044"
    ).encode("gbk")
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    service = ASharePanoramaService(
        gateway=PerCode({"fqkline/get": _KLINE, "qt.gtimg.cn": bj_quote}),
        cache=TtlCache(),
        now=lambda: now,
    )
    payload = asyncio.run(service.volume_margin())
    by_key = {v.key: v for v in payload.volumes}
    assert by_key["sh"].noonAmountYi == pytest.approx(5000.0)
    assert by_key["sh"].noonDeltaYi is None  # 昨日同一时点腾讯拿不到，不倒推
    # 北证只有报价里的当日成交额（140.0044 亿），没有午盘累计：全市场午盘留空，不拿别的数凑
    assert by_key["bj"].todayAmountYi == pytest.approx(140.0, abs=0.01)
    assert by_key["bj"].noonAmountYi is None
    assert by_key["all"].todayAmountYi == pytest.approx(2 * 7726.35 + 140.0, abs=0.05)
    assert by_key["all"].noonAmountYi is None
    assert any("腾讯分时" in note for note in payload.meta.notes)
