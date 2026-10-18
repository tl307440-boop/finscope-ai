"""自定义数据分析读取侧：PCR 视图、三项估值、数据源状态与 CSV 导出。"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from app.providers.exchange_options import OptionStat
from app.repositories.custom_analysis import SeriesPointIn
from app.services import custom_analysis_sync as sync
from app.services.custom_analysis import (
    CustomAnalysisService,
    pcr_csv,
    valuation_csv,
)
from app.services.custom_analysis_rules import QualityResult
from tests.unit.custom_analysis_fakes import NOW, TODAY, MemoryStore, weekdays

D = Decimal
SRC = "test"


def _service(store: MemoryStore, today: date = TODAY) -> CustomAnalysisService:
    return CustomAnalysisService(store, today=lambda: today)


def _put(
    store: MemoryStore, code: str, day: date, value: Decimal | float | int, **meta: object
) -> None:
    store.upsert_points(code, [SeriesPointIn(day, D(str(value)), SRC, "ok", meta or None)], NOW)


def _seed_gdp(store: MemoryStore) -> None:
    for year in range(2021, 2026):
        for month, cumulative in ((3, 100), (6, 200), (9, 300), (12, 400)):
            last = 30 if month in (6, 9) else 31
            _put(store, sync.SERIES_GDP, date(year, month, last), cumulative)
    _put(store, sync.SERIES_GDP, date(2026, 3, 31), 110)
    _put(store, sync.SERIES_GDP, date(2026, 6, 30), 230)


def _option(
    exchange: str, code: str, day: date, call: int, put: int
) -> tuple[OptionStat, QualityResult]:
    stat = OptionStat(
        trade_date=day,
        exchange=exchange,  # type: ignore[arg-type]
        underlying_code=code,
        underlying_name=None,
        call_volume=call,
        put_volume=put,
        call_oi=call * 2,
        put_oi=put * 2,
        reported_ratio=None,
        reported_ratio_kind=None,
        reported_total_volume=None,
        source=SRC,
    )
    return stat, QualityResult("ok")


# ---------------------------------------------------------------- 股债收益差


def test_erp_matches_formula_and_flags_opportunity_zone_when_above_the_band() -> None:
    store = MemoryStore()
    days = weekdays(TODAY, 900)
    for index, day in enumerate(days):
        _put(store, sync.index_pe_series("000300"), day, "12.5")
        # 国债收益率在 1.50–1.59 之间摆动，最后一天骤降到 0.50 → ERP 远高于通道上沿
        _put(
            store,
            sync.SERIES_YIELD,
            day,
            "0.50" if day == TODAY else f"{1.5 + (index % 10) / 100:.2f}",
        )

    view = _service(store).valuation().indicators[0]

    assert view.code == "erp"
    assert view.status == "ok"
    latest = view.latest
    assert latest is not None
    assert latest.value == 7.5  # 100/12.5 − 0.50
    assert latest.extras["pe_ttm"] == 12.5
    assert latest.extras["yield_10y_pct"] == 0.5
    assert latest.percentile_5y == 100.0
    assert latest.band_upper is not None and latest.value > latest.band_upper
    assert view.zone == "机会极值区（≥ μ+2σ）"
    assert view.percentile_note is None


def test_erp_needs_both_inputs_on_the_same_day() -> None:
    store = MemoryStore()
    _put(store, sync.index_pe_series("000300"), date(2026, 10, 15), "12.5")
    _put(store, sync.SERIES_YIELD, date(2026, 10, 14), "1.65")

    view = _service(store).valuation().indicators[0]

    assert view.status == "unavailable"
    assert view.latest is None
    assert view.reason is not None
    assert [item.name for item in view.inputs] == ["沪深300 PE-TTM", "10 年期国债收益率"]


def test_short_history_gives_values_but_no_percentile() -> None:
    store = MemoryStore()
    for day in weekdays(TODAY, 100):
        _put(store, sync.index_pe_series("000300"), day, "12.5")
        _put(store, sync.SERIES_YIELD, day, "1.65")

    view = _service(store).valuation().indicators[0]

    assert view.status == "ok"
    assert view.latest is not None
    assert view.latest.percentile_5y is None
    assert view.latest.band_upper is None
    assert view.zone is None
    assert view.percentile_note is not None and "100" in view.percentile_note


def test_stale_inputs_are_reported_as_stale_not_hidden() -> None:
    store = MemoryStore()
    for day in weekdays(date(2026, 10, 1), 30):
        _put(store, sync.index_pe_series("000300"), day, "12.5")
        _put(store, sync.SERIES_YIELD, day, "1.65")

    view = _service(store).valuation().indicators[0]

    assert view.status == "stale"
    assert view.reason is not None and "已过期" in view.reason
    assert view.latest is not None  # 过期也照常给最近值，只是显著标注


# ---------------------------------------------------------------- 巴菲特指标


def test_buffett_uses_trailing_four_published_quarters_and_converts_units() -> None:
    store = MemoryStore()
    _seed_gdp(store)
    for day in weekdays(TODAY, 300):
        # 4.3e10 元市值；2025Q3+Q4+2026Q1+Q2 = 100+100+110+120 = 430 亿元 → 100%
        _put(store, sync.SERIES_MV, day, "43000000000", stock_count=5500)

    view = _service(store).valuation().indicators[1]

    assert view.code == "buffett"
    assert view.status == "ok"
    assert view.latest is not None
    assert view.latest.value == 100.0
    # 2026 年 Q3 要到 10-21 才视为公布，2026-10-16 还不能用
    assert view.latest.extras["gdp_quarter_end"] == "2026-06-30"
    assert view.latest.extras["stock_count"] == 5500
    assert any("不做插值" in note for note in view.notes)


def test_buffett_switches_to_the_new_quarter_only_after_the_release_lag() -> None:
    store = MemoryStore()
    _seed_gdp(store)
    _put(store, sync.SERIES_GDP, date(2026, 9, 30), 380)  # Q3 单季 150
    _put(store, sync.SERIES_MV, date(2026, 10, 20), "43000000000")
    _put(store, sync.SERIES_MV, date(2026, 10, 21), "43000000000")

    view = _service(store, today=date(2026, 10, 21)).valuation().indicators[1]

    points = {point.trade_date: point for point in view.points}
    assert points[date(2026, 10, 20)].extras["gdp_quarter_end"] == "2026-06-30"
    assert points[date(2026, 10, 21)].extras["gdp_quarter_end"] == "2026-09-30"


def test_buffett_is_unavailable_without_gdp() -> None:
    store = MemoryStore()
    _put(store, sync.SERIES_MV, TODAY, "43000000000")

    view = _service(store).valuation().indicators[1]

    assert view.status == "unavailable"
    assert view.reason is not None and "GDP" in view.reason


# ---------------------------------------------------------------- 中位数 PB


def test_median_pb_exposes_cleaning_counts_and_st_basis() -> None:
    store = MemoryStore()
    days = weekdays(TODAY, 800)
    for index, day in enumerate(days):
        value = "3.00" if day == TODAY else f"{1.0 + (index % 20) / 20:.2f}"
        _put(
            store,
            sync.SERIES_PB,
            day,
            value,
            universe=5000,
            sample=4500,
            excluded_negative=60,
            excluded_st=150,
            excluded_new=30,
            listing_unknown=5,
            st_basis="current_name",
        )
        _put(store, sync.SERIES_PB_ALL, day, "1.5")

    view = _service(store).valuation().indicators[2]

    assert view.code == "median_pb"
    latest = view.latest
    assert latest is not None
    assert latest.value == 3.0
    assert latest.percentile_5y == 100.0
    assert view.zone == "偏贵区（> 80 分位）"
    assert latest.extras["sample"] == 4500
    assert latest.extras["excluded_st"] == 150
    assert latest.extras["st_basis"] == "current_name"
    assert latest.extras["median_without_st_filter"] == 1.5


def test_valuation_never_invents_values_when_nothing_is_synced() -> None:
    view = _service(MemoryStore()).valuation()
    assert [item.status for item in view.indicators] == ["unavailable"] * 3
    assert all(item.latest is None and item.points == [] for item in view.indicators)


# ---------------------------------------------------------------- PCR


def _seed_options(store: MemoryStore, count: int, *, skip_szse_on: date | None = None) -> None:
    rows: list[tuple[OptionStat, QualityResult]] = []
    for index, day in enumerate(weekdays(TODAY, count)):
        put = 900 + index % 50
        rows.append(_option("SSE", "510300", day, 1000, put))
        if day != skip_szse_on:
            rows.append(_option("SZSE", "159919", day, 500, 100))
        rows.append(_option("CFFEX", "IO", day, 200, 100))
        rows.append(_option("CFFEX", "MO", day, 300, 330))
    store.upsert_option_stats(rows, NOW)
    for day in weekdays(TODAY, count):
        _put(store, sync.index_close_series("000300"), day, "4170.61")


def test_pcr_sums_the_etf_basket_before_dividing_and_reports_signals() -> None:
    store = MemoryStore()
    _seed_options(store, 800)

    view = _service(store).pcr("csi300")

    assert view is not None
    etf = next(basket for basket in view.baskets if basket.kind == "etf")
    assert etf.status == "ok"
    assert etf.members == ["上交所 510300", "深交所 159919"]
    latest = etf.latest
    assert latest is not None
    # 最后一天：上交所认沽 900 + 49，深交所认沽 100，认购 1000 + 500
    assert (latest.call_volume, latest.put_volume) == (1500, 1049)
    assert latest.volume_pcr == round(1049 / 1500, 4)
    assert latest.oi_pcr == round(latest.put_oi / latest.call_oi, 4)
    assert latest.index_close == 4170.61
    assert latest.sentiment_signal == "正常"
    assert latest.volume_pcr_percentile_5y is not None
    assert latest.volume_pcr_ma10 is not None
    index_basket = next(basket for basket in view.baskets if basket.kind == "index")
    assert index_basket.latest is not None
    assert index_basket.latest.volume_pcr == 0.5
    assert index_basket.latest.sentiment_signal == "极度贪婪"


def test_pcr_drops_days_missing_a_basket_member_and_says_so() -> None:
    store = MemoryStore()
    skipped = weekdays(TODAY, 5)[0]
    _seed_options(store, 30, skip_szse_on=skipped)

    view = _service(store).pcr("csi300")

    assert view is not None
    etf = next(basket for basket in view.baskets if basket.kind == "etf")
    assert etf.incomplete_days == 1
    assert skipped not in {point.trade_date for point in etf.points}
    assert etf.latest is not None and etf.latest.volume_pcr_percentile_5y is None
    assert etf.reason is not None and "分位" in etf.reason


def test_pcr_csi1000_is_cffex_only_and_documents_why() -> None:
    store = MemoryStore()
    _seed_options(store, 30)

    view = _service(store).pcr("csi1000")

    assert view is not None
    assert [basket.kind for basket in view.baskets] == ["index"]
    assert "512100" in view.baskets[0].note


def test_pcr_unknown_group_and_empty_store() -> None:
    assert _service(MemoryStore()).pcr("nope") is None
    view = _service(MemoryStore()).pcr("sse50")
    assert view is not None
    assert all(basket.status == "unavailable" and basket.points == [] for basket in view.baskets)


def test_pcr_limits_returned_points_but_not_the_percentile_window() -> None:
    store = MemoryStore()
    _seed_options(store, 800)

    view = _service(store).pcr("csi300", days=60)

    assert view is not None
    etf = next(basket for basket in view.baskets if basket.kind == "etf")
    assert len(etf.points) == 60
    assert etf.latest is not None and etf.latest.volume_pcr_percentile_5y is not None


# ---------------------------------------------------------------- 数据源与导出


def test_sources_report_ok_stale_and_missing_with_review_notes() -> None:
    store = MemoryStore()
    stat, _ = _option("SSE", "510050", TODAY, 600, 400)
    store.upsert_option_stats([(stat, QualityResult("review", "自算比值与披露不一致"))], NOW)
    _put(store, sync.SERIES_YIELD, date(2026, 9, 1), "1.65")

    view = _service(store).sources()

    by_key = {row.key: row for row in view.sources}
    assert by_key["SSE"].status == "ok" and by_key["SSE"].review_rows == 1
    assert by_key[sync.SERIES_YIELD].status == "stale"
    assert by_key["SZSE"].status == "unavailable"
    assert any("不一致" in issue.note for issue in view.recent_issues)


def test_pcr_csv_follows_the_prd_columns() -> None:
    store = MemoryStore()
    _seed_options(store, 30)
    view = _service(store).pcr("csi300")
    assert view is not None

    text = pcr_csv(view, "etf")

    assert text is not None
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:9] == [
        "trade_date",
        "underlying_code",
        "underlying_close",
        "put_volume_sum",
        "call_volume_sum",
        "volume_pcr",
        "volume_pcr_ma10",
        "oi_pcr",
        "sentiment_signal",
    ]
    assert rows[1][1] == "510300+159919"
    assert pcr_csv(view, "nonexistent") is None


def test_valuation_csv_leaves_gaps_blank_instead_of_filling() -> None:
    store = MemoryStore()
    for day in weekdays(TODAY, 10):
        _put(store, sync.index_pe_series("000300"), day, "12.5")
        _put(store, sync.SERIES_YIELD, day, "1.65")
    _put(store, sync.SERIES_PB, TODAY, "1.6", sample=1)
    view = _service(store).valuation()

    rows = list(csv.DictReader(io.StringIO(valuation_csv(view))))

    assert list(rows[0]) == [
        "trade_date",
        "csi300_pe_ttm",
        "cn_10yr_yield",
        "erp_value",
        "erp_percentile_5y",
        "buffett_indicator",
        "buffett_percentile_5y",
        "market_median_pb",
        "market_pb_percentile_5y",
    ]
    last = rows[-1]
    assert last["trade_date"] == TODAY.isoformat()
    assert last["market_median_pb"] == "1.6"
    assert last["buffett_indicator"] == ""
    first = rows[0]
    assert first["market_median_pb"] == ""
    assert first["erp_value"] == "6.35"
