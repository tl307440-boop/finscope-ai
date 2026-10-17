"""板块分析的纯计算规则：无副作用、不发请求，输入输出都带单位。

版本号写进响应，口径变了就升版本，便于论文复现。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.providers.eastmoney_boards import BoardBar, BoardQuote, MemberQuote, ValuationPoint

ANALYTICS_VERSION = "board-analytics/2"
_HUNDRED = Decimal(100)
_TWO_PLACES = Decimal("0.01")

# 涨跌分布的分档（百分数，左闭右开；0 单独一档）。和同花顺 / 东财的「涨跌分布」口径接近。
DISTRIBUTION_EDGES: tuple[tuple[str, Decimal | None, Decimal | None], ...] = (
    ("≤-7%", None, Decimal(-7)),
    ("-7~-5%", Decimal(-7), Decimal(-5)),
    ("-5~-3%", Decimal(-5), Decimal(-3)),
    ("-3~0%", Decimal(-3), Decimal(0)),
    ("平", Decimal(0), Decimal(0)),
    ("0~3%", Decimal(0), Decimal(3)),
    ("3~5%", Decimal(3), Decimal(5)),
    ("5~7%", Decimal(5), Decimal(7)),
    ("≥7%", Decimal(7), None),
)


@dataclass(frozen=True)
class CurvePoint:
    trade_date: date
    board_index: Decimal
    benchmark_index: Decimal
    excess_pct: Decimal


@dataclass(frozen=True)
class PercentileSummary:
    current: Decimal
    percentile: Decimal
    minimum: Decimal
    p20: Decimal
    median: Decimal
    p80: Decimal
    maximum: Decimal
    samples: int
    start: date
    end: date


@dataclass(frozen=True)
class DistributionBucket:
    label: str
    count: int
    tone: str


@dataclass(frozen=True)
class SubIndustry:
    name: str
    members: int
    market_cap: Decimal
    weighted_change_pct: Decimal | None
    main_net_inflow: Decimal


@dataclass(frozen=True)
class Breadth:
    boards: int
    boards_up: int
    boards_down: int
    stocks_up: int
    stocks_down: int
    stocks_flat: int
    total_main_net_inflow: Decimal


def moving_average(closes: Sequence[Decimal], window: int) -> list[Decimal | None]:
    if window < 1:
        raise ValueError("window 必须至少为 1")
    averages: list[Decimal | None] = []
    running = Decimal(0)
    for index, close in enumerate(closes):
        running += close
        if index >= window:
            running -= closes[index - window]
        averages.append(_round(running / window) if index >= window - 1 else None)
    return averages


def window_return_pct(bars: Sequence[BoardBar], sessions: int) -> Decimal | None:
    """最近一根收盘相对 `sessions` 个交易日前收盘的涨跌幅（%）；样本不够返回 None。"""

    if sessions < 1 or len(bars) <= sessions:
        return None
    base = bars[-1 - sessions].close
    if base == 0:
        return None
    return _round((bars[-1].close / base - 1) * _HUNDRED)


def relative_curve(board: Sequence[BoardBar], benchmark: Sequence[BoardBar]) -> list[CurvePoint]:
    """板块与基准按共同交易日对齐，各自以首日为 100，超额 = 板块指数 - 基准指数（百分点）。"""

    bench_by_day = {bar.trade_date: bar.close for bar in benchmark}
    pairs = [
        (bar.trade_date, bar.close, bench_by_day[bar.trade_date])
        for bar in board
        if bar.trade_date in bench_by_day
    ]
    if not pairs or pairs[0][1] == 0 or pairs[0][2] == 0:
        return []
    board_base, bench_base = pairs[0][1], pairs[0][2]
    curve: list[CurvePoint] = []
    for day, board_close, bench_close in pairs:
        board_index = board_close / board_base * _HUNDRED
        bench_index = bench_close / bench_base * _HUNDRED
        curve.append(
            CurvePoint(
                trade_date=day,
                board_index=_round(board_index),
                benchmark_index=_round(bench_index),
                excess_pct=_round(board_index - bench_index),
            )
        )
    return curve


def percentile_summary(
    points: Iterable[ValuationPoint], *, field: str, since: date
) -> PercentileSummary | None:
    """当前估值在 `since` 起历史样本里的分位（≤ 当前值的样本占比）。亏损导致的负 PE 不参与。"""

    series = sorted(
        (
            (point.trade_date, value)
            for point in points
            if point.trade_date >= since
            and (value := _metric(point, field)) is not None
            and value > 0
        ),
        key=lambda item: item[0],
    )
    if len(series) < 20:
        return None
    values = sorted(value for _, value in series)
    current = series[-1][1]
    at_or_below = sum(1 for value in values if value <= current)
    return PercentileSummary(
        current=_round(current),
        percentile=_round(Decimal(at_or_below) / Decimal(len(values)) * _HUNDRED),
        minimum=_round(values[0]),
        p20=_round(_quantile(values, Decimal("0.2"))),
        median=_round(_quantile(values, Decimal("0.5"))),
        p80=_round(_quantile(values, Decimal("0.8"))),
        maximum=_round(values[-1]),
        samples=len(values),
        start=series[0][0],
        end=series[-1][0],
    )


def change_distribution(members: Iterable[MemberQuote]) -> tuple[list[DistributionBucket], int]:
    """返回各涨跌档的家数与停牌/无报价家数。"""

    counts = [0 for _ in DISTRIBUTION_EDGES]
    suspended = 0
    for member in members:
        change = member.change_pct
        if change is None:
            suspended += 1
            continue
        counts[_bucket_index(change)] += 1
    buckets = [
        DistributionBucket(label=label, count=counts[index], tone=_bucket_tone(low, high))
        for index, (label, low, high) in enumerate(DISTRIBUTION_EDGES)
    ]
    return buckets, suspended


def sub_industries(members: Iterable[MemberQuote]) -> list[SubIndustry]:
    """按成分股自带的所属行业聚合；涨跌幅按总市值加权，缺市值或缺涨跌幅的成分不进加权。"""

    groups: dict[str, list[MemberQuote]] = {}
    for member in members:
        if member.sub_industry:
            groups.setdefault(member.sub_industry, []).append(member)
    result: list[SubIndustry] = []
    for name, rows in groups.items():
        cap = sum((row.market_cap or Decimal(0) for row in rows), Decimal(0))
        weighted = [
            (row.market_cap, row.change_pct)
            for row in rows
            if row.market_cap and row.change_pct is not None
        ]
        weight = sum((cap_value for cap_value, _ in weighted), Decimal(0))
        change = (
            _round(sum((cap_value * pct for cap_value, pct in weighted), Decimal(0)) / weight)
            if weight
            else None
        )
        inflow = sum((row.main_net_inflow or Decimal(0) for row in rows), Decimal(0))
        result.append(
            SubIndustry(
                name=name,
                members=len(rows),
                market_cap=cap,
                weighted_change_pct=change,
                main_net_inflow=inflow,
            )
        )
    return sorted(result, key=lambda item: item.market_cap, reverse=True)


def breadth(quotes: Iterable[BoardQuote]) -> Breadth:
    rows = list(quotes)
    return Breadth(
        boards=len(rows),
        boards_up=sum(1 for row in rows if row.change_pct is not None and row.change_pct > 0),
        boards_down=sum(1 for row in rows if row.change_pct is not None and row.change_pct < 0),
        stocks_up=sum(row.advancers or 0 for row in rows),
        stocks_down=sum(row.decliners or 0 for row in rows),
        stocks_flat=sum(row.unchanged or 0 for row in rows),
        total_main_net_inflow=sum((row.main_net_inflow or Decimal(0) for row in rows), Decimal(0)),
    )


@dataclass(frozen=True)
class RotationValue:
    trade_date: date
    rs_ratio: Decimal
    rs_momentum: Decimal


def rotation_tail(
    board: Sequence[BoardBar],
    benchmark: Sequence[BoardBar],
    *,
    window: int,
    step: int,
    points: int,
) -> list[RotationValue]:
    """相对轮动图（RRG）的常用开源近似，不是 JdK 原版专有公式。

    RS = 板块收盘 / 基准收盘；RS-Ratio = 100 + RS 相对其 window 日均值的 z 分数；
    RS-Momentum = 100 + RS-Ratio 日变化率相对其 window 日均值的 z 分数。
    从最后一个交易日往前每隔 step 个交易日取一个点，最多 points 个，按时间先后返回。
    """

    if window < 2 or step < 1 or points < 1:
        raise ValueError("window ≥ 2、step ≥ 1、points ≥ 1")
    bench_by_day = {bar.trade_date: bar.close for bar in benchmark}
    pairs = [
        (bar.trade_date, bar.close / bench_by_day[bar.trade_date])
        for bar in board
        if bench_by_day.get(bar.trade_date)
    ]
    needed = 2 * window + step * (points - 1) + 1
    pairs = pairs[-needed:]
    days = [day for day, _ in pairs]
    ratios = _rolling_z([rs for _, rs in pairs], window)
    changes: list[Decimal | None] = [None]
    for prev, cur in zip(ratios, ratios[1:], strict=False):
        changes.append(
            None if prev is None or cur is None or prev == 0 else (cur / prev - 1) * _HUNDRED
        )
    momentum = _rolling_z(changes, window)
    tail: list[RotationValue] = []
    index = len(days) - 1
    while index >= 0 and len(tail) < points:
        ratio, mom = ratios[index], momentum[index]
        if ratio is None or mom is None:
            break
        tail.append(
            RotationValue(trade_date=days[index], rs_ratio=_round(ratio), rs_momentum=_round(mom))
        )
        index -= step
    return list(reversed(tail))


def rotation_quadrant(rs_ratio: Decimal, rs_momentum: Decimal) -> str:
    if rs_ratio >= _HUNDRED:
        return "领先" if rs_momentum >= _HUNDRED else "转弱"
    return "改善" if rs_momentum >= _HUNDRED else "落后"


def _rolling_z(values: Sequence[Decimal | None], window: int) -> list[Decimal | None]:
    result: list[Decimal | None] = []
    for index, value in enumerate(values):
        sample = values[index - window + 1 : index + 1] if index >= window - 1 else []
        if value is None or len(sample) < window or any(item is None for item in sample):
            result.append(None)
            continue
        numbers = [item for item in sample if item is not None]
        mean = sum(numbers, Decimal(0)) / window
        spread = (sum(((item - mean) ** 2 for item in numbers), Decimal(0)) / window).sqrt()
        result.append(None if spread == 0 else _HUNDRED + (value - mean) / spread)
    return result


def _metric(point: ValuationPoint, field: str) -> Decimal | None:
    if field == "pe_ttm":
        return point.pe_ttm
    if field == "pb_mrq":
        return point.pb_mrq
    if field == "ps_ttm":
        return point.ps_ttm
    raise ValueError(f"不支持的估值字段 {field}")


def _quantile(sorted_values: Sequence[Decimal], q: Decimal) -> Decimal:
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


def _bucket_index(change: Decimal) -> int:
    if change == 0:
        return 4
    for index, (_, low, high) in enumerate(DISTRIBUTION_EDGES):
        if index != 4 and (low is None or change >= low) and (high is None or change < high):
            return index
    return len(DISTRIBUTION_EDGES) - 1


def _bucket_tone(low: Decimal | None, high: Decimal | None) -> str:
    if low == 0 and high == 0:
        return "flat"
    if high is not None and high <= 0:
        return "down"
    return "up"


def _round(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
