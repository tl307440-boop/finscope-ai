"""参数稳健筛选与因子排名：纯函数的口径、门槛、边界，以及两个工具的注册和入参防护。"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.services.assistant.tools import default_registry
from app.services.assistant.tools.base import ToolContext
from app.services.backtest import Bar
from app.services.factor_rank import factor_value, rank_values
from app.services.strategy_screen import ParamScore, screen_sma, split_ranges


def _wave(n: int) -> list[Bar]:
    """有涨有跌的锯齿行情，保证均线有足够多的交叉。"""

    start = date(2025, 1, 1)
    out = []
    for i in range(n):
        price = Decimal(100 + (i % 20) * (1 if (i // 20) % 2 == 0 else -1) + i // 10)
        out.append(Bar(start + timedelta(days=i), price, price))
    return out


def test_split_ranges_cover_everything_without_overlap() -> None:
    ranges = split_ranges(103, 4)
    assert ranges[0][0] == 0 and ranges[-1][1] == 103
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:], strict=False))


def test_screen_ranks_by_robust_score_and_reports_oos() -> None:
    result = screen_sma(_wave(400), [2, 3, 5], [8, 12], folds=4, min_trades=1)
    assert result.tested == 6
    scores = [p.robust_pct for p in result.ranked]
    assert scores == sorted(scores, reverse=True)
    best = result.ranked[0]
    expected = (Decimal("0.6") * best.median_pct + Decimal("0.4") * best.worst_pct).quantize(
        Decimal("0.01")
    )
    assert best.robust_pct == (
        (Decimal("0.6") * best.median_pct + Decimal("0.4") * best.worst_pct).quantize(
            Decimal("0.01")
        )
    ) or abs(best.robust_pct - expected) <= Decimal("0.01")
    assert result.oos_return_pct is not None and result.oos_buy_hold_pct is not None
    assert result.in_sample_bars + result.oos_bars == 400


def test_screen_min_trades_gate_rejects_everything() -> None:
    result = screen_sma(_wave(400), [2, 3], [8], folds=4, min_trades=10_000)
    assert result.ranked == () and result.rejected_trades == 2
    assert result.oos_return_pct is None


def test_screen_skips_combos_whose_slow_window_does_not_fit_a_fold() -> None:
    result = screen_sma(_wave(200), [2], [8, 100], folds=4, min_trades=1)
    assert result.skipped_short == 1


def test_screen_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        screen_sma(_wave(400), [10], [5], folds=4)
    with pytest.raises(ValueError):
        screen_sma(_wave(40), [2], [5], folds=4)
    with pytest.raises(ValueError):
        screen_sma(_wave(400), [2], [5], folds=1)


def test_param_score_is_frozen() -> None:
    p = ParamScore(1, 2, (Decimal(1),), Decimal(1), Decimal(1), Decimal(1), 1)
    with pytest.raises(AttributeError):
        p.fast = 3  # type: ignore[misc]


def test_factor_values_known_answers() -> None:
    closes = [Decimal(x) for x in (100, 110, 99, 120)]
    assert factor_value(closes, "momentum", 3) == Decimal("20.00")
    # 峰值 110 → 谷值 99，回撤 10%
    assert factor_value(closes, "drawdown", 3) == Decimal("10.00")
    flat = [Decimal(10)] * 10
    assert factor_value(flat, "volatility", 5) == Decimal("0.00")
    assert factor_value(closes, "momentum", 5) is None
    assert factor_value([Decimal(0), Decimal(1), Decimal(2)], "momentum", 2) is None


def test_rank_values_order_and_ties() -> None:
    values = {"a": Decimal(5), "b": Decimal(9), "c": Decimal(5)}
    assert rank_values(values) == [(1, "b", Decimal(9)), (2, "a", Decimal(5)), (2, "c", Decimal(5))]
    assert rank_values(values, descending=False)[0][1] == "a"


def test_tools_registered_and_guarded() -> None:
    registry = default_registry()
    assert {"strategy_screen", "factor_rank", "run_backtest"} <= set(registry.names)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    ctx = ToolContext(engine=MagicMock(), user_id=uuid4(), now=now)
    raw = json.dumps({"instrument_id": str(uuid4())})
    assert registry.execute("strategy_screen", raw, ctx).result.status == "unavailable"
    bad_payload = json.dumps({"instrument_id": "x", "fast_values": [20], "slow_values": [5]})
    bad = registry.execute("strategy_screen", bad_payload, ctx)
    assert bad.result.status == "unavailable"
    assert (
        registry.execute("factor_rank", '{"factor": "alpha"}', ctx).result.status == "unavailable"
    )
    searched_payload = '{"factor": "momentum", "universe": "searched"}'
    only_one = registry.execute("factor_rank", searched_payload, ctx)
    assert only_one.result.status == "unavailable"
