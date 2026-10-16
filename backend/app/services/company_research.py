from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.services.financial_metrics import MetricResult, year_over_year

CHANGE_SCALE = Decimal("0.000001")


@dataclass(frozen=True)
class SourcedAmount:
    concept: str
    unit: str
    value: Decimal
    period_end: date
    filed: date
    form: str
    accession_no: str
    source_url: str
    period_start: date | None = None

    @property
    def span(self) -> int | None:
        return None if self.period_start is None else (self.period_end - self.period_start).days


def comparable(left: SourcedAmount, right: SourcedAmount) -> bool:
    """同为时点，或期间长度相差不超过 20 天（单季对单季、全年对全年）。"""
    if left.span is None or right.span is None:
        return left.span == right.span
    return abs(left.span - right.span) <= 20


@dataclass(frozen=True)
class ComparedMetric:
    concept: str
    unit: str
    earlier: SourcedAmount | None
    later: SourcedAmount | None
    change: MetricResult


@dataclass(frozen=True)
class ResearchRule:
    rule_id: str
    status: str
    rationale: str


@dataclass(frozen=True)
class ResearchComparison:
    earlier_period: date
    later_period: date
    metrics: tuple[ComparedMetric, ...]
    highlights: tuple[ResearchRule, ...]
    risks: tuple[ResearchRule, ...]
    unevaluated: tuple[ResearchRule, ...]


def compare_periods(
    facts: tuple[SourcedAmount, ...] | list[SourcedAmount],
    period_a: date,
    period_b: date,
) -> ResearchComparison:
    earlier, later = (period_a, period_b) if period_a <= period_b else (period_b, period_a)
    keys = sorted(
        {(item.concept, item.unit) for item in facts if item.period_end in {earlier, later}}
    )
    metrics = tuple(_compare(facts, key[0], key[1], earlier, later) for key in keys)
    revenue = next(
        (item for item in metrics if item.unit == "USD" and item.concept.endswith("Revenues")),
        None,
    )
    return ResearchComparison(
        earlier_period=earlier,
        later_period=later,
        metrics=metrics,
        highlights=_highlights(revenue),
        risks=(),
        unevaluated=_unevaluated(revenue),
    )


def _compare(
    facts: tuple[SourcedAmount, ...] | list[SourcedAmount],
    concept: str,
    unit: str,
    earlier: date,
    later: date,
) -> ComparedMetric:
    lefts = _candidates(facts, concept, unit, earlier)
    rights = _candidates(facts, concept, unit, later)
    pairs = [(left, right) for right in rights for left in lefts if comparable(left, right)]
    left: SourcedAmount | None
    right: SourcedAmount | None
    if pairs:
        left, right = max(pairs, key=lambda pair: (_short_first(pair[1]), pair[1].filed))
    else:
        left = max(lefts, key=lambda item: item.filed) if lefts else None
        right = max(rights, key=lambda item: item.filed) if rights else None
    change = _change(left, right)
    return ComparedMetric(concept=concept, unit=unit, earlier=left, later=right, change=change)


def _candidates(
    facts: tuple[SourcedAmount, ...] | list[SourcedAmount],
    concept: str,
    unit: str,
    period_end: date,
) -> list[SourcedAmount]:
    return [
        item
        for item in facts
        if item.concept == concept and item.unit == unit and item.period_end == period_end
    ]


def _short_first(item: SourcedAmount) -> int:
    """同一期末既有单季又有累计时优先单季，时点数排在最前。"""
    return -(item.span or 0)


def _change(earlier: SourcedAmount | None, later: SourcedAmount | None) -> MetricResult:
    if earlier is None or later is None:
        return MetricResult(value=None, reason="insufficient_data", display=None)
    if not comparable(earlier, later):
        return MetricResult(value=None, reason="incomparable_period", display=None)
    result = year_over_year(later.value, earlier.value)
    if result.value is None:
        return result
    return replace(result, value=result.value.quantize(CHANGE_SCALE, rounding=ROUND_HALF_UP))


def _highlights(revenue: ComparedMetric | None) -> tuple[ResearchRule, ...]:
    del revenue
    return ()


def _unevaluated(revenue: ComparedMetric | None) -> tuple[ResearchRule, ...]:
    revenue_note = "收入变化显示在指标行，但不能单独当成积极信号"
    if revenue is None or revenue.change.value is None:
        revenue_note = "两个报告期没有可比较的收入"
    return (
        ResearchRule("R001", "not_evaluated", "没有应收数据，不能评估增速差"),
        ResearchRule("R002", "not_evaluated", "没有现金流和利润，不能评估背离"),
        ResearchRule("R003", "not_evaluated", "没有毛利率，不能评估下降"),
        ResearchRule("R004", "not_evaluated", "没有存货数据，不能评估增速差"),
        ResearchRule("R005", "not_evaluated", "没有流动比率和短期债务，不能评估偿债压力"),
        ResearchRule("R006", "not_evaluated", revenue_note),
    )
