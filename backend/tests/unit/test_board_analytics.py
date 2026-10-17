"""板块分析纯计算：均线、区间涨幅、相对超额、估值分位、涨跌分布、子行业聚合。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.providers.eastmoney_boards import BoardBar, BoardQuote, MemberQuote, ValuationPoint
from app.services import board_analytics as rules


def _bar(day: date, close: str) -> BoardBar:
    value = Decimal(close)
    return BoardBar(day, value, value, value, value, Decimal(1), Decimal(1))


def _bars(closes: list[str], start: date = date(2026, 1, 1)) -> list[BoardBar]:
    return [_bar(start + timedelta(days=i), close) for i, close in enumerate(closes)]


def _member(change: str | None, cap: str | None = "100", sub: str | None = "A") -> MemberQuote:
    return MemberQuote(
        code="000001",
        exchange="SZSE",
        name="x",
        last=None,
        change_pct=None if change is None else Decimal(change),
        change_5d_pct=None,
        change_10d_pct=None,
        change_60d_pct=None,
        change_ytd_pct=None,
        amount=None,
        turnover_pct=None,
        pe_dynamic=None,
        pb=None,
        market_cap=None if cap is None else Decimal(cap),
        main_net_inflow=Decimal(10),
        sub_industry=sub,
    )


def test_moving_average_waits_for_full_window() -> None:
    values = [Decimal(v) for v in ("1", "2", "3", "4")]
    assert rules.moving_average(values, 3) == [None, None, Decimal("2.00"), Decimal("3.00")]
    with pytest.raises(ValueError):
        rules.moving_average(values, 0)


def test_window_return_needs_enough_sessions() -> None:
    bars = _bars(["100", "105", "110"])
    assert rules.window_return_pct(bars, 2) == Decimal("10.00")
    assert rules.window_return_pct(bars, 3) is None


def test_relative_curve_aligns_on_common_days_only() -> None:
    board = _bars(["100", "110", "121"])
    bench = [_bar(date(2026, 1, 1), "50"), _bar(date(2026, 1, 3), "55")]
    curve = rules.relative_curve(board, bench)
    assert [point.trade_date for point in curve] == [date(2026, 1, 1), date(2026, 1, 3)]
    assert curve[-1].board_index == Decimal("121.00")
    assert curve[-1].benchmark_index == Decimal("110.00")
    assert curve[-1].excess_pct == Decimal("11.00")
    assert rules.relative_curve(board, []) == []


def test_percentile_ignores_negative_pe_and_old_points() -> None:
    start = date(2026, 1, 1)
    points = [
        ValuationPoint(start + timedelta(days=i), Decimal(10 + i), None, None, 10, 0)
        for i in range(30)
    ]
    points.append(ValuationPoint(start - timedelta(days=4000), Decimal(1), None, None, 10, 0))
    points.append(ValuationPoint(start + timedelta(days=5), Decimal(-5), None, None, 10, 9))
    summary = rules.percentile_summary(points, field="pe_ttm", since=start)
    assert summary is not None
    assert summary.samples == 30
    assert summary.current == Decimal("39.00")
    assert summary.percentile == Decimal("100.00")
    assert summary.minimum == Decimal("10.00")


def test_percentile_needs_twenty_samples() -> None:
    points = [ValuationPoint(date(2026, 1, i + 1), Decimal(10), None, None, 1, 0) for i in range(5)]
    assert rules.percentile_summary(points, field="pe_ttm", since=date(2025, 1, 1)) is None


def test_distribution_counts_buckets_and_suspended() -> None:
    members = [_member(v) for v in ("-9.9", "-6", "-0.5", "0", "0.1", "4", "10")] + [_member(None)]
    buckets, suspended = rules.change_distribution(members)
    counts = {bucket.label: bucket.count for bucket in buckets}
    assert suspended == 1
    assert counts["≤-7%"] == 1 and counts["-7~-5%"] == 1 and counts["-3~0%"] == 1
    assert counts["平"] == 1 and counts["0~3%"] == 1 and counts["3~5%"] == 1 and counts["≥7%"] == 1
    assert {bucket.tone for bucket in buckets if bucket.label == "平"} == {"flat"}


def test_sub_industries_weight_change_by_market_cap() -> None:
    members = [
        _member("10", "300", "半导体"),
        _member("-10", "100", "半导体"),
        _member("5", None, "元件"),
        _member("1", "50", None),
    ]
    groups = {group.name: group for group in rules.sub_industries(members)}
    assert set(groups) == {"半导体", "元件"}
    assert groups["半导体"].weighted_change_pct == Decimal("5.00")
    assert groups["半导体"].members == 2
    # 没有市值的成分不进加权，整组无可加权样本时给 None 而不是 0。
    assert groups["元件"].weighted_change_pct is None


def test_breadth_sums_members_and_ignores_missing() -> None:
    def quote(change: str | None, up: int | None) -> BoardQuote:
        return BoardQuote(
            "BK1",
            "a",
            None,
            None if change is None else Decimal(change),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Decimal(5),
            None,
            up,
            1,
            0,
            None,
            None,
            None,
        )

    result = rules.breadth([quote("1", 3), quote("-1", None), quote(None, 2)])
    assert (result.boards_up, result.boards_down, result.stocks_up) == (1, 1, 5)
    assert result.total_main_net_inflow == Decimal(15)
