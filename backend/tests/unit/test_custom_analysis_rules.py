"""自定义数据分析的纯计算：PCR、质量闸门、滚动分位、ERP、巴菲特指标、中位数 PB。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.providers.eastmoney_valuation import StockValuation
from app.providers.exchange_options import OptionStat
from app.services import custom_analysis_rules as rules

D = Decimal


def _stat(**overrides: object) -> OptionStat:
    base: dict[str, object] = {
        "trade_date": date(2026, 10, 16),
        "exchange": "SSE",
        "underlying_code": "510050",
        "underlying_name": "上证50ETF",
        "call_volume": 621_465,
        "put_volume": 426_473,
        "call_oi": 885_490,
        "put_oi": 539_435,
        "reported_ratio": D("68.62"),
        "reported_ratio_kind": "volume_pcr",
        "reported_total_volume": 1_047_938,
        "source": "sse_option_daily_stats",
    }
    base.update(overrides)
    return OptionStat(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- PCR 与质量闸门


def test_put_call_ratio_uses_put_over_call() -> None:
    # 与上交所披露的 CP_RATE 68.62% 一致（四舍五入到两位小数）
    assert rules.put_call_ratio(426_473, 621_465) == D("0.686238")


def test_put_call_ratio_is_none_when_call_is_zero() -> None:
    assert rules.put_call_ratio(100, 0) is None


def test_real_sse_row_reconciles_with_reported_ratio() -> None:
    assert rules.check_option_stat(_stat()).flag == "ok"


def test_szse_oi_ratio_reconciles_with_reported_ratio() -> None:
    stat = _stat(
        exchange="SZSE",
        underlying_code="159919",
        call_volume=91_902,
        put_volume=69_478,
        call_oi=159_399,
        put_oi=85_363,
        reported_ratio=D("53.55"),
        reported_ratio_kind="oi_pcr",
        reported_total_volume=161_380,
    )
    assert rules.check_option_stat(stat).flag == "ok"


def test_mismatch_with_reported_ratio_is_flagged_for_review() -> None:
    result = rules.check_option_stat(_stat(reported_ratio=D("75.00")))
    assert result.flag == "review"
    assert "不一致" in (result.note or "")


def test_total_volume_mismatch_is_flagged_for_review() -> None:
    result = rules.check_option_stat(_stat(reported_total_volume=1_000_000))
    assert result.flag == "review"


def test_negative_or_all_zero_rows_are_rejected() -> None:
    assert rules.check_option_stat(_stat(put_volume=-1)).flag == "rejected"
    zero = _stat(
        call_volume=0,
        put_volume=0,
        call_oi=0,
        put_oi=0,
        reported_ratio=None,
        reported_ratio_kind=None,
        reported_total_volume=None,
    )
    assert rules.check_option_stat(zero).flag == "rejected"


def test_zero_call_volume_cannot_form_a_ratio_and_goes_to_review() -> None:
    stat = _stat(
        call_volume=0,
        put_volume=10,
        reported_ratio=None,
        reported_ratio_kind=None,
        reported_total_volume=None,
    )
    assert rules.check_option_stat(stat).flag == "review"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("1.2001"), "极度恐慌"),
        (D("1.2"), "正常"),
        (D("0.6"), "正常"),
        (D("0.5999"), "极度贪婪"),
        (D("0.9"), "正常"),
        (None, None),
    ],
)
def test_fixed_threshold_signal_boundaries(value: Decimal | None, expected: str | None) -> None:
    assert rules.sentiment_signal(value) == expected


def test_relative_signal_uses_own_percentile() -> None:
    assert rules.relative_signal(95.0) == "偏恐慌"
    assert rules.relative_signal(5.0) == "偏贪婪"
    assert rules.relative_signal(50.0) == "中性"
    assert rules.relative_signal(None) is None


def _row(day: date, exchange: str, code: str, call: int, put: int) -> rules.OptionRow:
    return rules.OptionRow(day, exchange, code, call, put, call * 2, put * 2, "ok")


def test_basket_sums_members_before_dividing_and_drops_incomplete_days() -> None:
    basket = rules.GROUPS_BY_CODE["csi300"].baskets[0]
    d1, d2 = date(2026, 10, 15), date(2026, 10, 16)
    rows = [
        _row(d1, "SSE", "510300", 100, 80),
        _row(d1, "SZSE", "159919", 50, 20),
        # d2 缺深交所：这一天不能拿残缺的和算 PCR
        _row(d2, "SSE", "510300", 100, 80),
    ]
    days, incomplete = rules.combine_basket(rows, basket)
    assert incomplete == 1
    assert [item.trade_date for item in days] == [d1]
    assert (days[0].call_volume, days[0].put_volume) == (150, 100)
    # 合并后的 PCR 不是两个 PCR 的平均：(80+20)/(100+50) ≠ mean(0.8, 0.4)
    assert rules.put_call_ratio(days[0].put_volume, days[0].call_volume) == D("0.666667")


def test_basket_ignores_other_underlyings_and_inherits_review_flag() -> None:
    basket = rules.GROUPS_BY_CODE["sse50"].baskets[0]
    day = date(2026, 10, 16)
    rows = [
        _row(day, "SSE", "510050", 10, 5),
        _row(day, "SSE", "588000", 99, 99),
        rules.OptionRow(date(2026, 10, 15), "SSE", "510050", 10, 5, 20, 10, "review"),
    ]
    days, incomplete = rules.combine_basket(rows, basket)
    assert incomplete == 0
    assert [(item.trade_date, item.quality_flag) for item in days] == [
        (date(2026, 10, 15), "review"),
        (day, "ok"),
    ]


def test_csi1000_has_only_the_cffex_basket() -> None:
    group = rules.GROUPS_BY_CODE["csi1000"]
    assert [basket.kind for basket in group.baskets] == ["index"]
    assert group.baskets[0].members[0].code == "MO"


def test_moving_average_needs_full_window_without_gaps() -> None:
    values: list[Decimal | None] = [D(i) for i in range(1, 12)]
    averages = rules.moving_average(values, 10)
    assert averages[8] is None
    assert averages[9] == D("5.5")
    assert averages[10] == D("6.5")
    gapped: list[Decimal | None] = [D(1)] * 4 + [None] + [D(1)] * 8
    assert rules.moving_average(gapped, 10)[12] is None


# ---------------------------------------------------------------- 滚动窗口


def _days(count: int, start: date = date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def test_percentile_is_share_of_window_not_above_current() -> None:
    dates = _days(5)
    values = [D(1), D(2), D(3), D(4), D(2)]
    out = rules.rolling_percentile(dates, values, years=5, min_samples=5)
    assert out[:4] == [None, None, None, None]
    assert out[4] == 60.0  # 不大于当前值 2 的有 1、2、2 共 3 个，占 5 个样本的 60%


def test_percentile_counts_values_not_above_current_inclusive() -> None:
    dates = _days(5)
    out = rules.rolling_percentile(dates, [D(5), D(4), D(3), D(2), D(1)], years=5, min_samples=5)
    assert out[4] == 20.0  # 只有自己不大于自己
    out_top = rules.rolling_percentile(
        dates, [D(1), D(2), D(3), D(4), D(5)], years=5, min_samples=5
    )
    assert out_top[4] == 100.0


def test_percentile_window_excludes_the_day_exactly_five_years_back() -> None:
    first = date(2020, 3, 2)
    later = date(2025, 3, 2)  # 恰好 5 年后：first 落在窗口之外
    dates = [first, date(2022, 1, 3), later]
    out = rules.rolling_percentile(dates, [D(100), D(1), D(2)], years=5, min_samples=2)
    # 窗口只含 2022-01-03 与 later 两个样本：不大于 2 的是 1 和 2 → 100
    assert out[2] == 100.0


def test_percentile_refuses_short_history() -> None:
    dates = _days(100)
    assert rules.rolling_percentile(dates, [D(i) for i in range(100)]) == [None] * 100


def test_band_is_mean_plus_minus_two_sample_std() -> None:
    dates = _days(5)
    bands = rules.rolling_band(dates, [D(2), D(4), D(4), D(4), D(6)], years=5, min_samples=5)
    band = bands[4]
    assert band is not None
    assert band.mean == pytest.approx(4.0)
    assert band.std == pytest.approx(1.414213, rel=1e-5)
    assert band.upper == pytest.approx(4.0 + 2 * band.std)
    assert band.lower == pytest.approx(4.0 - 2 * band.std)
    assert bands[3] is None


def test_years_back_handles_leap_day() -> None:
    assert rules.years_back(date(2024, 2, 29), 1) == date(2023, 2, 28)


# ---------------------------------------------------------------- 股债收益差


def test_erp_uses_percent_on_both_sides() -> None:
    # 100 / 12.5 = 8.0%；减去 1.65% → 6.35 个百分点
    assert rules.equity_risk_premium_pct(D("12.5"), D("1.65")) == D("6.35")


def test_erp_rejects_non_positive_pe() -> None:
    assert rules.equity_risk_premium_pct(D("-3"), D("1.65")) is None
    assert rules.earnings_yield_pct(D(0)) is None


# ---------------------------------------------------------------- 巴菲特指标


def _cumulative() -> dict[date, Decimal]:
    return {
        date(2025, 3, 31): D(300),
        date(2025, 6, 30): D(650),
        date(2025, 9, 30): D(1000),
        date(2025, 12, 31): D(1400),
        date(2026, 3, 31): D(330),
        date(2026, 6, 30): D(700),
    }


def test_cumulative_gdp_is_differenced_into_single_quarters() -> None:
    singles = rules.single_quarter_gdp(_cumulative())
    assert singles[date(2025, 3, 31)] == D(300)
    assert singles[date(2025, 6, 30)] == D(350)
    assert singles[date(2025, 9, 30)] == D(350)
    assert singles[date(2025, 12, 31)] == D(400)
    assert singles[date(2026, 6, 30)] == D(370)


def test_single_quarter_is_missing_when_previous_cumulative_is_missing() -> None:
    cumulative = {date(2025, 6, 30): D(650)}
    assert rules.single_quarter_gdp(cumulative) == {}


def test_trailing_gdp_waits_for_release_lag() -> None:
    singles = rules.single_quarter_gdp(_cumulative())
    # 2026-06-30 这一季要到 2026-07-21 才视为已公布
    before = rules.trailing_gdp_billion(singles, date(2026, 7, 20))
    after = rules.trailing_gdp_billion(singles, date(2026, 7, 21))
    assert before is not None
    assert before[1] == date(2026, 3, 31)
    assert after is not None
    assert after[1] == date(2026, 6, 30)
    # 2025Q3 + 2025Q4 + 2026Q1 + 2026Q2 = 350 + 400 + 330 + 370
    assert after[0] == D(1450)


def test_trailing_gdp_needs_four_consecutive_quarters() -> None:
    singles = rules.single_quarter_gdp(_cumulative())
    assert rules.trailing_gdp_billion(singles, date(2025, 11, 1)) is None


def test_buffett_ratio_converts_units() -> None:
    # 市值 120 万亿元；GDP 140 万亿元 = 1_400_000 亿元 → 85.71%
    ratio = rules.buffett_ratio_pct(D("120000000000000"), D(1_400_000))
    assert ratio is not None
    assert round(ratio, 2) == D("85.71")
    assert rules.buffett_ratio_pct(D(1), D(0)) is None


# ---------------------------------------------------------------- 中位数 PB


def _stock(code: str, name: str, pb: str | None, cap: str | None = "1000") -> StockValuation:
    return StockValuation(
        code=code,
        name=name,
        total_market_cap=None if cap is None else D(cap),
        pb_mrq=None if pb is None else D(pb),
    )


def test_median_pb_applies_every_cleaning_rule() -> None:
    day = date(2026, 10, 16)
    listing = {
        "000001": date(2000, 1, 1),
        "000002": date(2000, 1, 1),
        "000003": date(2000, 1, 1),
        "000004": date(2026, 6, 1),  # 上市 137 天 → 次新
        "000005": date(2026, 4, 19),  # 正好 180 天 → 不算次新
        "000006": date(2000, 1, 1),
        "000007": date(2000, 1, 1),
        "000008": date(2000, 1, 1),
    }
    rows = [
        _stock("000001", "甲", "1.0"),
        _stock("000002", "乙", "2.0"),
        _stock("000003", "丙", "3.0"),
        _stock("000004", "次新", "0.1"),
        _stock("000005", "满半年", "4.0"),
        _stock("000006", "*ST丁", "0.2"),
        _stock("000007", "负净资产", "-1.5"),
        _stock("000008", "无PB", None),
        _stock("000009", "不详上市日", "5.0"),
    ]
    result = rules.compute_median_pb(rows, day, listing)
    assert result.universe == 9
    assert result.excluded_missing == 1
    assert result.excluded_negative == 1
    assert result.excluded_new == 1
    assert result.excluded_st == 1
    assert result.listing_unknown == 1
    # 保留：1.0 2.0 3.0 4.0 5.0 → 中位数 3.0；不剔除 ST 则多一个 0.2 → (2.0+3.0)/2
    assert result.sample == 5
    assert result.median == D("3.0")
    assert result.median_without_st_filter == D("2.5")


def test_median_pb_is_none_without_any_valid_row() -> None:
    result = rules.compute_median_pb([_stock("1", "甲", "-1")], date(2026, 10, 16), {})
    assert result.median is None
    assert result.sample == 0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ST晨鸣", True),
        ("*ST康佳A", True),
        ("S*ST某某", True),
        ("某某退", True),
        ("贵州茅台", False),
        ("TCL科技", False),
    ],
)
def test_st_name_detection(name: str, expected: bool) -> None:
    assert rules.is_st_name(name) is expected


# ---------------------------------------------------------------- 序列闸门


def test_cross_section_gate() -> None:
    full = [_stock(f"{index:06d}", "名", "1.0") for index in range(3500)]
    assert rules.check_cross_section(full, 3500).flag == "ok"
    assert rules.check_cross_section(full[:2000], 2000).flag == "rejected"
    assert rules.check_cross_section(full, 3600).flag == "rejected"
    no_cap = [_stock(f"{index:06d}", "名", "1.0", cap=None) for index in range(3500)]
    assert rules.check_cross_section(no_cap, 3500).flag == "review"


def test_pe_and_yield_gates() -> None:
    assert rules.check_pe(D("12.9")).flag == "ok"
    assert rules.check_pe(D(0)).flag == "rejected"
    assert rules.check_pe(D(500)).flag == "rejected"
    assert rules.check_yield(D("1.65")).flag == "ok"
    assert rules.check_yield(D(-1)).flag == "rejected"
    assert rules.check_yield(D(40)).flag == "rejected"


def test_jump_gate_flags_large_day_over_day_change() -> None:
    assert rules.check_jump(None, D(100), D("0.1"), "x").flag == "ok"
    assert rules.check_jump(D(100), D(105), D("0.1"), "x").flag == "ok"
    assert rules.check_jump(D(100), D(120), D("0.1"), "x").flag == "review"
