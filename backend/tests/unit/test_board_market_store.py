"""板块收盘入库、读库回退与轮动图。用内存仓库 + 假网关，不联网、不连库。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError
from app.repositories.board_market import StoredBars, StoredSnapshot
from app.services import board_analytics as rules
from app.services.board_market_sync import snapshot_allowed, sync_board_market
from app.services.industry_market import (
    BENCHMARK_SECID,
    IndustryMarketService,
    expected_session,
)
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI

TREE = json.dumps(
    {
        "bklist": [
            {"code": "BK1201", "name": "电子", "type": 2, "flag": 1},
            {"code": "BK1036", "name": "半导体", "type": 2, "flag": 2},
            {"code": "BK1157", "name": "飞行汽车", "type": 3, "flag": 0},
        ]
    }
).encode()


def _sessions(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _bars(days: Sequence[date], slope: float, wave: float = 0.0) -> tuple[em.BoardBar, ...]:
    bars = []
    for index, day in enumerate(days):
        close = Decimal(str(round(100 + slope * index + wave * ((index % 7) - 3), 4)))
        bars.append(
            em.BoardBar(
                trade_date=day,
                open=close,
                close=close,
                high=close,
                low=close,
                volume=Decimal(1),
                amount=Decimal(1),
            )
        )
    return tuple(bars)


def _kline_payload(bars: Sequence[em.BoardBar]) -> bytes:
    lines = [
        f"{bar.trade_date.isoformat()},{bar.open},{bar.close},{bar.high},{bar.low},1,1"
        for bar in bars
    ]
    return json.dumps({"data": {"klines": lines}}).encode()


class MemoryStore:
    def __init__(self) -> None:
        self.snaps: dict[tuple[str, date], StoredSnapshot] = {}
        self.bar_rows: dict[str, dict[date, em.BoardBar]] = {}
        self.fetched_at = datetime(2026, 10, 16, 8, 0, tzinfo=UTC)

    def upsert_snapshots(
        self,
        trade_date: date,
        rows: Sequence[tuple[em.BoardNode, em.BoardQuote]],
        fetched_at: datetime,
    ) -> int:
        for node, quote in rows:
            self.snaps[(node.code, trade_date)] = StoredSnapshot(
                node, quote, trade_date, fetched_at
            )
        return len(rows)

    def upsert_bars(self, secid: str, bars: Sequence[em.BoardBar], fetched_at: datetime) -> int:
        self.bar_rows.setdefault(secid, {}).update({bar.trade_date: bar for bar in bars})
        return len(bars)

    def last_bar_dates(self, secids: Sequence[str]) -> dict[str, date]:
        return {secid: max(self.bar_rows[secid]) for secid in secids if self.bar_rows.get(secid)}

    def latest_snapshot_date(self, on_or_before: date | None = None) -> date | None:
        days = [day for _, day in self.snaps if on_or_before is None or day <= on_or_before]
        return max(days) if days else None

    def snapshot_dates(self, limit: int) -> list[date]:
        return sorted({day for _, day in self.snaps}, reverse=True)[:limit]

    def snapshots(self, trade_date: date, kind: str, level: int | None) -> list[StoredSnapshot]:
        return [
            snap
            for (_, day), snap in self.snaps.items()
            if day == trade_date
            and snap.node.kind == kind
            and (level is None or snap.node.level == level)
        ]

    def latest_snapshot(self, board_code: str) -> StoredSnapshot | None:
        rows = [snap for (code, _), snap in self.snaps.items() if code == board_code]
        return max(rows, key=lambda snap: snap.trade_date) if rows else None

    def bars(self, secid: str, since: date, until: date | None = None) -> StoredBars:
        return self.bars_many([secid], since, until).get(secid, StoredBars((), None))

    def bars_many(
        self, secids: Sequence[str], since: date, until: date | None = None
    ) -> dict[str, StoredBars]:
        result: dict[str, StoredBars] = {}
        for secid in secids:
            rows = [
                bar
                for day, bar in sorted(self.bar_rows.get(secid, {}).items())
                if day >= since and (until is None or day <= until)
            ]
            if rows:
                result[secid] = StoredBars(tuple(rows), self.fetched_at)
        return result


class Upstream:
    def __init__(self, session_end: date) -> None:
        self.days = _sessions(session_end, 260)
        self.calls: list[str] = []
        self.fail: set[str] = set()

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        if any(marker in url for marker in self.fail):
            raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", status_code=403)
        if "sidemenu_new" in url:
            return TREE
        if "ulist.np" in url:
            codes = [part.split(".")[1] for part in url.split("secids=")[1].split("%2C")]
            rows = [{"f12": code, "f14": code, "f3": 1.5, "f2": 900, "f104": 2} for code in codes]
            return json.dumps({"data": {"diff": rows}}).encode()
        if "kline" in url:
            slope = {"1.000300": 0.1, "90.BK1201": 0.4, "90.BK1036": -0.2}
            secid = url.split("secid=")[1].split("&")[0]
            return _kline_payload(_bars(self.days, slope[secid], wave=0.5))
        raise ProviderError(code="UPSTREAM_ERROR", message="本测试不提供这个接口", status_code=503)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise AssertionError("not used")


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


FRIDAY = date(2026, 10, 16)


def test_snapshot_only_after_close_or_before_next_open() -> None:
    assert snapshot_allowed(_at(FRIDAY, 15, 30), FRIDAY)
    assert not snapshot_allowed(_at(FRIDAY, 14, 0), FRIDAY)
    assert snapshot_allowed(_at(date(2026, 10, 17), 11, 0), FRIDAY)
    assert snapshot_allowed(_at(date(2026, 10, 19), 8, 0), FRIDAY)
    assert not snapshot_allowed(_at(date(2026, 10, 19), 10, 0), FRIDAY)


def test_sync_writes_snapshot_and_bars_then_is_incremental() -> None:
    store = MemoryStore()
    upstream = Upstream(FRIDAY)
    written = asyncio.run(sync_board_market(store, upstream, _at(FRIDAY, 15, 45)))
    assert written > 0
    assert {code for code, _ in store.snaps} == {"BK1201", "BK1036", "BK1157"}
    assert set(store.bar_rows) == {BENCHMARK_SECID, "90.BK1201", "90.BK1036"}
    assert store.latest_snapshot_date() == FRIDAY

    upstream.calls.clear()
    asyncio.run(sync_board_market(store, upstream, _at(FRIDAY, 16, 30)))
    board_klines = [url for url in upstream.calls if "kline" in url and "90.BK" in url]
    assert board_klines == []


def test_sync_skips_snapshot_when_no_new_session() -> None:
    store = MemoryStore()
    asyncio.run(sync_board_market(store, Upstream(FRIDAY), _at(date(2026, 10, 19), 10, 0)))
    assert store.snaps == {}
    assert store.bar_rows["90.BK1201"]


def test_sync_stops_after_consecutive_failures_and_reports_partial() -> None:
    store = MemoryStore()
    upstream = Upstream(FRIDAY)
    upstream.fail.add("secid=90.BK")
    with pytest.raises(ProviderError) as caught:
        asyncio.run(sync_board_market(store, upstream, _at(FRIDAY, 15, 45)))
    assert caught.value.code == "PARTIAL_SYNC"
    assert store.latest_snapshot_date() == FRIDAY
    assert BENCHMARK_SECID in store.bar_rows


def test_sync_without_benchmark_is_unavailable() -> None:
    upstream = Upstream(FRIDAY)
    upstream.fail.add("1.000300")
    with pytest.raises(ProviderError):
        asyncio.run(sync_board_market(MemoryStore(), upstream, _at(FRIDAY, 15, 45)))


def _filled_store() -> MemoryStore:
    store = MemoryStore()
    asyncio.run(sync_board_market(store, Upstream(FRIDAY), _at(FRIDAY, 15, 45)))
    return store


def _service(upstream: Upstream, store: MemoryStore, now: datetime) -> IndustryMarketService:
    return IndustryMarketService(
        gateway=upstream,
        cache=TtlCache(),
        today=lambda: now.astimezone(SHANGHAI).date(),
        now=lambda: now,
        store=store,
    )


def test_overview_falls_back_to_stored_snapshot_and_supports_as_of() -> None:
    store = _filled_store()
    upstream = Upstream(FRIDAY)
    upstream.fail.add("ulist.np")
    service = _service(upstream, store, _at(date(2026, 10, 19), 10, 0))
    overview = asyncio.run(service.overview("1"))
    assert overview.section.status == "stale" and overview.section.origin == "stored"
    assert overview.section.data_date == FRIDAY
    assert [board.code for board in overview.boards] == ["BK1201"]

    history = asyncio.run(service.overview("concept", as_of=date(2026, 10, 18)))
    assert history.section.status == "ok" and history.section.data_date == FRIDAY
    with pytest.raises(ProviderError) as caught:
        asyncio.run(service.overview("1", as_of=date(2026, 10, 1)))
    assert caught.value.status_code == 404


def test_trend_reads_store_outside_session_and_live_during_session() -> None:
    store = _filled_store()
    upstream = Upstream(FRIDAY)
    weekend = _service(upstream, store, _at(date(2026, 10, 17), 10, 0))
    trend = asyncio.run(weekend.detail("BK1036")).trend
    assert trend.section.origin == "stored" and trend.section.status == "ok"
    assert not any("kline" in url for url in upstream.calls)

    upstream.calls.clear()
    session = _service(upstream, store, _at(date(2026, 10, 19), 10, 0))
    upstream.fail.add("kline")
    trend = asyncio.run(session.detail("BK1036")).trend
    assert any("kline" in url for url in upstream.calls)
    assert trend.section.status == "stale" and trend.section.origin == "stored"
    assert trend.bars


def test_detail_quote_falls_back_to_stored_snapshot() -> None:
    store = _filled_store()
    upstream = Upstream(FRIDAY)
    upstream.fail.add("ulist.np")
    detail = asyncio.run(_service(upstream, store, _at(date(2026, 10, 17), 10, 0)).detail("BK1201"))
    assert detail.quote_section.origin == "stored" and detail.quote_section.status == "stale"
    assert detail.board.change_pct == Decimal("1.5")


def test_rotation_uses_stored_bars_and_explains_empty_store() -> None:
    upstream = Upstream(FRIDAY)
    now = _at(date(2026, 10, 17), 10, 0)
    empty = asyncio.run(_service(upstream, MemoryStore(), now).rotation("1"))
    assert empty.section.status == "unavailable" and empty.series == []

    view = asyncio.run(_service(upstream, _filled_store(), now).rotation("2"))
    assert view.section.status == "ok" and view.section.data_date == FRIDAY
    (series,) = view.series
    assert series.code == "BK1036" and len(series.tail) == 8
    assert series.tail[-1].trade_date == FRIDAY
    with pytest.raises(ProviderError):
        asyncio.run(_service(upstream, MemoryStore(), now).rotation("3"))


def test_rotation_tail_separates_leaders_from_laggards() -> None:
    days = _sessions(FRIDAY, 120)
    bench = _bars(days, 0.1, wave=0.3)
    leader = _bars(days, 0.1 + 0.002 * 0, wave=0.3)
    accelerating = tuple(
        em.BoardBar(
            bar.trade_date,
            bar.open,
            bar.close * (1 + Decimal(i) / 2000) ** 2,
            bar.high,
            bar.low,
            bar.volume,
            bar.amount,
        )
        for i, bar in enumerate(leader)
    )
    tail = rules.rotation_tail(accelerating, bench, window=20, step=5, points=4)
    assert len(tail) == 4 and tail[-1].rs_ratio > 100
    assert rules.rotation_tail(bench[:30], bench, window=20, step=5, points=4) == []


def test_rotation_quadrants_and_expected_session() -> None:
    assert rules.rotation_quadrant(Decimal(101), Decimal(101)) == "领先"
    assert rules.rotation_quadrant(Decimal(101), Decimal(99)) == "转弱"
    assert rules.rotation_quadrant(Decimal(99), Decimal(99)) == "落后"
    assert rules.rotation_quadrant(Decimal(99), Decimal(101)) == "改善"
    assert expected_session(_at(FRIDAY, 16, 0)) == FRIDAY
    assert expected_session(_at(FRIDAY, 10, 0)) == date(2026, 10, 15)
    assert expected_session(_at(date(2026, 10, 18), 10, 0)) == FRIDAY
