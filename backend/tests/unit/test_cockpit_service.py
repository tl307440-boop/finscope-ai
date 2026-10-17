"""总览驾驶舱服务：取数对齐与降级。假网关按 URL 路由返回构造的数据，不联网、不读库。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from app.providers.errors import ProviderError
from app.repositories.dashboard_metrics import StoredMetric
from app.services.cockpit import (
    DashboardService,
    announcement_window,
    bucket_label,
    minutes_traded,
)
from app.services.industry_market import IndustryMarketService
from app.services.market_cache import TtlCache
from app.services.market_sentiment import MarketSentimentService
from app.services.watchlist_insights import WatchlistInsightsService
from app.utils.market_time import SHANGHAI

AFTER_CLOSE = datetime(2026, 10, 16, 15, 30, tzinfo=SHANGHAI)


def _days(count: int, end: date = date(2026, 10, 16)) -> list[date]:
    days: list[date] = []
    current = end
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    return list(reversed(days))


def _kline(symbol: str, period: str) -> bytes:
    rows = [
        [
            day.isoformat(),
            "100",
            f"{100 + index * 0.5:.2f}",
            "101",
            "99",
            "1000",
            {},
            "1",
            "50000000",
        ]
        for index, day in enumerate(_days(70))
    ]
    return json.dumps({"code": 0, "data": {symbol: {period: rows}}}).encode()


def _quote(symbol: str, name: str, last: str, prev: str) -> str:
    fields = ["-"] * 60
    fields[1], fields[3], fields[4], fields[30] = name, last, prev, "20261016150000"
    fields[33], fields[34], fields[37] = last, prev, "50000000"
    return f'v_{symbol}="{"~".join(fields)}";'


def _pool(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"rc": 0, "data": {"tc": len(rows), "qdate": 20261016, "pool": rows}}
    ).encode()


class FakeGateway:
    def __init__(self) -> None:
        self.fail: set[str] = set()
        self.posts: list[dict[str, list[str]]] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        if any(marker in url for marker in self.fail):
            raise ProviderError(
                code="UPSTREAM_ERROR", message=f"上游失败：{url[:30]}", status_code=502
            )
        if "qt.gtimg.cn" in url:
            symbols = url.split("q=")[1].split(",")
            return "\n".join(_quote(symbol, symbol, "110", "100") for symbol in symbols).encode(
                "gbk"
            )
        if "newfqkline" in url:
            symbol, period = parse_qs(urlparse(url).query)["param"][0].split(",")[:2]
            return _kline(symbol, period)
        if "getTopicZDFenBu" in url:
            buckets = [{"11": 50}, {"5": 400}, {"1": 2600}, {"0": 100}, {"-1": 1800}, {"-6": 300}]
            return json.dumps({"data": {"qdate": 20261016, "fenbu": buckets}}).encode()
        if "getTopicZTPool" in url:
            return _pool(
                [{"c": "600001", "n": "甲", "lbc": 3}, {"c": "600002", "n": "乙", "lbc": 1}]
            )
        if "getTopicZBPool" in url:
            return _pool([{"c": "600003", "n": "丙"}])
        if "getTopicDTPool" in url:
            return _pool([])
        raise ProviderError(code="UPSTREAM_ERROR", message="测试未配置该上游", status_code=502)

    async def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        content_type: str = "application/json",
    ) -> bytes:
        form = parse_qs(body.decode(), keep_blank_values=True)
        self.posts.append(form)
        assert content_type.startswith("application/x-www-form-urlencoded")
        if form["searchkey"][0] in self.fail:
            raise ProviderError(code="UPSTREAM_ERROR", message="巨潮超时", status_code=502)
        category, keyword, page = form["category"][0], form["searchkey"][0], form["pageNum"][0]
        title = {
            "category_fxts_szsh;": "关于收到中国证监会立案告知书的公告",
            "category_yjygjxz_szsh;": "2026年前三季度业绩预告",
        }.get(category, f"关于{keyword or '日常'}事项的公告")
        row = {
            "announcementId": f"{category}{keyword}{page}",
            "secCode": "600001" if category else "000002",
            "secName": "甲公司",
            "announcementTitle": title,
            "adjunctUrl": f"finalpage/{category}{keyword}{page}.PDF",
            "announcementTime": 1792137600000,
        }
        return json.dumps({"announcements": [row]}).encode()


def _service(gateway: FakeGateway, now: datetime = AFTER_CLOSE) -> DashboardService:
    clock = lambda: now  # noqa: E731
    industry = IndustryMarketService(gateway=gateway, cache=TtlCache(), now=clock)
    sentiment = MarketSentimentService(gateway=gateway, cache=TtlCache(), now=clock)
    return DashboardService(
        gateway=gateway,
        overseas=gateway,
        industry=industry,
        sentiment=sentiment,
        insights=WatchlistInsightsService(
            gateway=gateway, industry=industry, sentiment=sentiment, now=clock
        ),
        cache=TtlCache(),
        now=clock,
    )


def test_market_aggregates_breadth_amounts_and_pools() -> None:
    overview = asyncio.run(_service(FakeGateway()).market())
    assert overview.trade_date == date(2026, 10, 16)
    assert overview.breadth_section.status == "ok"
    assert overview.breadth is not None
    assert (overview.breadth.up, overview.breadth.down, overview.breadth.flat) == (3050, 2100, 100)
    assert overview.breadth.limit_up == 50 and len(overview.breadth.buckets) == 23
    assert overview.limits is not None
    assert (overview.limits.limit_up, overview.limits.broken, overview.limits.limit_down) == (
        2,
        1,
        0,
    )
    assert overview.market_amount == Decimal("1000000000000.00")
    assert overview.amount_vs_base_pct == Decimal("0.00")
    assert [factor.code for factor in overview.temperature.factors] == [
        "trend",
        "breadth",
        "activity",
        "volatility",
    ]
    assert overview.temperature.score is not None and overview.temperature.missing == []
    first = overview.indices[0]
    assert first.name == "上证指数" and first.change_pct == Decimal("10.00")


def test_market_keeps_scoring_when_distribution_upstream_fails() -> None:
    gateway = FakeGateway()
    gateway.fail = {"getTopicZDFenBu", "getTopicZTPool"}
    overview = asyncio.run(_service(gateway).market())
    assert overview.breadth_section.status == "unavailable"
    assert overview.limits_section.status == "unavailable"
    assert overview.breadth is None and overview.limits is None
    assert overview.temperature.coverage == Decimal("0.65")
    assert overview.temperature.score is not None
    assert any("广度" in item for item in overview.temperature.missing)
    assert overview.quotes_section.status == "ok"


class FakeMetrics:
    def __init__(self, stored: dict[str, StoredMetric]) -> None:
        self.stored = stored
        self.recorded: dict[str, Decimal] = {}

    def record(self, trade_date: date, values: dict[str, Decimal], computed_at: datetime) -> None:
        self.recorded.update(values)

    def previous(self, metrics: list[str], before: date) -> dict[str, StoredMetric]:
        return {key: value for key, value in self.stored.items() if key in metrics}


def test_market_reports_contributions_and_change_vs_previous_day() -> None:
    service = _service(FakeGateway())
    metrics = FakeMetrics({"temperature": StoredMetric(date(2026, 10, 15), Decimal("40.04"))})
    service.metrics = metrics  # type: ignore[assignment]
    block = asyncio.run(service.market()).temperature
    assert block.score is not None
    shares = [factor.contribution for factor in block.factors]
    assert all(share is not None for share in shares)
    assert abs(sum(share or Decimal(0) for share in shares) - block.score) <= Decimal("0.05")
    assert block.previous == Decimal("40.0") and block.previous_date == date(2026, 10, 15)
    assert block.change == block.score - Decimal("40.0")
    assert metrics.recorded["temperature"] == block.score


def test_market_without_history_is_not_scored() -> None:
    gateway = FakeGateway()
    gateway.fail = {"newfqkline", "getTopicZDFenBu"}
    overview = asyncio.run(_service(gateway).market())
    assert overview.history_section.status == "unavailable"
    assert overview.temperature.score is None
    assert overview.temperature.label == "数据不足"
    assert overview.temperature.change is None
    assert all(factor.contribution is None for factor in overview.temperature.factors)
    assert overview.amounts == []


def test_index_bars_compute_moving_averages_and_reject_unknown_symbols() -> None:
    service = _service(FakeGateway())
    bars = asyncio.run(service.index_bars("sh000300", "day"))
    assert len(bars.bars) == 70 and bars.bars[3].ma5 is None
    assert bars.bars[4].ma5 == Decimal("101.00")
    assert bars.amount_vs_5d_pct == Decimal("0.00")
    week = asyncio.run(service.index_bars("sh000300", "week"))
    assert week.section.status == "ok" and len(week.bars) == 70
    with pytest.raises(ProviderError):
        asyncio.run(service.index_bars("sh600519", "day"))


def test_announcements_merge_queries_and_report_partial_failures() -> None:
    gateway = FakeGateway()
    gateway.fail = {"立案"}
    overview = asyncio.run(_service(gateway).announcements())
    assert overview.window_start == date(2026, 10, 15)
    assert all(form["stock"] == [""] for form in gateway.posts)
    assert len(gateway.posts) == 9
    assert overview.scanned == 8
    assert overview.section.status == "stale"
    assert overview.section.reason is not None and "立案" in overview.section.reason
    kinds = {item.kind for item in overview.important}
    assert {"风险", "业绩"} <= kinds
    assert overview.important[0].level == 3
    assert overview.important[0].url.startswith("https://static.cninfo.com.cn/")
    hot_codes = {item.code for item in overview.hot}
    assert hot_codes == {"600001"}


def test_announcements_unavailable_when_every_query_fails() -> None:
    gateway = FakeGateway()

    async def broken(*_: object, **__: object) -> bytes:
        raise ProviderError(code="UPSTREAM_ERROR", message="巨潮不可用", status_code=502)

    gateway.post = broken  # type: ignore[method-assign]
    overview = asyncio.run(_service(gateway).announcements())
    assert overview.section.status == "unavailable"
    assert overview.important == [] and overview.scanned == 0


def test_watch_without_groups_explains_how_to_add() -> None:
    overview = asyncio.run(_service(FakeGateway()).watch(None, [], lambda ids, since: {}))
    assert overview.total == 0 and overview.rows == []
    assert overview.quote_section.status == "unavailable"
    assert "自选关注" in (overview.quote_section.reason or "")


@pytest.mark.parametrize(
    ("moment", "minutes"),
    [(time(9, 0), 0), (time(10, 30), 60), (time(12, 0), 120), (time(14, 0), 180), (time(16), 240)],
)
def test_minutes_traded(moment: time, minutes: int) -> None:
    assert minutes_traded(moment) == minutes


def test_announcement_window_and_bucket_labels() -> None:
    assert announcement_window(date(2026, 10, 19)) == (date(2026, 10, 16), date(2026, 10, 19))
    assert announcement_window(date(2026, 10, 17)) == (date(2026, 10, 16), date(2026, 10, 17))
    assert announcement_window(date(2026, 10, 18)) == (date(2026, 10, 16), date(2026, 10, 18))
    assert [bucket_label(value) for value in (11, 10, 1, 0, -1, -10, -11)] == [
        "涨停",
        "≥9%",
        "0~1%",
        "平盘",
        "-1~0%",
        "≤-9%",
        "跌停",
    ]
