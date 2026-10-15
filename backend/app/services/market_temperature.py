from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median, pstdev

MIN_INDEX_BARS = 60
MIN_BREADTH_NAMES = 5
MIN_SECTOR_NAMES = 3
VERSION = "market-temperature-v1"


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    close: Decimal
    amount: Decimal | None


@dataclass(frozen=True)
class FactorScore:
    code: str
    label: str
    weight: Decimal
    score: Decimal | None
    raw: str
    note: str


@dataclass(frozen=True)
class TemperatureResult:
    status: str
    label: str
    score: Decimal | None
    coverage: str
    factors: tuple[FactorScore, ...]
    notice: str


def market_temperature(
    index_bars: list[PriceBar],
    members: dict[str, list[PriceBar]],
) -> TemperatureResult:
    ordered = _ordered(index_bars)
    notice = "这是描述性温度，不是收益预测。"
    if len(ordered) < MIN_INDEX_BARS:
        return _unable("指数样本不足 60 个交易日", notice)
    eligible = {
        symbol: _ordered(bars)
        for symbol, bars in members.items()
        if len(_ordered(bars)) >= MIN_INDEX_BARS
        and _ordered(bars)[-1].trade_date == ordered[-1].trade_date
    }
    if len(eligible) < MIN_BREADTH_NAMES:
        return _unable("可计算广度的成分不足 5 只，无法判断", notice)
    trend = _trend(ordered)
    breadth = _breadth(eligible)
    active = _activity(ordered)
    vol = _volatility(ordered)
    if active is None or vol is None:
        return _unable("成交额或波动样本不足，无法判断", notice)
    factors = (trend, breadth, active, vol)
    score = sum(
        ((item.score or Decimal(0)) * item.weight for item in factors),
        Decimal(0),
    )
    label = "偏强" if score >= 60 else "偏弱" if score <= 40 else "中性"
    return TemperatureResult(
        status="ok",
        label=label,
        score=score.quantize(Decimal("0.1")),
        coverage=f"指数 {len(ordered)} 日，成分 {len(eligible)} 只",
        factors=factors,
        notice=notice,
    )


def sector_strength(
    index_bars: list[PriceBar],
    members: dict[str, list[PriceBar]],
) -> TemperatureResult:
    ordered = _ordered(index_bars)
    notice = "成分以当前分类计算，历史退市股未回溯，有幸存者偏差。这不是排名承诺。"
    eligible = {
        symbol: _ordered(bars) for symbol, bars in members.items() if len(_ordered(bars)) >= 2
    }
    if len(ordered) < 2 or len(eligible) < MIN_SECTOR_NAMES:
        return _unable("板块样本不足，无法判断", notice)
    end = ordered[-1].trade_date
    aligned = {
        symbol: bars
        for symbol, bars in eligible.items()
        if bars[-1].trade_date == end and bars[0].trade_date <= ordered[0].trade_date
    }
    if len(aligned) < MIN_SECTOR_NAMES:
        return _unable("与指数日期对齐的成分不足，无法判断", notice)
    returns = [_return(bars) for bars in aligned.values()]
    index_return = _return(ordered)
    if index_return is None or any(item is None for item in returns):
        return _unable("收益无法计算", notice)
    numeric = [item for item in returns if item is not None]
    middle = median(numeric)
    advances = sum(1 for item in numeric if item > 0) / Decimal(len(numeric))
    excess = middle - index_return
    score = Decimal("50") + (excess * Decimal("100")).quantize(Decimal("0.1"))
    bounded = min(Decimal("100"), max(Decimal("0"), score))
    label = "偏强" if bounded >= 60 else "偏弱" if bounded <= 40 else "中性"
    factor = FactorScore(
        code="excess",
        label="相对指数的中位收益",
        weight=Decimal("1"),
        score=bounded.quantize(Decimal("0.1")),
        raw=f"中位收益 {middle}，指数收益 {index_return}，上涨占比 {advances}",
        note=notice,
    )
    return TemperatureResult(
        status="ok",
        label=label,
        score=bounded.quantize(Decimal("0.1")),
        coverage=f"成分 {len(aligned)} 只",
        factors=(factor,),
        notice=notice,
    )


def _trend(bars: list[PriceBar]) -> FactorScore:
    close = bars[-1].close
    ma20 = _mean([item.close for item in bars[-20:]])
    ma60 = _mean([item.close for item in bars[-60:]])
    if close > ma20 and close > ma60:
        score = Decimal("70")
    elif close < ma20 and close < ma60:
        score = Decimal("30")
    else:
        score = Decimal("50")
    return FactorScore(
        "trend",
        "趋势",
        Decimal("0.30"),
        score,
        f"收盘 {close}，MA20 {ma20}，MA60 {ma60}",
        "收盘相对均线",
    )


def _breadth(members: dict[str, list[PriceBar]]) -> FactorScore:
    advances = 0
    above = 0
    for bars in members.values():
        if bars[-1].close > bars[-2].close:
            advances += 1
        ma20 = _mean([item.close for item in bars[-20:]])
        if bars[-1].close > ma20:
            above += 1
    count = Decimal(len(members))
    ratio = (Decimal(advances) / count + Decimal(above) / count) / Decimal(2)
    score = (ratio * Decimal("100")).quantize(Decimal("0.1"))
    return FactorScore(
        "breadth",
        "广度",
        Decimal("0.35"),
        score,
        f"上涨占比 {Decimal(advances) / count}，高于 MA20 占比 {Decimal(above) / count}",
        "只统计样本内已有日线",
    )


def _activity(bars: list[PriceBar]) -> FactorScore | None:
    window = bars[-21:-1]
    if any(item.amount is None for item in window) or bars[-1].amount is None:
        return None
    amounts = [item.amount for item in window if item.amount is not None]
    base = median(amounts)
    if base <= 0:
        return None
    last = bars[-1].amount or Decimal(0)
    ratio = last / base
    if ratio >= Decimal("1.2"):
        score = Decimal("70")
    elif ratio <= Decimal("0.8"):
        score = Decimal("30")
    else:
        score = Decimal("50")
    return FactorScore(
        "activity", "活跃", Decimal("0.20"), score, f"成交额 / 20日中位 {ratio}", "相对近期成交额"
    )


def _volatility(bars: list[PriceBar]) -> FactorScore | None:
    returns = _returns(bars)
    if len(returns) < 40:
        return None
    latest = returns[-20:]
    current = Decimal(str(pstdev([float(item) for item in latest])))
    history: list[Decimal] = []
    for index in range(20, len(returns) + 1):
        window = returns[index - 20 : index]
        history.append(Decimal(str(pstdev([float(item) for item in window]))))
    rank = sum(1 for item in history if item <= current) / Decimal(len(history))
    if rank >= Decimal("0.8"):
        score = Decimal("30")
        note = "波动处于高位，按风险处理，不加分"
    elif rank <= Decimal("0.2"):
        score = Decimal("70")
        note = "波动处于低位"
    else:
        score = Decimal("50")
        note = "波动处于中间"
    return FactorScore("volatility", "波动", Decimal("0.15"), score, f"20日波动分位 {rank}", note)


def _return(bars: list[PriceBar]) -> Decimal | None:
    if bars[0].close <= 0:
        return None
    return (bars[-1].close / bars[0].close) - Decimal(1)


def _returns(bars: list[PriceBar]) -> list[Decimal]:
    values: list[Decimal] = []
    for previous, current in zip(bars, bars[1:], strict=False):
        if previous.close <= 0:
            continue
        values.append((current.close / previous.close) - Decimal(1))
    return values


def _ordered(bars: list[PriceBar]) -> list[PriceBar]:
    return sorted(bars, key=lambda item: item.trade_date)


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _unable(coverage: str, notice: str) -> TemperatureResult:
    return TemperatureResult("insufficient", "无法判断", None, coverage, (), notice)
