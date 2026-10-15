from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from app.repositories.filings import FactRecord
from app.repositories.macro import decimal_text
from app.schemas.research import RESEARCH_NOTICE
from app.services.company_research import ComparedMetric, SourcedAmount, compare_periods
from app.services.document_index import SearchHit
from app.services.llm_gateway import EvidenceItem, LanguageModel, run_analysis

CALC_VERSION = "filing-report-v1"
RULES_MESSAGE = "未配置模型，本记录只整理已入库数字。"
NO_DOCUMENT = "没有可引用的上传文档。"


@dataclass(frozen=True)
class ReportDraft:
    status: str
    title: str
    message: str
    body: str
    model_name: str
    fact_ids: tuple[UUID, ...]
    chunk_ids: tuple[UUID, ...]


def compose_filing_report(
    *,
    user_id: UUID,
    symbol: str,
    name_cn: str,
    as_of: date,
    facts: list[FactRecord],
    period_a: date,
    period_b: date,
    chunks: list[SearchHit],
    model: LanguageModel | None,
    budget_remaining: int,
) -> ReportDraft:
    comparison = compare_periods(
        [_sourced(fact) for fact in facts],
        period_a,
        period_b,
    )
    used = _used_facts(facts, comparison.metrics)
    lines = [_metric_line(item, used) for item in comparison.metrics]
    document_line = NO_DOCUMENT if not chunks else _chunk_lines(chunks)
    message = RULES_MESSAGE
    model_name = ""
    extra: list[str] = []
    status = "succeeded"
    if model is not None:
        outcome = run_analysis(
            model,
            user_id=user_id,
            symbol=symbol,
            as_of=datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC),
            evidence=_evidence(used, chunks),
            budget_remaining=budget_remaining,
            timeout_seconds=5,
        )
        model_name = "mock"
        message = outcome.message
        status = "succeeded" if outcome.status == "accepted" else "failed"
        if outcome.analysis is not None:
            extra = [
                finding.claim for finding in [*outcome.analysis.highlights, *outcome.analysis.risks]
            ]
    body = "\n".join(
        [
            f"{name_cn} 研究记录",
            RESEARCH_NOTICE,
            message,
            (
                f"报告期 {comparison.earlier_period.isoformat()} "
                f"与 {comparison.later_period.isoformat()}"
            ),
            *lines,
            document_line,
            *extra,
        ]
    )
    return ReportDraft(
        status=status,
        title=f"{name_cn} 研究记录",
        message=message,
        body=body,
        model_name=model_name,
        fact_ids=tuple(fact.id for fact in used),
        chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
    )


def _sourced(fact: FactRecord) -> SourcedAmount:
    return SourcedAmount(
        concept=fact.concept,
        unit=fact.unit,
        value=fact.value,
        period_end=fact.period_end,
        filed=fact.filed,
        form=fact.form,
        accession_no=fact.accession_no,
        source_url=fact.source_url,
    )


def _used_facts(facts: list[FactRecord], metrics: tuple[ComparedMetric, ...]) -> list[FactRecord]:
    wanted = {
        (side.concept, side.unit, side.period_end, side.accession_no)
        for metric in metrics
        for side in (metric.earlier, metric.later)
        if side is not None
    }
    return [
        fact
        for fact in facts
        if (fact.concept, fact.unit, fact.period_end, fact.accession_no) in wanted
    ]


def _metric_line(metric: ComparedMetric, facts: list[FactRecord]) -> str:
    earlier = _side("早期", metric.earlier, facts)
    later = _side("后期", metric.later, facts)
    change = "无法计算" if metric.change.value is None else decimal_text(metric.change.value)
    return f"{metric.concept} {metric.unit} {earlier} {later} 变化 {change}"


def _side(label: str, amount: SourcedAmount | None, facts: list[FactRecord]) -> str:
    if amount is None:
        return f"{label} 暂无"
    fact_id = next(
        (
            fact.id
            for fact in facts
            if fact.accession_no == amount.accession_no
            and fact.period_end == amount.period_end
            and fact.concept == amount.concept
        ),
        None,
    )
    marker = "" if fact_id is None else f" 事实 {fact_id}"
    return (
        f"{label} {decimal_text(amount.value)} 申报号 {amount.accession_no}"
        f" 来源 {amount.source_url}{marker}"
    )


def _chunk_lines(chunks: list[SearchHit]) -> str:
    rows = [
        f"文档 {chunk.document_id} 第 {chunk.page_start}-{chunk.page_end} 页 {chunk.snippet}"
        for chunk in chunks
    ]
    return "\n".join(rows)


def _evidence(facts: list[FactRecord], chunks: list[SearchHit]) -> list[EvidenceItem]:
    items = [
        EvidenceItem(
            chunk_id=None,
            metric_id=fact.id,
            text=(
                f"{fact.concept} {decimal_text(fact.value)} {fact.unit} 申报号 {fact.accession_no}"
            ),
        )
        for fact in facts
    ]
    items.extend(
        EvidenceItem(chunk_id=chunk.chunk_id, metric_id=None, text=chunk.snippet)
        for chunk in chunks
    )
    return items
