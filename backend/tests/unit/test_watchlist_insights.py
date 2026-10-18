"""自选股看板：代码解析、汇总、行业暴露、实时失败回退入库日线。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.providers.errors import ProviderError
from app.repositories.watchlists import RecentClose, WatchGroup, WatchItem
from app.services.industry_market import IndustryMarketService
from app.services.market_cache import TtlCache
from app.services.market_sentiment import MarketSentimentService
from app.services.watchlist_insights import (
    WatchlistInsightsService,
    parse_targets,
    window_change,
    ytd_change,
)

TREE = json.dumps({"bklist": [{"code": "BK0477", "name": "白酒Ⅱ", "type": 2, "flag": 2}]}).encode()


class Upstream:
    def __init__(self) -> None:
        self.fail: set[str] = set()

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        if any(marker in url for marker in self.fail):
            raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", status_code=403)
        if "sidemenu_new" in url:
            return TREE
        other = "BK0473" in url or "105.AAPL" in url
        if "ulist.np" in url and "f23" in url and not other:
            diff = [
                {
                    "f12": "600519",
                    "f14": "贵州茅台",
                    "f3": 2,
                    "f20": 3000,
                    "f62": 100,
                    "f100": "白酒Ⅱ",
                },
                {
                    "f12": "000858",
                    "f14": "五粮液",
                    "f3": -1,
                    "f20": 1000,
                    "f62": -50,
                    "f100": "白酒Ⅱ",
                },
                {"f12": "300750", "f14": "宁德时代", "f3": "-", "f20": "-", "f100": "电池"},
            ]
            return json.dumps({"data": {"diff": diff}}).encode()
        if "ulist.np" in url and other:
            diff = [
                {"f12": "BK0473", "f13": 90, "f14": "证券Ⅱ", "f2": "134562.68", "f3": "-1.23"},
                {"f12": "AAPL", "f13": 105, "f14": "苹果", "f2": 250, "f3": 1.5},
            ]
            return json.dumps({"data": {"diff": diff}}).encode()
        if "push2ex" in url:
            pool = (
                [{"c": "000858", "n": "五粮液", "lbc": 2, "fbt": 93000}] if "ZTPool" in url else []
            )
            return json.dumps({"data": {"qdate": 20261016, "pool": pool}}).encode()
        if "RPT_ORG_SURVEYNEW" in url:
            rows = [
                {
                    "SECURITY_CODE": "600519",
                    "SECURITY_NAME_ABBR": "贵州茅台",
                    "NOTICE_DATE": "2026-10-10",
                    "SUM": 40,
                }
            ]
            return json.dumps({"result": {"pages": 1, "data": rows}}).encode()
        if "10jqka" in url and "stock" in url:
            stock = {"code": "600519", "name": "贵州茅台", "order": 7}
            return json.dumps({"status_code": 0, "data": {"stock_list": [stock]}}).encode()
        raise ProviderError(code="UPSTREAM_ERROR", message="不提供", status_code=503)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise ProviderError(code="UPSTREAM_ERROR", message="不提供", status_code=503)


ITEMS = (
    WatchItem(uuid.uuid4(), "600519.SH", "贵州茅台", "CN", "SSE", "600519"),
    WatchItem(uuid.uuid4(), "000858.SZ", "五粮液", "CN", "SZSE", "000858"),
    WatchItem(uuid.uuid4(), "300750.SZ", "宁德时代", "CN", "SZSE", "300750"),
    WatchItem(uuid.uuid4(), "AAPL", "苹果", "US", "NASDAQ", "AAPL", secid="105.AAPL"),
    # 外部标的：没有本系统证券记录，只有东财 secid 与名称。
    WatchItem(
        None, "BK0473", "证券Ⅱ", "EXT", "90", "BK0473", item_id=uuid.uuid4(), secid="90.BK0473"
    ),
)
GROUP = WatchGroup(
    id=uuid.uuid4(), name="消费", created_at=datetime(2026, 1, 1, tzinfo=UTC), items=ITEMS
)
NOW = datetime(2026, 10, 16, 8, tzinfo=UTC)


def _service(upstream: Upstream) -> WatchlistInsightsService:
    return WatchlistInsightsService(
        gateway=upstream,
        industry=IndustryMarketService(gateway=upstream, cache=TtlCache()),
        sentiment=MarketSentimentService(gateway=upstream, cache=TtlCache(), now=lambda: NOW),
        cache=TtlCache(),
        now=lambda: NOW,
    )


def _no_closes(ids: Sequence[UUID], since: date) -> dict[UUID, list[RecentClose]]:
    return {}


def test_parse_targets_splits_a_shares_external_targets_and_garbage() -> None:
    parsed = parse_targets(
        "600519, sz000858\n300750.SZ；1.601318 0.000001 贵州茅台 123456 830799 "
        "1.513120 513120 159516 90.BK1630 105.nvda 171.US10Y 12345 1.000001"
    )
    assert parsed.cn == [
        ("SSE", "600519"),
        ("SZSE", "000858"),
        ("SZSE", "300750"),
        ("SSE", "601318"),
        ("SZSE", "000001"),
        ("BSE", "830799"),
        ("SSE", "513120"),
        ("SSE", "000001"),
    ]
    # 没写前缀的 6 位基金 / 债券代码按号段推断沪深；东财 secid 原样保留并统一大写。
    assert parsed.external == [
        "0.123456",
        "1.513120",
        "0.159516",
        "90.BK1630",
        "105.NVDA",
        "171.US10Y",
    ]
    assert parsed.invalid == ["贵州茅台", "12345"]
    assert parse_targets("600519 600519").cn == [("SSE", "600519")]


def test_live_insights_join_signals_and_compute_exposure() -> None:
    insights = asyncio.run(_service(Upstream()).build(GROUP, _no_closes))
    assert insights.quote_section.status == "ok"
    by_code = {stock.code: stock for stock in insights.stocks}
    assert by_code["600519"].board_code == "BK0477"
    assert by_code["600519"].surveys == 1 and by_code["600519"].surveyed_institutions == 40
    assert by_code["600519"].ths_hot_rank == 7
    assert by_code["000858"].limit_status == "涨停" and by_code["000858"].streak == 2
    assert by_code["300750"].change_pct is None

    summary = insights.summary
    assert (summary.quoted, summary.up, summary.down) == (2, 1, 1)
    assert summary.average_change_pct == Decimal("0.50")
    assert summary.weighted_change_pct == Decimal("1.25")
    assert summary.total_main_net_inflow == Decimal(50)
    assert (summary.limit_up, summary.surveyed, summary.hot) == (1, 1, 1)

    liquor = insights.industries[0]
    assert liquor.name == "白酒Ⅱ" and liquor.count == 2 and liquor.weight_pct == Decimal("100.00")


def test_other_targets_are_listed_apart_and_stay_out_of_a_share_summaries() -> None:
    insights = asyncio.run(_service(Upstream()).build(GROUP, _no_closes))
    others = {row.code: row for row in insights.others}
    assert set(others) == {"AAPL", "BK0473"}
    board = others["BK0473"]
    assert board.kind == "board" and board.kind_label == "板块"
    assert board.last == Decimal("134562.68") and board.change_pct == Decimal("-1.23")
    assert board.instrument_id is None and board.item_id is not None
    assert others["AAPL"].kind == "us_stock" and others["AAPL"].change_pct == Decimal("1.5")
    assert insights.others_section.status == "ok"
    # 汇总只统计三只 A 股；板块与美股的涨跌不能混进等权平均。
    assert insights.summary.count == 3
    assert insights.summary.average_change_pct == Decimal("0.50")
    assert {stock.code for stock in insights.stocks} == {"600519", "000858", "300750"}


def test_other_targets_survive_when_their_quotes_fail() -> None:
    upstream = Upstream()
    upstream.fail.add("BK0473")
    insights = asyncio.run(_service(upstream).build(GROUP, _no_closes))
    assert insights.quote_section.status == "ok"
    assert insights.others_section.status == "unavailable"
    names = {row.code: (row.name, row.last) for row in insights.others}
    assert names == {"AAPL": ("苹果", None), "BK0473": ("证券Ⅱ", None)}


def test_resolve_names_is_keyed_by_secid_and_tolerates_outage() -> None:
    service = _service(Upstream())
    names = asyncio.run(service.resolve_names(["90.BK0473", "105.AAPL"]))
    assert names == {"90.BK0473": "证券Ⅱ", "105.AAPL": "苹果"}
    down = Upstream()
    down.fail.add("BK0473")
    assert asyncio.run(_service(down).resolve_names(["90.BK0473"])) == {}


class DropsFirstRow(Upstream):
    """东财偶尔少回一行：第一次请求缺最后一行，补查时才返回完整。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        body = await super().get(url, headers)
        if "ulist.np" not in url:
            return body
        self.calls += 1
        if self.calls > 1:
            return body
        payload = json.loads(body)
        payload["data"]["diff"] = payload["data"]["diff"][:-1]
        return json.dumps(payload).encode()


def test_resolve_names_retries_rows_the_upstream_left_out() -> None:
    upstream = DropsFirstRow()
    names = asyncio.run(_service(upstream).resolve_names(["90.BK0473", "105.AAPL"]))
    assert names == {"90.BK0473": "证券Ⅱ", "105.AAPL": "苹果"}
    assert upstream.calls == 2


def test_quotes_fall_back_to_stored_closes() -> None:
    upstream = Upstream()
    upstream.fail.add("f23")
    first = date(2025, 12, 1)
    closes = [RecentClose(first + timedelta(days=i), Decimal(100 + i)) for i in range(80)]

    def recent(ids: Sequence[UUID], since: date) -> dict[UUID, list[RecentClose]]:
        return {ITEMS[0].instrument_id: closes}

    insights = asyncio.run(_service(upstream).build(GROUP, recent))
    assert insights.quote_section.status == "stale" and insights.quote_section.origin == "stored"
    moutai = insights.stocks[0]
    assert moutai.origin == "stored" and moutai.last == Decimal(179)
    assert moutai.change_pct == window_change(closes, 1)
    assert insights.stocks[1].last is None


def test_quotes_unavailable_without_any_stored_bars() -> None:
    upstream = Upstream()
    upstream.fail.add("f23")
    insights = asyncio.run(_service(upstream).build(GROUP, _no_closes))
    assert insights.quote_section.status == "unavailable"
    assert insights.summary.quoted == 0 and insights.summary.average_change_pct is None


def test_window_and_ytd_changes() -> None:
    closes = [
        RecentClose(date(2025, 12, 31), Decimal(100)),
        RecentClose(date(2026, 1, 2), Decimal(110)),
        RecentClose(date(2026, 1, 5), Decimal(121)),
    ]
    assert window_change(closes, 1) == Decimal("10.00")
    assert window_change(closes, 5) is None
    assert ytd_change(closes, 2026) == Decimal("21.00")
    assert ytd_change(closes[1:], 2026) is None
