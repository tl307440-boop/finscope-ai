"""自定义数据分析的同步：期权日统计、指数收盘与 PE、国债收益率、GDP、全市场截面。

约定（与 board_market_sync 一致）：
- 一律走限速网关，连续 3 次上游失败就停，已写入的保留，整体按任务重试策略重来；
- 交易日以中证指数沪深300 日行情的日期为准，周末 / 节假日不请求；
  指数收盘经常晚于 18:30 才发布，期权在日历末尾再试最近几个工作日，空结果不入库；
- 可重复运行：已入库的交易日不重抓（近 7 天的指数行情与国债收益率重抓一遍以吸收修正）；
- 质量闸门在入库前执行，被拒绝的行不入库，标待审的行入库并带 quality_note。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from app.providers import csindex
from app.providers import eastmoney_rates as rates
from app.providers import eastmoney_valuation as valuation
from app.providers import exchange_options as options
from app.providers.errors import ProviderError
from app.repositories.custom_analysis import SeriesPointIn, StoredPoint
from app.services.custom_analysis_rules import (
    INDEX_CODES,
    WINDOW_YEARS,
    QualityResult,
    check_cross_section,
    check_jump,
    check_option_stat,
    check_pe,
    check_yield,
    compute_median_pb,
    years_back,
)
from app.utils.market_time import SHANGHAI

logger = logging.getLogger(__name__)

SERIES_YIELD = "CN_10Y_YIELD"
SERIES_GDP = "CN_GDP_CUM"
SERIES_MV = "A_TOTAL_MV"
SERIES_PB = "A_MEDIAN_PB"
SERIES_PB_ALL = "A_MEDIAN_PB_NO_ST_FILTER"
CALENDAR_INDEX = "000300"
MAX_CONSECUTIVE_FAILURES = 3
RECHECK_DAYS = 7
MAX_YIELD_PAGES = 12
FETCH_ATTEMPTS = 3
RETRY_PAUSE_SECONDS = 4.0
# 深交所对短时间内的连续请求会直接掐连接（实测 0.4 秒间隔约 60 个请求后开始，封一段时间；
# 1.5 秒间隔同样会被封）。这是对方的限流，不绕：放慢到约 3.4 秒/请求，被封就停，下次续跑。
_EXTRA_INTERVAL = {"SZSE": 3.0}
DEFAULT_CROSS_SECTION_DAYS = 60
# 各交易所期权统计的最早可用日（再往前没有数据，不浪费请求）。
_OPTION_START = {
    "SSE": date(2015, 2, 9),
    "SZSE": date(2019, 12, 23),
    "CFFEX": date(2019, 12, 23),
}
_JUMP_LIMITS = {SERIES_MV: Decimal("0.15"), SERIES_PB: Decimal("0.12")}
# 指数行情没跟上时，期权最多再向前试这么多个工作日。空文件不入库，下次还会再试；
# 封顶是为了长假期间不要对着深交所把同一天反复打满。
OPTION_TAIL_WEEKDAYS = 3


def index_close_series(index_code: str) -> str:
    return f"IDX_CLOSE_{index_code}"


def index_pe_series(index_code: str) -> str:
    return f"IDX_PE_TTM_{index_code}"


class Gateway(Protocol):
    async def get(self, url: str, headers: Mapping[str, str]) -> bytes: ...


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _throttle(seconds: float) -> None:
    """常规请求间隔（区别于失败后的退避），单独成函数，测试里可以直接关掉。"""

    await asyncio.sleep(seconds)


async def _get_with_retry(gateway: Gateway, url: str, headers: Mapping[str, str]) -> bytes:
    """上游偶发断连时先退避重试同一个请求；404 是确定的「没有数据」，不重试。"""

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return await gateway.get(url, headers)
        except ProviderError as error:
            if error.status_code == 404 or attempt == FETCH_ATTEMPTS:
                raise
            logger.info("retry %s/%s after %s: %s", attempt, FETCH_ATTEMPTS, error.message, url)
            await _pause(RETRY_PAUSE_SECONDS * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


class Store(Protocol):
    def upsert_option_stats(
        self, rows: Sequence[tuple[options.OptionStat, QualityResult]], fetched_at: datetime
    ) -> int: ...

    def option_days(self, exchange: str) -> set[date]: ...

    def upsert_points(
        self, series_code: str, points: Sequence[SeriesPointIn], fetched_at: datetime
    ) -> int: ...

    def series_dates(self, series_code: str) -> set[date]: ...

    def series(self, series_code: str, start: date | None = None) -> list[StoredPoint]: ...


Progress = Callable[[str], None]


@dataclass
class SyncReport:
    rows: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def add(self, key: str, count: int) -> None:
        self.rows[key] = self.rows.get(key, 0) + count

    @property
    def total(self) -> int:
        return sum(self.rows.values())


async def sync_custom_analysis(
    store: Store,
    gateway: Gateway,
    now: datetime,
    *,
    years: int = WINDOW_YEARS,
    max_cross_section_days: int | None = DEFAULT_CROSS_SECTION_DAYS,
    progress: Progress | None = None,
) -> SyncReport:
    """返回各段写入行数；有失败的段会记在 failures 里，由调用方决定是否抛 PARTIAL_SYNC。"""

    say = progress or (lambda message: logger.info("custom-analysis %s", message))
    report = SyncReport()
    today = now.astimezone(SHANGHAI).date()
    start = years_back(today, years)

    steps: tuple[tuple[str, Callable[[], Any]], ...] = (
        ("指数行情", lambda: _sync_indices(store, gateway, now, start, today, report, say)),
        ("国债收益率", lambda: _sync_yield(store, gateway, now, today, report, say)),
        ("GDP", lambda: _sync_gdp(store, gateway, now, report, say)),
        ("期权统计", lambda: _sync_options(store, gateway, now, start, today, report, say)),
        (
            "全市场估值截面",
            lambda: _sync_cross_sections(
                store, gateway, now, today, report, say, max_cross_section_days
            ),
        ),
    )
    for label, step in steps:
        try:
            await step()
        except ProviderError as error:
            report.failures.append(f"{label}：{error.message}")
            logger.warning("custom-analysis %s failed: %s", label, error.message)
    return report


# ------------------------------------------------------------------ 指数行情


async def _sync_indices(
    store: Store,
    gateway: Gateway,
    now: datetime,
    start: date,
    today: date,
    report: SyncReport,
    say: Progress,
) -> None:
    for index_code in INDEX_CODES:
        closes = store.series_dates(index_close_series(index_code))
        begin = start if not closes else max(start, max(closes) - timedelta(days=RECHECK_DAYS))
        days: list[csindex.IndexDay] = []
        for chunk_start, chunk_end in _year_chunks(begin, today):
            # 中证指数接口在 startDate 不是交易日时会原样回显一条同日期的行（实测周六也有），
            # 所以请求时往前多退 7 天，再只保留 chunk_start 之后、且不是周末的行。
            payload = await gateway.get(
                csindex.index_perf_url(index_code, chunk_start - timedelta(days=7), chunk_end),
                csindex.HEADERS,
            )
            days.extend(
                day
                for day in csindex.parse_index_perf(payload, index_code)
                if day.trade_date >= chunk_start and day.trade_date.weekday() < 5
            )
        close_points: list[SeriesPointIn] = []
        pe_points: list[SeriesPointIn] = []
        for day in days:
            if day.close is not None and day.close > 0:
                close_points.append(SeriesPointIn(day.trade_date, day.close, csindex.SOURCE, "ok"))
            if day.pe_ttm is not None:
                quality = check_pe(day.pe_ttm)
                if quality.flag != "rejected":
                    pe_points.append(
                        SeriesPointIn(
                            day.trade_date,
                            day.pe_ttm,
                            csindex.SOURCE,
                            quality.flag,
                            {"note": quality.note} if quality.note else None,
                        )
                    )
        report.add(
            "指数收盘", store.upsert_points(index_close_series(index_code), close_points, now)
        )
        report.add("指数PE", store.upsert_points(index_pe_series(index_code), pe_points, now))
        say(f"指数 {index_code}：{len(close_points)} 个交易日")
    _review_jumps(
        store, index_pe_series(CALENDAR_INDEX), csindex.SOURCE, Decimal("0.08"), "PE-TTM", now
    )


def option_candidate_days(calendar_days: Sequence[date], today: date) -> list[date]:
    """指数交易日，再加日历末尾到 today 之间的少量工作日。

    中证指数当天的收盘经常在定时任务之后才出现。只认已入库的指数日期时，
    交易所已经公布的期权统计也会被整段跳过，页面就停在上一个交易日。
    """

    days = list(calendar_days)
    if not days:
        return []
    cursor = days[-1] + timedelta(days=1)
    extra = 0
    while cursor <= today and extra < OPTION_TAIL_WEEKDAYS:
        if cursor.weekday() < 5:
            days.append(cursor)
            extra += 1
        cursor += timedelta(days=1)
    return days


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=365))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


# ------------------------------------------------------------------ 国债收益率 / GDP


async def _sync_yield(
    store: Store, gateway: Gateway, now: datetime, today: date, report: SyncReport, say: Progress
) -> None:
    stored = store.series_dates(SERIES_YIELD)
    # 东财数据中心单页最多 500 行（pageSize 再大也不生效），首次要翻页直到覆盖 5 年窗口
    # 再多留一点余量；之后只取最近 90 条吸收修正。
    page_size = 90 if stored else 500
    floor = years_back(today, WINDOW_YEARS) - timedelta(days=30)
    collected: list[rates.YieldRow] = []
    for page in range(1, MAX_YIELD_PAGES + 1):
        payload = await gateway.get(rates.treasury_url(page_size, page), rates.DATACENTER_HEADERS)
        rows = rates.parse_treasury(payload)
        collected.extend(rows)
        if stored or len(rows) < page_size or min(row.day for row in rows) <= floor:
            break
    points: list[SeriesPointIn] = []
    for row in collected:
        if row.cn10y is None:
            continue
        quality = check_yield(row.cn10y)
        if quality.flag == "rejected":
            logger.warning("custom-analysis yield rejected %s: %s", row.day, quality.note)
            continue
        points.append(SeriesPointIn(row.day, row.cn10y, valuation.TREASURY_SOURCE, quality.flag))
    report.add("10年国债收益率", store.upsert_points(SERIES_YIELD, points, now))
    say(f"10 年国债收益率：{len(points)} 条")


async def _sync_gdp(
    store: Store, gateway: Gateway, now: datetime, report: SyncReport, say: Progress
) -> None:
    payload = await gateway.get(valuation.gdp_url(), valuation.HEADERS)
    points = [
        SeriesPointIn(
            point.quarter_end,
            point.cumulative_billion_cny,
            valuation.GDP_SOURCE,
            "ok",
            {"label": point.label, "unit": "亿元", "basis": "年内累计"},
        )
        for point in valuation.parse_gdp(payload)
    ]
    report.add("GDP累计值", store.upsert_points(SERIES_GDP, points, now))
    say(f"GDP 累计值：{len(points)} 个报告期")


# ------------------------------------------------------------------ 期权统计


async def _sync_options(
    store: Store,
    gateway: Gateway,
    now: datetime,
    start: date,
    today: date,
    report: SyncReport,
    say: Progress,
) -> None:
    stored_days = sorted(
        day for day in store.series_dates(index_close_series(CALENDAR_INDEX)) if day >= start
    )
    calendar_days = option_candidate_days(stored_days, today)
    if not calendar_days:
        raise ProviderError(
            code="NO_ROWS", message="拿不到沪深300 行情，无法确定交易日", status_code=503
        )
    fetchers: dict[str, tuple[Callable[[date], str], Mapping[str, str], Callable[..., Any]]] = {
        "SSE": (options.sse_url, options.SSE_HEADERS, options.parse_sse),
        "SZSE": (options.szse_url, options.SZSE_HEADERS, options.parse_szse),
        "CFFEX": (options.cffex_url, options.CFFEX_HEADERS, options.parse_cffex),
    }
    for exchange, (build_url, headers, parse) in fetchers.items():
        known = store.option_days(exchange)
        missing = [
            day for day in calendar_days if day >= _OPTION_START[exchange] and day not in known
        ]
        say(f"{exchange} 期权：待抓 {len(missing)} 个交易日")
        failures = 0
        batch: list[tuple[options.OptionStat, QualityResult]] = []
        for index, day in enumerate(missing, start=1):
            try:
                payload = await _get_with_retry(gateway, build_url(day), headers)
                stats = parse(payload, day)
            except ProviderError as error:
                if error.status_code == 404:
                    continue  # 该交易所当天没有文件，按没有数据处理
                failures += 1
                logger.warning("option %s %s failed: %s", exchange, day, error.message)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    report.failures.append(f"{exchange} 期权连续 {failures} 次上游失败，已停止")
                    break
                continue
            failures = 0
            if _EXTRA_INTERVAL.get(exchange):
                await _throttle(_EXTRA_INTERVAL[exchange])
            if not stats and (today - day).days > 3:
                logger.warning("option %s %s returned no rows on a trading day", exchange, day)
            for stat in stats:
                quality = check_option_stat(stat)
                if quality.flag == "rejected":
                    logger.warning(
                        "option rejected %s %s %s: %s",
                        exchange,
                        stat.underlying_code,
                        day,
                        quality.note,
                    )
                batch.append((stat, quality))
            if index % 60 == 0 and batch:
                report.add(f"{exchange}期权", store.upsert_option_stats(batch, now))
                batch = []
                say(f"{exchange} 期权：已处理 {index}/{len(missing)}")
        if batch:
            report.add(f"{exchange}期权", store.upsert_option_stats(batch, now))


# ------------------------------------------------------------------ 全市场截面


async def _sync_cross_sections(
    store: Store,
    gateway: Gateway,
    now: datetime,
    today: date,
    report: SyncReport,
    say: Progress,
    max_days: int | None,
) -> None:
    calendar_days = sorted(store.series_dates(index_close_series(CALENDAR_INDEX)))
    done = store.series_dates(SERIES_MV)
    # 只补最近 5 年窗口内的交易日；最新的先补，页面能最快看到当前值。
    floor = years_back(today, WINDOW_YEARS)
    missing = [day for day in reversed(calendar_days) if day >= floor and day not in done]
    if max_days is not None:
        missing = missing[:max_days]
    if not missing:
        say("全市场截面：无需补抓")
        return
    say(f"全市场截面：待抓 {len(missing)} 个交易日")
    listing = await _fetch_listing_dates(gateway)
    failures = 0
    for index, day in enumerate(missing, start=1):
        try:
            rows, expected = await _fetch_cross_section(gateway, day)
        except ProviderError as error:
            failures += 1
            logger.warning("cross-section %s failed: %s", day, error.message)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                report.failures.append(f"全市场截面连续 {failures} 次上游失败，已停止")
                break
            continue
        failures = 0
        if not rows:
            continue  # 当天还没发布
        quality = check_cross_section(rows, expected)
        if quality.flag == "rejected":
            logger.warning("cross-section %s rejected: %s", day, quality.note)
            continue
        total_cap = sum(
            (row.total_market_cap for row in rows if row.total_market_cap is not None), Decimal(0)
        )
        median = compute_median_pb(rows, day, listing)
        # 当天或前一天抓到的简称就是当时的简称；更早的日期只能用当前简称近似剔除 ST。
        st_basis = "snapshot_name" if (today - day).days <= 1 else "current_name"
        note = {"note": quality.note} if quality.note else {}
        mv_meta: dict[str, Any] = {"stock_count": len(rows), "unit": "元", **note}
        report.add(
            "A股总市值",
            store.upsert_points(
                SERIES_MV,
                [
                    SeriesPointIn(
                        day, total_cap, valuation.CROSS_SECTION_SOURCE, quality.flag, mv_meta
                    )
                ],
                now,
            ),
        )
        pb_meta: dict[str, Any] = {
            "universe": median.universe,
            "sample": median.sample,
            "excluded_missing": median.excluded_missing,
            "excluded_negative": median.excluded_negative,
            "excluded_new": median.excluded_new,
            "excluded_st": median.excluded_st,
            "listing_unknown": median.listing_unknown,
            "st_basis": st_basis,
        }
        if median.median is not None:
            report.add(
                "中位数PB",
                store.upsert_points(
                    SERIES_PB,
                    [
                        SeriesPointIn(
                            day,
                            median.median,
                            valuation.CROSS_SECTION_SOURCE,
                            quality.flag,
                            pb_meta,
                        )
                    ],
                    now,
                ),
            )
        if median.median_without_st_filter is not None:
            store.upsert_points(
                SERIES_PB_ALL,
                [
                    SeriesPointIn(
                        day,
                        median.median_without_st_filter,
                        valuation.CROSS_SECTION_SOURCE,
                        quality.flag,
                        None,
                    )
                ],
                now,
            )
        if index % 20 == 0:
            say(f"全市场截面：已处理 {index}/{len(missing)}")
    for code, limit in _JUMP_LIMITS.items():
        label = "A 股总市值" if code == SERIES_MV else "中位数 PB"
        _review_jumps(store, code, valuation.CROSS_SECTION_SOURCE, limit, label, now)


async def _fetch_cross_section(
    gateway: Gateway, day: date
) -> tuple[list[valuation.StockValuation], int]:
    first = valuation.parse_cross_section(
        await _get_with_retry(gateway, valuation.cross_section_url(day, 1), valuation.HEADERS), day
    )
    rows = list(first.rows)
    for page in range(2, first.pages + 1):
        part = valuation.parse_cross_section(
            await _get_with_retry(
                gateway, valuation.cross_section_url(day, page), valuation.HEADERS
            ),
            day,
        )
        rows.extend(part.rows)
    return rows, first.count


async def _fetch_listing_dates(gateway: Gateway) -> dict[str, date]:
    first, pages = valuation.parse_listing_dates(
        await gateway.get(valuation.listing_url(1), valuation.HEADERS)
    )
    found = dict(first)
    for page in range(2, pages + 1):
        part, _ = valuation.parse_listing_dates(
            await gateway.get(valuation.listing_url(page), valuation.HEADERS)
        )
        found.update(part)
    return found


# ------------------------------------------------------------------ 跳变复核


def _review_jumps(
    store: Store, series_code: str, source: str, limit: Decimal, label: str, now: datetime
) -> int:
    """相邻交易日变化超限的点标成待审（值不动），便于在页面上看到「这个点值得核对」。"""

    points = store.series(series_code)
    updates: list[SeriesPointIn] = []
    previous: Decimal | None = None
    for point in points:
        quality = check_jump(previous, point.value, limit, label)
        wanted = "review" if quality.flag == "review" else "ok"
        # 只改 ok ↔ review，不把别的闸门写进来的 review 降回 ok。
        if wanted == "review" and point.quality_flag == "ok":
            meta = dict(point.meta or {})
            meta["note"] = quality.note
            updates.append(SeriesPointIn(point.trade_date, point.value, source, "review", meta))
        previous = point.value
    return store.upsert_points(series_code, updates, now) if updates else 0
