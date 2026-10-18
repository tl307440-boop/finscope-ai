"""自定义数据分析同步：增量、质量闸门、连续失败即停。内存仓库 + 假网关。"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import pytest
from app.providers.errors import ProviderError
from app.services import custom_analysis_sync as sync
from app.services.custom_analysis_rules import INDEX_CODES
from tests.unit.custom_analysis_fakes import (
    NOW,
    TODAY,
    FakeGateway,
    MemoryStore,
    build_gateway,
    cross_section_payload,
    failing,
    treasury_payload,
    weekdays,
)

CALENDAR = weekdays(TODAY, 6)


@pytest.fixture(autouse=True)
def no_real_pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """重试退避不真睡，同时记下每次想睡多久。"""

    pauses: list[float] = []

    async def record(seconds: float) -> None:
        pauses.append(seconds)

    async def skip(seconds: float) -> None:
        return None

    monkeypatch.setattr(sync, "_pause", record)
    monkeypatch.setattr(sync, "_throttle", skip)
    return pauses


def _run(store: MemoryStore, gateway: FakeGateway, **kwargs: object) -> sync.SyncReport:
    return asyncio.run(sync.sync_custom_analysis(store, gateway, NOW, **kwargs))  # type: ignore[arg-type]


def test_happy_path_writes_every_source_with_provenance() -> None:
    store = MemoryStore()
    report = _run(store, build_gateway(CALENDAR), years=1)

    assert report.failures == []
    for code in INDEX_CODES:
        assert store.series_dates(sync.index_close_series(code)) == set(CALENDAR)
    assert store.series_dates(sync.index_pe_series("000300")) == set(CALENDAR)
    assert store.series_dates(sync.SERIES_YIELD) == set(CALENDAR)
    assert len(store.series_dates(sync.SERIES_GDP)) == 6
    # 期权：三家交易所每天都有
    for exchange in ("SSE", "SZSE", "CFFEX"):
        assert store.option_days(exchange) == set(CALENDAR)
    assert ("SSE", "510050", TODAY) in store.options
    assert ("CFFEX", "MO", TODAY) in store.options
    # 全市场截面：总市值与两条中位数 PB
    assert store.series_dates(sync.SERIES_MV) == set(CALENDAR)
    assert store.series_dates(sync.SERIES_PB) == set(CALENDAR)
    assert store.series_dates(sync.SERIES_PB_ALL) == set(CALENDAR)

    point, _ = store.points[(sync.SERIES_PB, TODAY)]
    assert point.source == "eastmoney_valuation_cross_section"
    assert point.meta is not None
    assert point.meta["st_basis"] == "snapshot_name"  # 当天抓到的简称就是当天简称
    assert point.meta["excluded_st"] > 0
    older, _ = store.points[(sync.SERIES_PB, CALENDAR[0])]
    assert older.meta is not None
    assert older.meta["st_basis"] == "current_name"  # 历史日期的 ST 只能按当前简称近似


def test_gdp_is_stored_as_cumulative_with_unit_and_basis() -> None:
    store = MemoryStore()
    _run(store, build_gateway(CALENDAR), years=1)
    point, _ = store.points[(sync.SERIES_GDP, date(2026, 6, 30))]
    assert point.value == Decimal("700000")
    assert point.meta == {"label": "2026年第1-2季度", "unit": "亿元", "basis": "年内累计"}


def test_second_run_does_not_refetch_known_days() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    _run(store, gateway, years=1)
    gateway.calls.clear()

    report = _run(store, gateway, years=1)

    assert report.failures == []
    assert gateway.count("query.sse.com.cn") == 0
    assert gateway.count("szse.cn") == 0
    assert gateway.count("cffex.com.cn") == 0
    assert gateway.count("RPT_VALUEANALYSIS_DET") == 0
    assert gateway.count("RPT_F10_ORG_BASICINFO") == 0  # 没有待补截面就不拉上市日期


def test_cross_section_budget_is_spent_newest_first() -> None:
    store = MemoryStore()
    _run(store, build_gateway(CALENDAR), years=1, max_cross_section_days=2)
    assert store.series_dates(sync.SERIES_MV) == set(CALENDAR[-2:])


def test_three_consecutive_failures_stop_that_exchange_only() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    gateway.routes.insert(0, ("query.sse.com.cn", failing()))

    report = _run(store, gateway, years=1)

    # 3 个不同的交易日，每个日子重试到第 3 次才算失败
    assert gateway.count("query.sse.com.cn") == 3 * sync.FETCH_ATTEMPTS
    assert any("SSE" in item for item in report.failures)
    assert store.option_days("SSE") == set()
    # 其他来源不受影响
    assert store.option_days("SZSE") == set(CALENDAR)
    assert store.option_days("CFFEX") == set(CALENDAR)


def test_cffex_missing_file_is_no_data_not_a_failure() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    gateway.routes.insert(0, ("cffex.com.cn", failing("UPSTREAM_ERROR", 404)))

    report = _run(store, gateway, years=1)

    assert report.failures == []
    assert store.option_days("CFFEX") == set()


def test_incomplete_cross_section_is_not_stored() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    gateway.routes.insert(
        0,
        (
            "RPT_VALUEANALYSIS_DET",
            lambda url: cross_section_payload(TODAY, stocks=2000),
        ),
    )

    _run(store, gateway, years=1)

    assert store.series_dates(sync.SERIES_MV) == set()
    assert store.series_dates(sync.SERIES_PB) == set()


def test_option_rows_that_fail_reconciliation_are_kept_but_flagged() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)

    def skewed(url: str) -> bytes:
        day = url.split("tradeDate=")[1][:8]
        text = f"{day[:4]}-{day[4:6]}-{day[6:]}"
        row = {
            "SECURITY_CODE": "510050",
            "SECURITY_ABBR": "上证50ETF",
            "TRADE_DATE": text,
            "PUT_VOLUME": "400",
            "CALL_VOLUME": "600",
            "LEAVES_PUT_QTY": "500",
            "LEAVES_CALL_QTY": "800",
            "CP_RATE": "90.00",  # 与 400/600 = 66.67% 对不上
            "TOTAL_VOLUME": "1,000",
        }
        import json

        return ("cb(" + json.dumps({"result": [row]}) + ")").encode()

    gateway.routes.insert(0, ("query.sse.com.cn", skewed))

    _run(store, gateway, years=1)

    stored = store.options[("SSE", "510050", TODAY)]
    assert stored.quality.flag == "review"
    assert "不一致" in (stored.quality.note or "")


def test_large_day_over_day_jump_is_marked_for_review_without_changing_the_value() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    # 最后一天的沪深300 PE 比前一天高出一倍
    original = gateway.routes[0][1]

    def spiky(url: str) -> bytes:
        payload = original(url)
        if "indexCode=000300" not in url:
            return payload
        import json

        document = json.loads(payload)
        document["data"][-1]["peg"] = 30.0
        return json.dumps(document).encode()

    gateway.routes[0] = ("csindex.com.cn", spiky)

    _run(store, gateway, years=1)

    point, _ = store.points[(sync.index_pe_series("000300"), TODAY)]
    assert point.value == Decimal("30.0")
    assert point.quality_flag == "review"
    assert point.meta is not None
    assert "单日变化" in str(point.meta["note"])


def test_transient_disconnect_is_retried_with_backoff_and_then_succeeds(
    no_real_pauses: list[float],
) -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    healthy = gateway.routes[[needle for needle, _ in gateway.routes].index("szse.cn")][1]
    state = {"drops": 2}

    def flaky(url: str) -> bytes:
        if state["drops"] > 0:
            state["drops"] -= 1
            raise ProviderError(
                code="UPSTREAM_DISCONNECTED", message="上游连接中断", status_code=502
            )
        return healthy(url)

    gateway.routes.insert(0, ("szse.cn", flaky))

    report = _run(store, gateway, years=1)

    assert report.failures == []
    assert store.option_days("SZSE") == set(CALENDAR)
    assert no_real_pauses == [sync.RETRY_PAUSE_SECONDS, sync.RETRY_PAUSE_SECONDS * 2]


def test_csindex_phantom_row_on_a_non_trading_start_date_never_reaches_the_calendar() -> None:
    store = MemoryStore()
    # 假接口与真实接口一样：startDate 不是交易日时回显一条同日期的幽灵行
    gateway = build_gateway(CALENDAR)

    _run(store, gateway, years=1)

    for code in INDEX_CODES:
        dates = store.series_dates(sync.index_close_series(code))
        assert dates == set(CALENDAR)
        assert all(day.weekday() < 5 for day in dates)


def test_phantom_row_is_filtered_even_when_it_would_land_on_a_weekday_before_the_window() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    original = gateway.routes[0][1]

    def with_early_phantom(url: str) -> bytes:
        import json

        document = json.loads(original(url))
        document["data"].insert(
            0,
            {
                "tradeDate": "20200101",
                "indexCode": url.split("indexCode=")[1][:6],
                "close": 1,
                "peg": 9,
            },
        )
        return json.dumps(document).encode()

    gateway.routes[0] = ("csindex.com.cn", with_early_phantom)

    _run(store, gateway, years=1)

    assert date(2020, 1, 1) not in store.series_dates(sync.index_close_series("000300"))


def test_treasury_yield_is_paged_until_the_five_year_window_is_covered() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    long_history = weekdays(TODAY, 1400)  # 约 5.4 年，3 页：500 + 500 + 400
    gateway.routes.insert(
        0,
        (
            "RPTA_WEB_TREASURYYIELD",
            lambda url: treasury_payload(long_history, int(url.split("pageNumber=")[1][:1])),
        ),
    )

    _run(store, gateway, years=1)

    assert gateway.count("RPTA_WEB_TREASURYYIELD") == 3
    stored = store.series_dates(sync.SERIES_YIELD)
    assert len(stored) == 1400
    assert min(stored) == long_history[0]


def test_treasury_yield_stops_paging_once_the_window_is_covered() -> None:
    store = MemoryStore()
    gateway = build_gateway(CALENDAR)
    gateway.routes.insert(
        0,
        (
            "RPTA_WEB_TREASURYYIELD",
            lambda url: treasury_payload(
                weekdays(TODAY, 3000), int(url.split("pageNumber=")[1][:2].rstrip("&"))
            ),
        ),
    )

    _run(store, gateway, years=1)

    # 5 年 ≈ 1300 个交易日 → 第 3 页（第 1001–1500 行）已越过窗口起点，不再翻第 4 页
    assert gateway.count("RPTA_WEB_TREASURYYIELD") == 3


def test_szse_requests_are_spaced_out_but_other_exchanges_are_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(sync, "_throttle", record)
    store = MemoryStore()

    _run(store, build_gateway(CALENDAR), years=1)

    # 只有深交所每个交易日后多等一次；上交所 / 中金所不额外限速
    assert waits == [sync._EXTRA_INTERVAL["SZSE"]] * len(CALENDAR)
