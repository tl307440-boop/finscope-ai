"""决策记忆（对齐 TradingAgents v0.6 的 memory log）：结算历史决策并写反思。

结算口径：入场为 as_of 当日或之前最后一个收盘，出场为入场后 holding_days 自然日内最后一个收盘；
同一对入场/出场日取基准（沪深300）收盘，超额 = 标的收益 - 基准收益。
用于新一轮推演时，只结算「在新 as_of 之前持有期已经走完」的决策，避免把未来收益带进记忆。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.trading_agents.backtest import window_return
from app.trading_agents.bridge import Bar

ACCURATE_BAND = Decimal("0.02")
HOLD_BAND = Decimal("0.03")
HOLD_WIDE = Decimal("0.08")
VERDICT_LABEL = {
    "accurate": "方向一致",
    "partially_accurate": "大体持平",
    "divergent": "方向相反",
}
RATING_DIRECTION = {"buy": 1, "overweight": 1, "hold": 0, "underweight": -1, "sell": -1}


@dataclass(frozen=True)
class Settlement:
    entry_date: date
    exit_date: date
    actual: Decimal
    benchmark: Decimal | None
    excess: Decimal | None
    verdict: str | None


def settle(
    rating: str,
    closes: Sequence[Bar],
    benchmark: Sequence[Bar],
    as_of: date,
    holding_days: int,
    visible_until: date | None = None,
) -> Settlement | None:
    pairs = [
        (bar.trade_date, bar.close)
        for bar in closes
        if visible_until is None or bar.trade_date <= visible_until
    ]
    measured = window_return(pairs, as_of, holding_days)
    if measured is None:
        return None
    if (
        visible_until is not None
        and measured.entry_date + timedelta(days=holding_days) > visible_until
    ):
        return None
    base = _close_on(benchmark, measured.entry_date)
    end = _close_on(benchmark, measured.exit_date)
    bench = None if base is None or end is None or base <= 0 else end / base - 1
    excess = None if bench is None else measured.actual - bench
    return Settlement(
        entry_date=measured.entry_date,
        exit_date=measured.exit_date,
        actual=measured.actual.quantize(Decimal("0.00000001")),
        benchmark=None if bench is None else bench.quantize(Decimal("0.00000001")),
        excess=None if excess is None else excess.quantize(Decimal("0.00000001")),
        verdict=None if excess is None else verdict_for(rating, excess),
    )


def verdict_for(rating: str, excess: Decimal) -> str | None:
    side = RATING_DIRECTION.get(rating.lower())
    if side is None:
        return None
    if side == 0:
        if abs(excess) <= HOLD_BAND:
            return "accurate"
        return "partially_accurate" if abs(excess) <= HOLD_WIDE else "divergent"
    signed = excess * side
    if signed > ACCURATE_BAND:
        return "accurate"
    if signed >= -ACCURATE_BAND:
        return "partially_accurate"
    return "divergent"


def reflection_for(
    rating: str,
    settlement: Settlement,
    drivers: Sequence[str],
    risks: Sequence[str],
) -> str:
    head = (
        f"{settlement.entry_date} 评级 {rating.capitalize()}，至 {settlement.exit_date} "
        f"标的 {_pct(settlement.actual)}"
    )
    if settlement.excess is None or settlement.verdict is None:
        return head + "，缺少同期基准收盘，无法判断超额"
    head += f"、超额 {_pct(settlement.excess)}（{VERDICT_LABEL[settlement.verdict]}）"
    side = RATING_DIRECTION.get(rating.lower(), 0)
    if settlement.verdict == "accurate":
        basis = drivers[0] if side >= 0 and drivers else (risks[0] if risks else "")
        return head + (f"，当时的关键依据「{_short(basis)}」得到验证" if basis else "")
    if settlement.verdict == "divergent":
        if side > 0:
            missed = risks[0] if risks else ""
            return head + (
                f"，当时低估了「{_short(missed)}」" if missed else "，当时没有识别出足够的下行风险"
            )
        if side < 0:
            missed = drivers[0] if drivers else ""
            return head + (
                f"，当时低估了「{_short(missed)}」" if missed else "，当时没有识别出上行动能"
            )
        return head + "，持有观望错过了明显的方向性行情"
    return head + "，结论与市场大体持平"


def _close_on(bars: Sequence[Bar], day: date) -> Decimal | None:
    found = None
    for bar in bars:
        if bar.trade_date > day:
            break
        found = bar.close
    return found


def _pct(value: Decimal) -> str:
    return f"{value * 100:+.2f}%"


def _short(text: str) -> str:
    return text if len(text) <= 30 else text[:29] + "…"
