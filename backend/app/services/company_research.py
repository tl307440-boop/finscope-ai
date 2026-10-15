from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.financial_metrics import MetricResult, year_over_year


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
    left = _one(facts, concept, unit, earlier)
    right = _one(facts, concept, unit, later)
    change = _change(left, right, unit, concept)
    return ComparedMetric(concept=concept, unit=unit, earlier=left, later=right, change=change)


def _one(
    facts: tuple[SourcedAmount, ...] | list[SourcedAmount],
    concept: str,
    unit: str,
    period_end: date,
) -> SourcedAmount | None:
    matches = [
        item
        for item in facts
        if item.concept == concept and item.unit == unit and item.period_end == period_end
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: item.filed)


def _change(
    earlier: SourcedAmount | None,
    later: SourcedAmount | None,
    unit: str,
    concept: str,
) -> MetricResult:
    if earlier is None or later is None:
        return MetricResult(value=None, reason="insufficient_data", display=None)
    if unit != "USD" or not concept.endswith("Revenues"):
        return MetricResult(value=None, reason="not_a_flow", display=None)
    return year_over_year(later.value, earlier.value)


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
