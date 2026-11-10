"""均线参数的稳健筛选：折外稳健分 + 最小交易数门槛 + 样本外检验。纯函数、无副作用。

流程（参考 TSP 的做法，口径自己定义）：
1. 序列按时间切成「样本内（前 1-oos_ratio）」和「样本外（尾部 oos_ratio）」。
2. 样本内再切成 `folds` 个连续等长的折，每个参数组合在每一折上独立回测。
3. 稳健分 = 0.6 × 各折收益中位数 + 0.4 × 最差一折收益；各折交易数合计低于门槛的组合淘汰。
4. 稳健分最高的组合拿去跑样本外尾段，并与同期买入持有对比。

参数是事后挑出来的，网格越大越容易挑到运气，所以结果里必须带上「测试了多少组」。
"""

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from app.services.backtest import Bar, run_sma_cross

MEDIAN_WEIGHT = Decimal("0.6")
WORST_WEIGHT = Decimal("0.4")
MAX_COMBOS = 36
_QUANT = Decimal("0.01")


@dataclass(frozen=True)
class ParamScore:
    fast: int
    slow: int
    fold_returns_pct: tuple[Decimal, ...]
    median_pct: Decimal
    worst_pct: Decimal
    robust_pct: Decimal
    trades: int


@dataclass(frozen=True)
class ScreenResult:
    ranked: tuple[ParamScore, ...]  # 通过门槛的，按稳健分从高到低
    tested: int  # 网格里 fast < slow 的组合数
    skipped_short: int  # 折太短跑不了的组合数
    rejected_trades: int  # 交易数不够被淘汰的组合数
    folds: int
    in_sample_bars: int
    oos_bars: int
    oos_return_pct: Decimal | None  # 稳健分第一名在样本外的收益
    oos_buy_hold_pct: Decimal | None
    oos_trades: int | None


def split_ranges(count: int, folds: int) -> list[tuple[int, int]]:
    size = count // folds
    return [(i * size, count if i == folds - 1 else (i + 1) * size) for i in range(folds)]


def screen_sma(
    bars: list[Bar],
    fast_values: list[int],
    slow_values: list[int],
    *,
    folds: int = 4,
    min_trades: int = 6,
    fee_bps: Decimal = Decimal(10),
    oos_ratio: Decimal = Decimal("0.3"),
) -> ScreenResult:
    if folds < 2:
        raise ValueError("折数至少为 2")
    if not Decimal("0.1") <= oos_ratio <= Decimal("0.5"):
        raise ValueError("样本外占比应在 0.1 到 0.5 之间")
    combos = sorted({(f, s) for f in fast_values for s in slow_values if 1 <= f < s})
    if not combos:
        raise ValueError("没有 fast < slow 的参数组合")
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"参数组合最多 {MAX_COMBOS} 组")
    cut = len(bars) - int(len(bars) * oos_ratio)
    in_sample, oos = bars[:cut], bars[cut:]
    if len(in_sample) < folds * 30:
        raise ValueError("样本内日线太少，至少需要折数 × 30 根")
    ranges = split_ranges(len(in_sample), folds)

    scored: list[ParamScore] = []
    skipped = rejected = 0
    for fast, slow in combos:
        returns: list[Decimal] = []
        trades = 0
        try:
            for lo, hi in ranges:
                res = run_sma_cross(in_sample[lo:hi], fast, slow, fee_bps)
                returns.append(res.total_return_pct)
                trades += res.trades
        except ValueError:
            skipped += 1
            continue
        if trades < min_trades:
            rejected += 1
            continue
        mid = Decimal(str(median(returns)))
        worst = min(returns)
        robust = (MEDIAN_WEIGHT * mid + WORST_WEIGHT * worst).quantize(_QUANT)
        scored.append(
            ParamScore(fast, slow, tuple(returns), mid.quantize(_QUANT), worst, robust, trades)
        )
    scored.sort(key=lambda item: (-item.robust_pct, item.fast, item.slow))

    oos_return = oos_hold = None
    oos_trades = None
    if scored:
        best = scored[0]
        try:
            res = run_sma_cross(oos, best.fast, best.slow, fee_bps)
            oos_return, oos_hold, oos_trades = (
                res.total_return_pct,
                res.buy_hold_return_pct,
                res.trades,
            )
        except ValueError:
            pass  # 样本外太短，留空，由工具说明
    return ScreenResult(
        ranked=tuple(scored),
        tested=len(combos),
        skipped_short=skipped,
        rejected_trades=rejected,
        folds=folds,
        in_sample_bars=len(in_sample),
        oos_bars=len(oos),
        oos_return_pct=oos_return,
        oos_buy_hold_pct=oos_hold,
        oos_trades=oos_trades,
    )
