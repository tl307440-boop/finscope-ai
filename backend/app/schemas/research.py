from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.repositories.filings import FactRecord
from app.repositories.macro import decimal_text
from app.services.company_research import ResearchComparison, SourcedAmount, compare_periods

RESEARCH_NOTICE = "本地样例，不是本次从 SEC 拉取。没有数据时不会编造财务数字。"
EDGAR_NOTICE = "数据来自 SEC EDGAR companyfacts，按申报日做时点过滤。没有数据时不会编造财务数字。"


def research_notice(facts: list[FactRecord]) -> str:
    if facts and all(fact.filing_status == "edgar" for fact in facts):
        return EDGAR_NOTICE
    return RESEARCH_NOTICE


class SourceRef(BaseModel):
    value: str
    unit: str
    form: str
    filed: date
    accession_no: str
    source_url: str


class MetricComparison(BaseModel):
    concept: str
    unit: str
    earlier: SourceRef | None = None
    later: SourceRef | None = None
    change: str | None = None
    change_reason: str | None = None


class RuleLine(BaseModel):
    rule_id: str
    status: str
    rationale: str


class ResearchData(BaseModel):
    instrument_id: UUID
    name_cn: str
    notice: str
    available_periods: list[date]
    earlier_period: date | None = None
    later_period: date | None = None
    metrics: list[MetricComparison]
    highlights: list[RuleLine]
    risks: list[RuleLine]
    unevaluated: list[RuleLine]


def build_research(
    *,
    instrument_id: UUID,
    name_cn: str,
    facts: list[FactRecord],
    period_a: date | None,
    period_b: date | None,
) -> ResearchData:
    periods = sorted({fact.period_end for fact in facts})
    comparison = None
    if period_a is not None and period_b is not None:
        comparison = compare_periods(_sourced(facts), period_a, period_b)
    return ResearchData(
        instrument_id=instrument_id,
        name_cn=name_cn,
        notice=research_notice(facts),
        available_periods=periods,
        earlier_period=None if comparison is None else comparison.earlier_period,
        later_period=None if comparison is None else comparison.later_period,
        metrics=[] if comparison is None else [_metric(item) for item in comparison.metrics],
        highlights=_rules(comparison, "highlights"),
        risks=_rules(comparison, "risks"),
        unevaluated=_rules(comparison, "unevaluated"),
    )


def _sourced(facts: list[FactRecord]) -> list[SourcedAmount]:
    return [
        SourcedAmount(
            concept=fact.concept,
            unit=fact.unit,
            value=fact.value,
            period_end=fact.period_end,
            filed=fact.filed,
            form=fact.form,
            accession_no=fact.accession_no,
            source_url=fact.source_url,
            period_start=fact.period_start,
        )
        for fact in facts
    ]


def _metric(item: object) -> MetricComparison:
    from app.services.company_research import ComparedMetric

    if not isinstance(item, ComparedMetric):
        raise TypeError("对比结果类型不正确")
    return MetricComparison(
        concept=item.concept,
        unit=item.unit,
        earlier=_ref(item.earlier),
        later=_ref(item.later),
        change=None if item.change.value is None else decimal_text(item.change.value),
        change_reason=item.change.reason,
    )


def _ref(amount: SourcedAmount | None) -> SourceRef | None:
    if amount is None:
        return None
    return SourceRef(
        value=decimal_text(amount.value),
        unit=amount.unit,
        form=amount.form,
        filed=amount.filed,
        accession_no=amount.accession_no,
        source_url=amount.source_url,
    )


def _rules(comparison: ResearchComparison | None, name: str) -> list[RuleLine]:
    if comparison is None:
        return []
    rows = getattr(comparison, name)
    return [
        RuleLine(rule_id=row.rule_id, status=row.status, rationale=row.rationale) for row in rows
    ]
