"""板块行情服务：缓存、stale 回退与区块降级。用假网关，不联网。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date, timedelta

import pytest
from app.providers.errors import ProviderError
from app.services.industry_market import IndustryMarketService
from app.services.market_cache import TtlCache

TREE = json.dumps(
    {
        "bklist": [
            {"code": "BK1201", "name": "电子", "type": 2, "flag": 1},
            {"code": "BK1036", "name": "半导体", "type": 2, "flag": 2},
            {"code": "BK1157", "name": "飞行汽车", "type": 3, "flag": 0},
        ]
    }
).encode()


def _quote(code: str, name: str) -> dict[str, object]:
    return {"f12": code, "f14": name, "f3": 1.5, "f20": 1000, "f62": 10, "f104": 3, "f105": 1}


def _klines(days: int, start: float = 100.0) -> bytes:
    first = date(2025, 9, 1)
    lines = [
        f"{(first + timedelta(days=i)).isoformat()},1,{start + i},1,1,1,1" for i in range(days)
    ]
    return json.dumps({"data": {"klines": lines}}).encode()


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        for marker in self.fail:
            if marker in url:
                raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", status_code=403)
        if "sidemenu_new" in url:
            return TREE
        if "ulist.np" in url:
            codes = [part.split(".")[1] for part in url.split("secids=")[1].split("%2C")]
            names = {"BK1201": "电子", "BK1036": "半导体", "BK1157": "飞行汽车"}
            return json.dumps(
                {"data": {"diff": [_quote(code, names.get(code, code)) for code in codes]}}
            ).encode()
        if "clist" in url:
            return json.dumps(
                {
                    "data": {
                        "total": 3,
                        "diff": [
                            {
                                "f12": "688385",
                                "f13": 1,
                                "f14": "复旦微电",
                                "f3": 5,
                                "f20": 300,
                                "f100": "半导体",
                            },
                            {
                                "f12": "002475",
                                "f13": 0,
                                "f14": "立讯精密",
                                "f3": -2,
                                "f20": 100,
                                "f100": "消费电子",
                            },
                            {"f12": "000001", "f13": 0, "f14": "停牌股", "f3": "-", "f100": "元件"},
                        ],
                    }
                }
            ).encode()
        if "kline" in url:
            return _klines(300)
        if "RPT_VALUEINDUSTRY_DET" in url:
            first = date(2026, 10, 16)
            rows = [
                {
                    "TRADE_DATE": f"{(first - timedelta(days=i)).isoformat()} 00:00:00",
                    "PE_TTM": 100 - i * 0.1,
                    "PB_MRQ": 5,
                    "NUM": 188,
                    "LOSS_COUNT": 20,
                }
                for i in range(60)
            ]
            return json.dumps({"result": {"pages": 1, "data": rows}}).encode()
        if "reportapi" in url:
            return json.dumps({"hits": 0, "data": []}).encode()
        raise AssertionError(f"unexpected url {url}")

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _service(gateway: FakeGateway, clock: Clock | None = None) -> IndustryMarketService:
    return IndustryMarketService(
        gateway=gateway, cache=TtlCache(clock or Clock()), today=lambda: date(2026, 10, 17)
    )


def test_overview_filters_level_and_caches() -> None:
    gateway = FakeGateway()
    service = _service(gateway)
    overview = asyncio.run(service.overview("1"))
    assert [board.code for board in overview.boards] == ["BK1201"]
    assert overview.boards[0].level == 1
    assert overview.breadth is not None and overview.breadth.stocks_up == 3
    calls = len(gateway.calls)
    asyncio.run(service.overview("1"))
    assert len(gateway.calls) == calls


def test_overview_serves_stale_quotes_when_refresh_fails() -> None:
    gateway = FakeGateway()
    clock = Clock()
    service = _service(gateway, clock)
    asyncio.run(service.overview("concept"))
    clock.now += 120
    gateway.fail.add("ulist.np")
    overview = asyncio.run(service.overview("concept"))
    assert overview.section.status == "stale"
    assert overview.boards[0].code == "BK1157"


def test_overview_without_any_cache_raises_structured_error() -> None:
    gateway = FakeGateway()
    gateway.fail.add("sidemenu_new")
    with pytest.raises(ProviderError) as caught:
        asyncio.run(_service(gateway).overview("1"))
    assert caught.value.status_code == 403


def test_level_two_detail_has_trend_members_and_valuation() -> None:
    detail = asyncio.run(_service(FakeGateway()).detail("bk1036"))
    assert detail.board.name == "半导体"
    assert detail.trend.section.status == "ok"
    assert detail.trend.bars[-1].ma20 is not None
    assert detail.trend.returns.d20 is not None
    assert detail.trend.relative and detail.trend.relative[-1].excess_pct == 0
    assert detail.members.total == 3 and detail.members.suspended == 1
    groups = {group.name: group for group in detail.members.sub_industries}
    assert set(groups) == {"半导体", "消费电子", "元件"}
    assert groups["半导体"].code == "BK1036"
    assert groups["元件"].weighted_change_pct is None
    assert detail.valuation.section.status == "ok"
    assert detail.valuation.pe_ttm is not None and detail.valuation.loss_members == 20


def test_level_one_detail_explains_missing_valuation_and_degrades_members() -> None:
    gateway = FakeGateway()
    gateway.fail.add("clist")
    detail = asyncio.run(_service(gateway).detail("BK1201"))
    assert detail.valuation.section.status == "unavailable"
    assert "二级行业" in (detail.valuation.section.reason or "")
    assert detail.members.section.status == "unavailable"
    assert detail.members.members == []
    assert detail.trend.section.status == "ok"
    assert not any("RPT_VALUEINDUSTRY_DET" in url for url in gateway.calls)


def test_empty_reports_are_unavailable_with_reason() -> None:
    reports = asyncio.run(_service(FakeGateway()).reports("BK1201"))
    assert reports.section.status == "unavailable"
    assert reports.reports == []


def test_hot_list_degrades_each_source_independently() -> None:
    gateway = FakeGateway()
    gateway.fail.add("10jqka")
    hot = asyncio.run(_service(gateway).hot())
    assert hot.eastmoney_section.status == "unavailable"
    assert hot.ths_section.status == "unavailable"
    assert hot.plates_section.status == "unavailable"
    assert hot.eastmoney == [] and hot.ths_stocks == []
