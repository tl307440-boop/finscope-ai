"""总览驾驶舱的评分与归类规则：纯函数，不联网、不读库，便于用固定样本回归。

所有评分都是 0–100，缺失因子不补值：剩余因子按权重重新归一；可用权重合计不足一半时不出分。

- 市场温度 market-temperature-v2：趋势 30% / 广度 35% / 活跃度 20% / 波动 15%，全部用全市场数据。
- 全球流动性 global-liquidity-v1（越高越松）：美联储净流动性、美元、实际利率、信用利差、
  金融条件指数、SHIBOR、中美利差。
- 全球经济热度 global-growth-v1 与宏观趋势判定（增长分 × CPI 同比动能）。
- 热点板块 board-heat-v1：同花顺热度 40% + 当日涨幅分位 30% + 涨停家数 30%。
- 公告重要度：只看标题关键词；中介意见、董事会决议等附属文件不计重要度。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
_HALF = Decimal("0.5")
_TRADING_DAYS = Decimal(250)


@dataclass(frozen=True)
class Factor:
    code: str
    label: str
    weight: Decimal
    score: Decimal
    raw: str
    formula: str
    value: Decimal | None = None


@dataclass(frozen=True)
class Composite:
    score: Decimal | None
    label: str
    factors: tuple[Factor, ...]
    missing: tuple[str, ...]
    coverage: Decimal


@dataclass(frozen=True)
class Observation:
    day: date
    value: Decimal


def clamp(value: Decimal, low: Decimal = ZERO, high: Decimal = HUNDRED) -> Decimal:
    return max(low, min(high, value))


def mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def round_to(value: Decimal, places: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def signed(value: Decimal, places: int = 2, suffix: str = "%") -> str:
    rounded = round_to(value, places)
    return f"{'+' if rounded > 0 else ''}{rounded}{suffix}"


def pct_change(last: Decimal | None, base: Decimal | None) -> Decimal | None:
    if last is None or base is None or base == 0:
        return None
    return (last / base - ONE) * HUNDRED


def composite(
    factors: Sequence[Factor], missing: Sequence[str], labels: Sequence[tuple[Decimal, str]]
) -> Composite:
    weight = sum((factor.weight for factor in factors), ZERO)
    if weight < _HALF:
        return Composite(None, "数据不足", tuple(factors), tuple(missing), weight)
    score = round_to(sum((f.score * f.weight for f in factors), ZERO) / weight, 1)
    label = next(text for floor, text in labels if score >= floor)
    return Composite(score, label, tuple(factors), tuple(missing), weight)


def contributions(result: Composite) -> tuple[Decimal | None, ...]:
    """每个因子对总分的贡献 = 得分 × 权重 / 可用权重；不出分时没有贡献可言。"""
    if result.score is None or result.coverage <= 0:
        return tuple(None for _ in result.factors)
    return tuple(round_to(f.score * f.weight / result.coverage, 2) for f in result.factors)


# ---------------------------------------------------------------- 市场温度

TEMPERATURE_LABELS: tuple[tuple[Decimal, str], ...] = (
    (Decimal(80), "过热"),
    (Decimal(65), "偏强"),
    (Decimal(55), "中性偏强"),
    (Decimal(45), "中性"),
    (Decimal(35), "中性偏弱"),
    (Decimal(20), "偏弱"),
    (ZERO, "冰点"),
)


@dataclass(frozen=True)
class Breadth:
    up: int
    down: int
    flat: int
    total: int
    strong: int
    weak: int
    limit_up: int
    limit_down: int


@dataclass(frozen=True)
class Activity:
    amount: Decimal
    base: Decimal
    ratio: Decimal
    session_fraction: Decimal
    seal_rate: Decimal | None


@dataclass(frozen=True)
class Trend:
    close: Decimal
    ma20: Decimal
    ma60: Decimal
    change_20d_pct: Decimal


@dataclass(frozen=True)
class Volatility:
    annualized_pct: Decimal
    rank: Decimal
    windows: int


@dataclass(frozen=True)
class Temperature:
    result: Composite
    breadth: Breadth | None
    activity: Activity | None
    trend: Trend | None
    volatility: Volatility | None


def breadth_from(buckets: Mapping[int, int]) -> Breadth | None:
    """档位 >0 上涨、<0 下跌、0 平盘；|档位| ≥ 4 即涨跌幅超过 3%；±11 为涨跌停。"""

    if not buckets:
        return None
    up = sum(count for key, count in buckets.items() if key > 0)
    down = sum(count for key, count in buckets.items() if key < 0)
    flat = buckets.get(0, 0)
    if up + down == 0:
        return None
    return Breadth(
        up=up,
        down=down,
        flat=flat,
        total=up + down + flat,
        strong=sum(count for key, count in buckets.items() if key >= 4),
        weak=sum(count for key, count in buckets.items() if key <= -4),
        limit_up=buckets.get(11, 0),
        limit_down=buckets.get(-11, 0),
    )


def breadth_factor(stats: Breadth) -> Factor:
    share = Decimal(stats.up) / Decimal(stats.up + stats.down) * HUNDRED
    spread = clamp(50 + Decimal(stats.strong - stats.weak) / Decimal(stats.total) * 250)
    limits = stats.limit_up + stats.limit_down
    limit_ratio = Decimal(stats.limit_up) / Decimal(limits) * HUNDRED if limits else Decimal(50)
    score = Decimal("0.5") * share + Decimal("0.25") * spread + Decimal("0.25") * limit_ratio
    return Factor(
        code="breadth",
        label="广度",
        weight=Decimal("0.35"),
        score=round_to(score, 2),
        raw=(
            f"上涨 {stats.up} / 下跌 {stats.down} / 平盘 {stats.flat}（共 {stats.total}）；"
            f"涨超 3% {stats.strong} 家，跌超 3% {stats.weak} 家；"
            f"涨停 {stats.limit_up}，跌停 {stats.limit_down}"
        ),
        formula=(
            f"0.5×上涨占比({round_to(share, 1)}) + 0.25×强弱差分({round_to(spread, 1)}) "
            f"+ 0.25×涨跌停比({round_to(limit_ratio, 1)})；强弱差分 = clamp(50 + (强−弱)/总数×250)"
        ),
    )


def activity_from(
    amounts: Sequence[Decimal],
    limit_up: int | None,
    broken: int | None,
    session_fraction: Decimal = ONE,
) -> Activity | None:
    """amounts 为按日期升序的两市成交额，最后一个是当日（盘中为累计值）。

    盘中把前 20 日均额按已交易分钟占比线性折算，避免早盘量比天然偏低。
    """

    if len(amounts) < 21 or session_fraction <= 0:
        return None
    base = mean(amounts[-21:-1])
    if base <= 0:
        return None
    scaled = base * min(session_fraction, ONE)
    seal = None
    if limit_up is not None and broken is not None and limit_up + broken > 0:
        seal = Decimal(limit_up) / Decimal(limit_up + broken) * HUNDRED
    return Activity(
        amount=amounts[-1],
        base=base,
        ratio=amounts[-1] / scaled,
        session_fraction=min(session_fraction, ONE),
        seal_rate=seal,
    )


def activity_factor(stats: Activity, limit_up: int | None, broken: int | None) -> Factor:
    volume = clamp(50 + (stats.ratio - ONE) * 125)
    if stats.seal_rate is None:
        score, seal_text, seal_score_text = volume, "—", "缺"
    else:
        seal_score = clamp((stats.seal_rate - 40) * 2)
        score = Decimal("0.7") * volume + Decimal("0.3") * seal_score
        seal_text, seal_score_text = (
            f"{round_to(stats.seal_rate, 2)}%",
            str(round_to(seal_score, 1)),
        )
    session = (
        ""
        if stats.session_fraction >= ONE
        else f"（盘中：均额按已交易 {round_to(stats.session_fraction * HUNDRED, 0)}% 折算）"
    )
    return Factor(
        code="activity",
        label="活跃度",
        weight=Decimal("0.20"),
        score=round_to(score, 2),
        raw=(
            f"两市成交额 {_yi(stats.amount)}，前 20 日均额 {_yi(stats.base)}，"
            f"量比 {round_to(stats.ratio, 3)}{session}；封板率 {seal_text}"
            f"（涨停 {_count(limit_up)} / 炸板 {_count(broken)}）"
        ),
        formula=(
            f"0.7×量能分({round_to(volume, 1)}) + 0.3×封板率分({seal_score_text})；"
            "量能分 = clamp(50 + (量比−1)×125)，封板率分 = clamp((封板率−40)×2)；"
            "缺封板率时只用量能分"
        ),
        value=stats.ratio,
    )


def trend_from(closes: Sequence[Decimal]) -> Trend | None:
    if len(closes) < 61 or closes[-21] == 0:
        return None
    return Trend(
        close=closes[-1],
        ma20=mean(closes[-20:]),
        ma60=mean(closes[-60:]),
        change_20d_pct=(closes[-1] / closes[-21] - ONE) * HUNDRED,
    )


def trend_factor(stats: Trend, index_name: str) -> Factor:
    checks = (stats.close > stats.ma20, stats.close > stats.ma60, stats.ma20 > stats.ma60)
    state = Decimal(sum(checks)) / 3 * HUNDRED
    momentum = clamp(50 + stats.change_20d_pct * 5)
    marks = "".join("✓" if passed else "✗" for passed in checks)
    return Factor(
        code="trend",
        label="趋势",
        weight=Decimal("0.30"),
        score=round_to(_HALF * state + _HALF * momentum, 2),
        raw=(
            f"{index_name} {round_to(stats.close, 2)}，MA20 {round_to(stats.ma20, 2)}，"
            f"MA60 {round_to(stats.ma60, 2)}，20 日涨跌 {signed(stats.change_20d_pct)}"
        ),
        formula=(
            f"0.5×均线状态({round_to(state, 1)}：{marks} 收>MA20 / 收>MA60 / MA20>MA60) "
            f"+ 0.5×clamp(50 + 20日涨跌%×5)({round_to(momentum, 1)})"
        ),
        value=stats.change_20d_pct,
    )


def volatility_from(closes: Sequence[Decimal]) -> Volatility | None:
    """20 日收益率标准差（总体）在最近至多 250 个滚动窗口里的分位（≤ 当前的占比）。"""

    if len(closes) < 60 or any(close == 0 for close in closes[:-1]):
        return None
    returns = [closes[index + 1] / closes[index] - ONE for index in range(len(closes) - 1)]
    deviations: list[Decimal] = []
    for end in range(20, len(returns) + 1):
        window = returns[end - 20 : end]
        average = mean(window)
        deviations.append(mean([(value - average) ** 2 for value in window]).sqrt())
    recent = deviations[-250:]
    current = recent[-1]
    rank = Decimal(sum(1 for value in recent if value <= current)) / Decimal(len(recent))
    return Volatility(
        annualized_pct=current * _TRADING_DAYS.sqrt() * HUNDRED, rank=rank, windows=len(recent)
    )


def volatility_factor(stats: Volatility) -> Factor:
    return Factor(
        code="volatility",
        label="波动",
        weight=Decimal("0.15"),
        score=round_to(clamp(HUNDRED - stats.rank * HUNDRED), 2),
        raw=(
            f"20 日年化波动 {round_to(stats.annualized_pct, 2)}%，"
            f"处于近 {stats.windows} 个窗口的 {round_to(stats.rank * HUNDRED, 1)}% 分位"
        ),
        formula="100 − 分位×100（波动处于高位时得分低）",
        value=stats.annualized_pct,
    )


def market_temperature(
    *,
    buckets: Mapping[int, int] | None,
    amounts: Sequence[Decimal],
    limit_up: int | None,
    broken: int | None,
    closes: Sequence[Decimal],
    index_name: str = "中证全指",
    session_fraction: Decimal = ONE,
) -> Temperature:
    factors: list[Factor] = []
    missing: list[str] = []
    trend = trend_from(closes)
    if trend is not None:
        factors.append(trend_factor(trend, index_name))
    else:
        missing.append(f"趋势（{index_name}日线不足 61 根）")
    breadth = breadth_from(buckets or {})
    if breadth is not None:
        factors.append(breadth_factor(breadth))
    else:
        missing.append("广度（全市场涨跌分布不可用）")
    activity = activity_from(amounts, limit_up, broken, session_fraction)
    if activity is not None:
        factors.append(activity_factor(activity, limit_up, broken))
    else:
        missing.append("活跃度（两市成交额历史不足 21 日）")
    volatility = volatility_from(closes)
    if volatility is not None:
        factors.append(volatility_factor(volatility))
    else:
        missing.append(f"波动（{index_name}日线不足 60 根）")
    return Temperature(
        result=composite(factors, missing, TEMPERATURE_LABELS),
        breadth=breadth,
        activity=activity,
        trend=trend,
        volatility=volatility,
    )


def session_fraction(minutes_traded: int) -> Decimal:
    """A 股连续竞价共 240 分钟；收盘后为 1。"""

    return clamp(Decimal(minutes_traded) / Decimal(240), ZERO, ONE)


# ---------------------------------------------------------------- 全球流动性

LIQUIDITY_LABELS: tuple[tuple[Decimal, str], ...] = (
    (Decimal(65), "偏松"),
    (Decimal(55), "中性偏松"),
    (Decimal(45), "中性"),
    (Decimal(35), "中性偏紧"),
    (ZERO, "偏紧"),
)


@dataclass(frozen=True)
class YieldPoint:
    day: date
    cn10y: Decimal
    us10y: Decimal


@dataclass(frozen=True)
class LiquidityInputs:
    walcl: Sequence[Observation] = ()
    tga: Sequence[Observation] = ()
    rrp: Sequence[Observation] = ()
    dollar: Sequence[Observation] = ()
    real_yield: Sequence[Observation] = ()
    hy_spread: Sequence[Observation] = ()
    nfci: Sequence[Observation] = ()
    shibor_on: Sequence[Observation] = ()
    yields: Sequence[YieldPoint] = ()


@dataclass(frozen=True)
class Liquidity:
    result: Composite
    net_liquidity: tuple[Observation, ...]


def net_liquidity(
    walcl: Sequence[Observation], tga: Sequence[Observation], rrp: Sequence[Observation]
) -> tuple[Observation, ...]:
    """净流动性（十亿美元）= WALCL − WTREGEN − RRPONTSYD×1000，前两者百万美元、后者十亿美元。

    TGA 与逆回购取不晚于该周三的最近观测。
    """

    rows: list[Observation] = []
    for point in walcl:
        treasury = _on_or_before(tga, point.day)
        reverse = _on_or_before(rrp, point.day)
        if treasury is None or reverse is None:
            continue
        rows.append(Observation(point.day, (point.value - treasury - reverse * 1000) / 1000))
    return tuple(rows)


def global_liquidity(inputs: LiquidityInputs) -> Liquidity:
    factors: list[Factor] = []
    missing: list[str] = []
    net = net_liquidity(inputs.walcl, inputs.tga, inputs.rrp)
    if len(net) > 4:
        last, prev = net[-1], net[-5]
        delta = last.value - prev.value
        factors.append(
            Factor(
                code="fed_net",
                label="美联储净流动性 4 周变化",
                weight=Decimal("0.25"),
                score=round_to(clamp(50 + delta / 4), 2),
                raw=(
                    f"{last.day} 净流动性 {round_to(last.value, 0)} 十亿美元，"
                    f"较 {prev.day} {signed(delta, 0, ' 十亿美元')}"
                ),
                formula=(
                    "净流动性 = WALCL − WTREGEN − RRPONTSYD×1000；"
                    "得分 = clamp(50 + Δ4周(十亿美元)/4)"
                ),
                value=delta,
            )
        )
    else:
        missing.append("美联储净流动性（WALCL / WTREGEN / RRPONTSYD 不足）")
    _relative(
        factors,
        missing,
        inputs.dollar,
        code="DTWEXBGS",
        label="美元指数（广义）20 期变化",
        weight=Decimal("0.15"),
        multiplier=Decimal(-10),
        formula="得分 = clamp(50 − 20期涨跌%×10)：美元走强 = 全球美元流动性收紧",
    )
    _bp_change(
        factors,
        missing,
        inputs.real_yield,
        code="DFII10",
        label="美债 10Y 实际利率 20 期变化",
        weight=Decimal("0.15"),
        multiplier=Decimal(-1),
        formula="得分 = clamp(50 − Δbp×1)：实际利率上行 = 收紧",
    )
    _bp_change(
        factors,
        missing,
        inputs.hy_spread,
        code="BAMLH0A0HYM2",
        label="美国高收益债利差 20 期变化",
        weight=Decimal("0.10"),
        multiplier=Decimal("-0.5"),
        formula="得分 = clamp(50 − Δbp×0.5)：信用利差走阔 = 收紧",
    )
    if inputs.nfci:
        last = inputs.nfci[-1]
        factors.append(
            Factor(
                code="NFCI",
                label="芝加哥联储金融条件指数",
                weight=Decimal("0.10"),
                score=round_to(clamp(50 - last.value * 50), 2),
                raw=f"{last.day} NFCI = {last.value}（负值 = 金融条件宽于历史均值）",
                formula="得分 = clamp(50 − NFCI×50)",
                value=last.value,
            )
        )
    else:
        missing.append("NFCI")
    shibor = inputs.shibor_on
    if len(shibor) >= 40:
        recent = mean([row.value for row in shibor[-20:]])
        before = mean([row.value for row in shibor[-40:-20]])
        delta = (recent - before) * HUNDRED
        factors.append(
            Factor(
                code="SHIBOR_ON",
                label="SHIBOR 隔夜 20 日均值变化",
                weight=Decimal("0.15"),
                score=round_to(clamp(50 - delta), 2),
                raw=(
                    f"近 20 日均值 {round_to(recent, 3)}%，前 20 日均值 {round_to(before, 3)}%，"
                    f"变化 {signed(delta, 1, ' bp')}；最新 {shibor[-1].day} {shibor[-1].value}%"
                ),
                formula="得分 = clamp(50 − Δbp)：国内资金价格下行 = 宽松",
                value=delta,
            )
        )
    else:
        missing.append("SHIBOR 隔夜（不足 40 个交易日）")
    yields = inputs.yields
    if len(yields) > 20:
        last_yield, prev_yield = yields[-1], yields[-21]
        spread_now = (last_yield.cn10y - last_yield.us10y) * HUNDRED
        spread_then = (prev_yield.cn10y - prev_yield.us10y) * HUNDRED
        delta = spread_now - spread_then
        factors.append(
            Factor(
                code="CN_US_10Y",
                label="中美 10Y 利差 20 日变化",
                weight=Decimal("0.10"),
                score=round_to(clamp(50 + delta), 2),
                raw=(
                    f"{last_yield.day} 中国10Y {last_yield.cn10y}% − 美国10Y {last_yield.us10y}% "
                    f"= {round_to(spread_now, 0)} bp，较 {prev_yield.day} {signed(delta, 0, ' bp')}"
                ),
                formula="得分 = clamp(50 + Δbp)：倒挂收窄 = 人民币资产的外部约束放松",
                value=delta,
            )
        )
    else:
        missing.append("中美 10Y 利差")
    return Liquidity(result=composite(factors, missing, LIQUIDITY_LABELS), net_liquidity=net)


def _relative(
    factors: list[Factor],
    missing: list[str],
    series: Sequence[Observation],
    *,
    code: str,
    label: str,
    weight: Decimal,
    multiplier: Decimal,
    formula: str,
) -> None:
    if len(series) <= 20 or series[-21].value == 0:
        missing.append(label)
        return
    last, prev = series[-1], series[-21]
    delta = (last.value / prev.value - ONE) * HUNDRED
    factors.append(
        Factor(
            code=code,
            label=label,
            weight=weight,
            score=round_to(clamp(50 + delta * multiplier), 2),
            raw=(
                f"{last.day} {last.value}，较 20 个观测前（{prev.day} {prev.value}）{signed(delta)}"
            ),
            formula=formula,
            value=delta,
        )
    )


def _bp_change(
    factors: list[Factor],
    missing: list[str],
    series: Sequence[Observation],
    *,
    code: str,
    label: str,
    weight: Decimal,
    multiplier: Decimal,
    formula: str,
) -> None:
    if len(series) <= 20:
        missing.append(label)
        return
    last, prev = series[-1], series[-21]
    delta = (last.value - prev.value) * HUNDRED
    factors.append(
        Factor(
            code=code,
            label=label,
            weight=weight,
            score=round_to(clamp(50 + delta * multiplier), 2),
            raw=(
                f"{last.day} {last.value}%，较 20 个观测前（{prev.day} {prev.value}%）"
                f"{signed(delta, 0, ' bp')}"
            ),
            formula=formula,
            value=delta,
        )
    )


def _on_or_before(series: Sequence[Observation], day: date) -> Decimal | None:
    found: Decimal | None = None
    for row in series:
        if row.day > day:
            break
        found = row.value
    return found


# ---------------------------------------------------------------- 全球经济热度与宏观趋势

GROWTH_LABELS: tuple[tuple[Decimal, str], ...] = (
    (Decimal(60), "偏热"),
    (Decimal(45), "中性"),
    (ZERO, "偏冷"),
)


@dataclass(frozen=True)
class Growth:
    result: Composite
    cpi_yoy: Decimal | None
    cpi_prev_yoy: Decimal | None
    inflation_momentum: Decimal | None
    regime: str


def year_over_year(series: Sequence[Observation], index: int) -> Decimal | None:
    point = series[index]
    target = (point.day.year - 1, point.day.month)
    base = next((row for row in series if (row.day.year, row.day.month) == target), None)
    return None if base is None else pct_change(point.value, base.value)


def global_growth(
    *,
    unemployment: Sequence[Observation],
    payrolls: Sequence[Observation],
    china_pmi: Sequence[Observation],
    cpi: Sequence[Observation],
) -> Growth:
    factors: list[Factor] = []
    missing: list[str] = []
    if len(unemployment) > 3:
        last, prev = unemployment[-1], unemployment[-4]
        delta = last.value - prev.value
        factors.append(
            Factor(
                code="UNRATE",
                label="美国失业率 3 个月变化",
                weight=Decimal("0.3"),
                score=round_to(clamp(50 - delta * 100), 2),
                raw=(
                    f"{last.day:%Y-%m} {last.value}%，3 个月前 {prev.value}%，"
                    f"变化 {signed(delta, 2, ' pp')}"
                ),
                formula="得分 = clamp(50 − Δpp×100)",
                value=delta,
            )
        )
    else:
        missing.append("美国失业率")
    if len(payrolls) > 3:
        adds = [payrolls[-index].value - payrolls[-index - 1].value for index in (1, 2, 3)]
        average = mean(adds)
        factors.append(
            Factor(
                code="PAYEMS",
                label="美国非农近 3 月平均新增",
                weight=Decimal("0.3"),
                score=round_to(clamp(50 + (average - 100) * Decimal("0.25")), 2),
                raw=(
                    "近 3 个月新增 "
                    + " / ".join(str(round_to(value, 0)) for value in adds)
                    + f" 千人，均值 {round_to(average, 0)} 千人"
                ),
                formula="得分 = clamp(50 + (均值−100)×0.25)",
                value=average,
            )
        )
    else:
        missing.append("美国非农")
    if china_pmi:
        last = china_pmi[-1]
        factors.append(
            Factor(
                code="CN_PMI",
                label="中国制造业 PMI",
                weight=Decimal("0.4"),
                score=round_to(clamp(50 + (last.value - 50) * 10), 2),
                raw=f"{last.day:%Y-%m} PMI {last.value}（50 为荣枯线）",
                formula="得分 = clamp(50 + (PMI−50)×10)",
                value=last.value,
            )
        )
    else:
        missing.append("中国制造业 PMI")
    result = composite(factors, missing, GROWTH_LABELS)
    cpi_yoy = cpi_prev = momentum = None
    if len(cpi) > 16:
        cpi_yoy = year_over_year(cpi, len(cpi) - 1)
        cpi_prev = year_over_year(cpi, len(cpi) - 2)
        three_back = year_over_year(cpi, len(cpi) - 4)
        if cpi_yoy is not None and three_back is not None:
            momentum = cpi_yoy - three_back
    return Growth(
        result=result,
        cpi_yoy=cpi_yoy,
        cpi_prev_yoy=cpi_prev,
        inflation_momentum=momentum,
        regime=macro_regime(result.score, momentum),
    )


def macro_regime(growth: Decimal | None, momentum: Decimal | None) -> str:
    """增长分 ≥55 扩张、≤45 放缓；通胀动能 = CPI 同比较 3 个月前的变化（pp）。"""

    if growth is None or momentum is None:
        return "无法判断"
    if growth >= 55:
        return "扩张伴随通胀升温" if momentum > 0 else "温和扩张"
    if growth <= 45:
        return "滞胀压力" if momentum > 0 else "增长放缓"
    if momentum > Decimal("0.2"):
        return "通胀回升"
    if momentum < Decimal("-0.2"):
        return "平稳降温"
    return "平稳"


# ---------------------------------------------------------------- 热点板块

_LIMIT_TAG = re.compile(r"(\d+)家涨停")


@dataclass(frozen=True)
class PlateInput:
    code: str
    name: str
    kind: str
    heat: Decimal | None
    change_pct: Decimal | None
    tag: str | None = None
    hot_tag: str | None = None


@dataclass(frozen=True)
class PlateHeat:
    plate: PlateInput
    limit_ups: int
    heat_score: Decimal
    change_score: Decimal
    limit_score: Decimal
    index: int
    hot: bool


def board_heat(plates: Sequence[PlateInput]) -> list[PlateHeat]:
    """热度指数 = 0.4×热度分 + 0.3×涨幅分位 + 0.3×涨停家数分；热点 = 指数 ≥ 60 且涨幅 ≥ 1%。

    热度分 = 同花顺热度 / 列表内最高热度×100；涨幅分位 = 涨幅 ≤ 本板块的占比；
    涨停家数取同花顺标签「N家涨停」，10 家封顶。
    """

    if not plates:
        return []
    top = max((plate.heat or ZERO for plate in plates), default=ZERO) or ONE
    changes = sorted(plate.change_pct for plate in plates if plate.change_pct is not None)
    rows: list[PlateHeat] = []
    for plate in plates:
        match = _LIMIT_TAG.search(plate.tag or "")
        limit_ups = int(match.group(1)) if match else 0
        heat_score = (plate.heat or ZERO) / top * HUNDRED
        change_score = (
            Decimal(sum(1 for value in changes if value <= plate.change_pct))
            / Decimal(len(changes))
            * HUNDRED
            if plate.change_pct is not None and changes
            else ZERO
        )
        limit_score = min(Decimal(limit_ups) / 10, ONE) * HUNDRED
        index = int(
            round_to(
                Decimal("0.4") * heat_score
                + Decimal("0.3") * change_score
                + Decimal("0.3") * limit_score,
                0,
            )
        )
        rows.append(
            PlateHeat(
                plate=plate,
                limit_ups=limit_ups,
                heat_score=round_to(heat_score, 1),
                change_score=round_to(change_score, 1),
                limit_score=round_to(limit_score, 1),
                index=index,
                hot=index >= 60 and (plate.change_pct or ZERO) >= ONE,
            )
        )
    return sorted(rows, key=lambda row: row.index, reverse=True)


# ---------------------------------------------------------------- 自选强弱


def watch_state(change_5d_pct: Decimal | None) -> str | None:
    """近 5 个交易日涨跌 ≥ +3% 强势，≤ −3% 弱势，其余震荡；没有 5 日数据不归类。"""

    if change_5d_pct is None:
        return None
    if change_5d_pct >= 3:
        return "强势"
    if change_5d_pct <= -3:
        return "弱势"
    return "震荡"


def watch_signals(
    *,
    change_pct: Decimal | None,
    limit_status: str | None,
    streak: int | None,
    ths_hot_rank: int | None,
) -> list[tuple[str, str]]:
    """(tone, 文案)：涨停 / 连板 / 炸板 / 跌停 / 热榜名次；非涨跌停且日涨跌超 5% 记为异动。"""

    signals: list[tuple[str, str]] = []
    if limit_status == "涨停":
        signals.append(("rise", f"{streak}连板" if streak and streak > 1 else "涨停"))
    elif limit_status == "炸板":
        signals.append(("warn", "炸板"))
    elif limit_status == "跌停":
        signals.append(("fall", "跌停"))
    if ths_hot_rank is not None:
        signals.append(("violet", f"热榜#{ths_hot_rank}"))
    if change_pct is not None and abs(change_pct) >= 5 and limit_status not in ("涨停", "跌停"):
        signals.append(("rise" if change_pct > 0 else "fall", "异动"))
    return signals


# ---------------------------------------------------------------- 公告

ANNOUNCEMENT_RULES: tuple[tuple[str, int, str, re.Pattern[str]], ...] = (
    ("质押", 1, "muted", re.compile(r"质押")),
    (
        "风险",
        3,
        "fall",
        re.compile(r"立案|处罚|警示函|退市|风险警示|终止上市|违规|司法冻结|重大诉讼|仲裁"),
    ),
    (
        "重组",
        3,
        "violet",
        re.compile(
            r"重大资产重组|发行股份购买|吸收合并|控制权|实际控制人.*变更|要约收购|收购.*股权"
        ),
    ),
    ("业绩", 3, "rise", re.compile(r"业绩预告|业绩快报|预增|预减|扭亏|首亏")),
    ("定期报告", 2, "", re.compile(r"季度报告|半年度报告|年度报告")),
    ("问询", 2, "warn", re.compile(r"问询函|关注函|监管函|监管工作函")),
    ("增持回购", 2, "rise", re.compile(r"回购|增持")),
    ("减持", 2, "fall", re.compile(r"减持")),
    ("合同订单", 2, "", re.compile(r"中标|重大合同|签订.*(合同|协议)|项目定点")),
    ("分红", 1, "", re.compile(r"分红|权益分派|利润分配")),
    ("股权激励", 1, "", re.compile(r"股权激励|员工持股")),
)
ANNOUNCEMENT_ROUTINE = re.compile(
    r"法律意见书|核查意见|评估报告|审计报告|审阅报告|董事会关于|监事会关于|的说明|董事会决议|"
    r"监事会决议|独立董事|会议资料|提示性公告|投资者关系|调研活动|章程|制度|保荐|专项说明|摘要|英文版"
)


@dataclass(frozen=True)
class AnnouncementClass:
    kind: str
    level: int
    tone: str


def classify_announcement(title: str) -> AnnouncementClass:
    """只匹配标题；先排除附属文件，再按规则顺序取第一条（质押最先，避免「解除质押」被归为风险）。"""

    if ANNOUNCEMENT_ROUTINE.search(title):
        return AnnouncementClass("常规", 0, "muted")
    for kind, level, tone, pattern in ANNOUNCEMENT_RULES:
        if pattern.search(title):
            return AnnouncementClass(kind, level, tone)
    return AnnouncementClass("其他", 0, "muted")


def clean_title(title: str, code: str, name: str) -> str:
    text = title.strip()
    for part in (code, name):
        if part and text.startswith(part):
            text = text[len(part) :].lstrip(" :：")
    return text


@dataclass(frozen=True)
class StockHeat:
    level: int
    reason: str


@dataclass
class HeatBoard:
    """股票热度：同花顺热榜前 20 记 3 级、前 100 记 2 级；涨停 / 跌停 / 涨幅榜前 40 记 1 级。"""

    entries: dict[str, StockHeat] = field(default_factory=dict)

    def bump(self, code: str, level: int, reason: str) -> None:
        current = self.entries.get(code)
        if current is None or current.level < level:
            self.entries[code] = StockHeat(level, reason)


def stock_heat(
    *,
    ths_ranks: Sequence[tuple[str, int]],
    limit_up: Sequence[tuple[str, int | None]],
    limit_down: Sequence[str],
    gainers: Sequence[str],
) -> dict[str, StockHeat]:
    board = HeatBoard()
    for code, rank in ths_ranks:
        board.bump(code, 3 if rank <= 20 else 2, f"同花顺热榜第 {rank}")
    for code, streak in limit_up:
        board.bump(code, 1, f"{streak}连板" if streak and streak > 1 else "涨停")
    for code in limit_down:
        board.bump(code, 1, "跌停")
    for code in gainers[:40]:
        board.bump(code, 1, "涨幅榜前 40")
    return board.entries


def _yi(value: Decimal) -> str:
    if value >= Decimal("1e12"):
        return f"{round_to(value / Decimal('1e12'), 2)} 万亿元"
    return f"{round_to(value / Decimal('1e8'), 0)} 亿元"


def _count(value: int | None) -> str:
    return "—" if value is None else str(value)
