"""涨停股池与机构调研：解析、统计与降级。假网关，不联网。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from app.providers import eastmoney_sentiment as es
from app.providers.errors import ProviderError
from app.services.market_cache import TtlCache
from app.services.market_sentiment import (
    MarketSentimentService,
    seal_rate,
    stock_secid,
)
from app.utils.market_time import SHANGHAI

FRIDAY = date(2026, 10, 16)


def _pool(rows: list[dict[str, object]], qdate: int = 20261016) -> bytes:
    return json.dumps({"rc": 0, "data": {"tc": len(rows), "qdate": qdate, "pool": rows}}).encode()


ZT_ROWS: list[dict[str, object]] = [
    {
        "c": "600825",
        "m": 1,
        "n": "新华传媒",
        "p": 15160,
        "zdp": 10.0072,
        "amount": 1.2e9,
        "ltsz": 2.2e9,
        "tshare": 3.79e9,
        "hs": 12.3456,
        "lbc": 11,
        "fbt": 92500,
        "lbt": 140102,
        "fund": 908271953,
        "zbc": 1,
        "hybk": "出版",
        "zttj": {"days": 11, "ct": 11},
    },
    {
        "c": "000573",
        "m": 0,
        "n": "粤宏远Ａ",
        "p": 4470,
        "zdp": 10.09,
        "lbc": 1,
        "fbt": 100512,
        "hybk": "房地产开",
    },
    {"c": "300001", "m": 0, "n": "特锐德", "p": "-", "zdp": "-", "fbt": 131000, "hybk": "出版"},
]


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        if any(marker in url for marker in self.fail):
            raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", status_code=403)
        if "push2ex" in url:
            day = parse_qs(urlparse(url).query)["date"][0]
            if day != "20261016":
                return json.dumps({"rc": 0, "data": None}).encode()
            if "ZTPool" in url:
                return _pool(ZT_ROWS)
            if "ZBPool" in url:
                return _pool([{"c": "002001", "m": 0, "n": "炸板股", "p": 1000, "fbt": 93000}])
            return _pool([])
        if "RPT_ORG_SURVEYNEW" in url:
            rows = [
                {
                    "SECURITY_CODE": "300124",
                    "SECURITY_NAME_ABBR": "汇川技术",
                    "NOTICE_DATE": "2026-10-16 00:00:00",
                    "RECEIVE_START_DATE": "2026-10-15 00:00:00",
                    "SUM": 200,
                    "RECEIVE_WAY_EXPLAIN": "特定对象调研,电话会议",
                },
                {
                    "SECURITY_CODE": "300124",
                    "SECURITY_NAME_ABBR": "汇川技术",
                    "NOTICE_DATE": "2026-10-10 00:00:00",
                    "SUM": 118,
                    "RECEIVE_WAY_EXPLAIN": "现场参观",
                },
                {
                    "SECURITY_CODE": "600610",
                    "SECURITY_NAME_ABBR": "中毅达",
                    "NOTICE_DATE": "2026-10-16 00:00:00",
                    "SUM": 1,
                    "RECEIVE_WAY_EXPLAIN": "业绩说明会",
                },
                {"SECURITY_CODE": "000001", "NOTICE_DATE": None},
            ]
            return json.dumps({"success": True, "result": {"pages": 1, "data": rows}}).encode()
        if "ulist.np" in url:
            diff = [
                {"f12": "300124", "f14": "汇川技术", "f100": "自动化设备"},
                {"f12": "600610", "f14": "中毅达", "f100": "化学制品"},
            ]
            return json.dumps({"data": {"diff": diff}}).encode()
        raise AssertionError(url)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise AssertionError(url)


def _service(gateway: FakeGateway, now: datetime) -> MarketSentimentService:
    return MarketSentimentService(gateway=gateway, cache=TtlCache(), now=lambda: now)


def test_pool_parse_scales_price_and_keeps_missing_values() -> None:
    page = es.parse_pool(_pool(ZT_ROWS))
    assert page.trade_date == FRIDAY
    first, _, suspended = page.stocks
    assert first.price == Decimal("15.16") and first.first_seal == "09:25:00"
    assert first.exchange == "SSE" and first.limit_days == 11
    assert (first.float_cap, first.total_cap) == (Decimal("2200000000"), Decimal("3790000000"))
    assert suspended.price is None and suspended.change_pct is None
    assert suspended.total_cap is None and suspended.float_cap is None  # 没给就是没有，不补 0
    assert es.parse_pool(json.dumps({"data": None}).encode()).trade_date is None


def test_limit_up_walks_back_to_latest_session_and_summarises() -> None:
    gateway = FakeGateway()
    view = asyncio.run(_service(gateway, datetime(2026, 10, 18, 10, tzinfo=SHANGHAI)).limit_up())
    assert view.trade_date == FRIDAY and view.section.status == "ok"
    assert (view.limit_up, view.broken, view.limit_down) == (3, 1, 0)
    assert view.seal_rate == Decimal("75.00")
    assert view.max_streak == 11 and [step.streak for step in view.ladder] == [11, 1]
    assert view.industries[0].label == "出版" and view.industries[0].count == 2
    counts = {item.label: item.count for item in view.seal_times}
    assert counts["竞价封板"] == 1 and counts["10:00-11:30"] == 1 and counts["13:00-14:00"] == 1


def test_limit_up_marks_missing_side_pools_as_unknown_not_zero() -> None:
    gateway = FakeGateway()
    gateway.fail.add("ZBPool")
    view = asyncio.run(_service(gateway, datetime(2026, 10, 16, 16, tzinfo=SHANGHAI)).limit_up())
    assert view.broken is None and view.seal_rate is None
    assert "炸板池" in (view.section.reason or "")


def test_limit_up_for_holiday_is_unavailable() -> None:
    view = asyncio.run(
        _service(FakeGateway(), datetime(2026, 10, 16, 16, tzinfo=SHANGHAI)).limit_up(
            date(2026, 10, 1)
        )
    )
    assert view.section.status == "unavailable" and view.limit_up_stocks == []


def test_surveys_aggregate_by_stock_and_degrade_industries() -> None:
    gateway = FakeGateway()
    now = datetime(2026, 10, 17, 10, tzinfo=SHANGHAI)
    view = asyncio.run(_service(gateway, now).surveys(30))
    assert view.events == 3 and view.stocks == 2 and view.institutions == 319
    top = view.top_stocks[0]
    assert top.name == "汇川技术" and top.events == 2 and top.institutions == 318
    assert top.industry == "自动化设备" and top.latest == FRIDAY
    assert {item.label for item in view.ways} >= {"特定对象调研", "现场参观"}

    broken = FakeGateway()
    broken.fail.add("ulist.np")
    degraded = asyncio.run(_service(broken, now).surveys(30))
    assert degraded.industry_section.status == "unavailable"
    assert degraded.top_stocks[0].industry is None and degraded.events == 3


def test_helpers() -> None:
    assert seal_rate(0, 0) is None and seal_rate(3, None) is None
    assert stock_secid("600519") == "1.600519" and stock_secid("830799") == "0.830799"
    assert stock_secid("12345") is None and stock_secid("700001") is None


def test_survey_url_filters_by_notice_date() -> None:
    query = parse_qs(urlparse(es.survey_url(date(2026, 9, 17), 1)).query)
    assert "NOTICE_DATE>='2026-09-17'" in query["filter"][0]


def test_bad_pool_payload_raises() -> None:
    with pytest.raises(ProviderError):
        es.parse_pool(b"not json")
