"""均线交叉回测引擎：纯函数、无副作用、可离线测试。

口径（避免未来函数）：
- 信号只用当日及以前的收盘价，在**下一个交易日开盘价**成交；最后一根 K 线产生的信号不成交。
- 只做多、满仓或空仓；每次成交按成交额收单边费率 `fee_bps`（基点）。
- 权益按每日收盘价盯市；年化按 252 个交易日折算。
- 全程 Decimal；结果只描述历史，不是收益预测。
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

TRADING_DAYS = Decimal(252)
MIN_TRADES_FOR_STATS = 3
_ONE = Decimal(1)
_BPS = Decimal(10000)


@dataclass(frozen=True)
class Bar:
    trade_date: date
    open: Decimal
    close: Decimal


@dataclass(frozen=True)
class BacktestResult:
    total_return_pct: Decimal
    annualized_pct: Decimal
    max_drawdown_pct: Decimal
    buy_hold_return_pct: Decimal
    trades: int
    win_rate_pct: Decimal | None
    exposure_pct: Decimal
    first_date: date
    last_date: date
    bars: int

    @property
    def enough_trades(self) -> bool:
        return self.trades >= MIN_TRADES_FOR_STATS


def _sma(closes: list[Decimal], end: int, window: int) -> Decimal | None:
    """`closes[end-window+1 .. end]` 的均值；样本不够返回 None。"""

    if end + 1 < window:
        return None
    return sum(closes[end + 1 - window : end + 1], Decimal(0)) / window


def run_sma_cross(
    bars: list[Bar], fast: int, slow: int, fee_bps: Decimal = Decimal(0)
) -> BacktestResult:
    if not 1 <= fast < slow:
        raise ValueError("fast 必须小于 slow，且都至少为 1")
    if fee_bps < 0:
        raise ValueError("费率不能为负")
    if len(bars) < slow + 2:
        raise ValueError("日线太少，至少需要 slow + 2 根")
    if any(b.open <= 0 or b.close <= 0 for b in bars):
        raise ValueError("日线里有非正价格")
    closes = [b.close for b in bars]
    fee = fee_bps / _BPS

    cash = _ONE  # 初始净值 1
    shares = Decimal(0)
    holding = False
    entry_equity = Decimal(0)
    wins = 0
    trades = 0
    held_days = 0
    peak = _ONE
    max_dd = Decimal(0)
    pending: bool | None = None  # 昨收盘产生的目标仓位，今天开盘执行

    for i, bar in enumerate(bars):
        if pending is not None and pending != holding:
            if pending:
                shares = cash * (_ONE - fee) / bar.open
                cash = Decimal(0)
                entry_equity = shares * bar.open
            else:
                cash = shares * bar.open * (_ONE - fee)
                trades += 1
                if cash > entry_equity:
                    wins += 1
                shares = Decimal(0)
            holding = pending
        pending = None
        equity = cash + shares * bar.close
        if holding:
            held_days += 1
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        f, s = _sma(closes, i, fast), _sma(closes, i, slow)
        if f is not None and s is not None and i < len(bars) - 1:
            pending = f > s

    final = cash + shares * bars[-1].close
    total = final - _ONE
    years = Decimal(len(bars)) / TRADING_DAYS
    annual = (final ** (_ONE / years) - _ONE) if years > 0 and final > 0 else Decimal(-1)
    hold = bars[-1].close / bars[0].close - _ONE
    return BacktestResult(
        total_return_pct=_pct(total),
        annualized_pct=_pct(annual),
        max_drawdown_pct=_pct(max_dd),
        buy_hold_return_pct=_pct(hold),
        trades=trades,
        win_rate_pct=_pct(Decimal(wins) / trades) if trades else None,
        exposure_pct=_pct(Decimal(held_days) / len(bars)),
        first_date=bars[0].trade_date,
        last_date=bars[-1].trade_date,
        bars=len(bars),
    )


def _pct(value: Decimal) -> Decimal:
    return (value * 100).quantize(Decimal("0.01"))
