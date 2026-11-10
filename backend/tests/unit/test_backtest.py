"""均线交叉回测引擎与 `run_backtest` 工具：无未来函数、边界、缺数据。"""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.services.assistant.tools import default_registry
from app.services.backtest import Bar, run_sma_cross


def _bars(opens: list[str], closes: list[str]) -> list[Bar]:
    start = date(2026, 1, 1)
    return [
        Bar(start + timedelta(days=i), Decimal(o), Decimal(c))
        for i, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]


def test_signal_executes_next_open_not_same_close() -> None:
    # 第 4 天收盘（11）首次让快线上穿慢线；第 5 天开盘价 100 成交，不能用当天收盘 11。
    closes = ["10", "10", "10", "10", "11", "12", "13"]
    opens = ["10", "10", "10", "10", "11", "100", "12"]
    result = run_sma_cross(_bars(opens, closes), fast=1, slow=2)
    # 信号在第 5 天（index 4）收盘产生 → index 5 开盘 100 买入，收盘 11 → 大亏。
    assert result.total_return_pct < Decimal(-80)


def test_uptrend_beats_flat_and_counts_fees() -> None:
    closes = [str(10 + i) for i in range(40)]
    bars = _bars(closes, closes)
    free = run_sma_cross(bars, 3, 8, Decimal(0))
    costly = run_sma_cross(bars, 3, 8, Decimal(100))
    assert free.total_return_pct > costly.total_return_pct > 0
    assert free.max_drawdown_pct == 0


def test_round_trip_trades_and_win_rate() -> None:
    closes = ["10"] * 5 + ["12"] * 5 + ["8"] * 5 + ["12"] * 5
    result = run_sma_cross(_bars(closes, closes), 1, 3)
    assert result.trades >= 1
    assert result.win_rate_pct is not None
    assert not run_sma_cross(_bars(["10"] * 10, ["10"] * 10), 1, 3).enough_trades


def test_invalid_inputs_raise() -> None:
    bars = _bars(["10"] * 10, ["10"] * 10)
    with pytest.raises(ValueError):
        run_sma_cross(bars, 5, 5)
    with pytest.raises(ValueError):
        run_sma_cross(bars, 2, 20)
    with pytest.raises(ValueError):
        run_sma_cross(_bars(["0"] * 10, ["10"] * 10), 1, 3)


def test_registered_and_rejects_unsearched_instrument() -> None:
    from datetime import UTC, datetime
    from unittest.mock import MagicMock
    from uuid import uuid4

    from app.services.assistant.tools.base import ToolContext

    registry = default_registry()
    assert "run_backtest" in registry.names
    now = datetime(2026, 10, 21, tzinfo=UTC)
    ctx = ToolContext(engine=MagicMock(), user_id=uuid4(), now=now)
    raw = json.dumps({"instrument_id": str(uuid4())})
    bad = registry.execute("run_backtest", raw, ctx)
    assert bad.result.status == "unavailable"
    wrong = registry.execute("run_backtest", '{"instrument_id": "x", "fast": 20, "slow": 5}', ctx)
    assert wrong.result.status == "unavailable"
