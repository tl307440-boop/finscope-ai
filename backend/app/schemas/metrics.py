from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.repositories.macro import decimal_text
from app.services.financial_metrics import (
    MetricResult,
    ReportedAmount,
    StatementKind,
    pe_ttm,
    single_quarter,
)

SAMPLE_NOTICE = "计算样例，不是公司真实财报。"


class QuarterRequest(BaseModel):
    statement: Literal["income", "cashflow", "balance"]
    q1: str | None = None
    q2: str | None = None
    q3: str | None = None
    fy: str | None = None
    market_cap: str | None = None
    profit: str | None = None
    unit: str = Field(default="CNY", min_length=1, max_length=16)
    version: str = Field(default="sample", min_length=1, max_length=32)


class QuarterValue(BaseModel):
    quarter: str
    value: str | None = None
    reason: str | None = None
    display: str | None = None


class EvaluationData(BaseModel):
    notice: str
    quarters: list[QuarterValue]
    pe_value: str | None = None
    pe_display: str | None = None
    pe_reason: str | None = None


def evaluate_request(body: QuarterRequest) -> EvaluationData:
    kind: StatementKind = body.statement
    quarters = single_quarter(
        kind,
        q1=_amount(body.q1, body.unit, body.version),
        q2=_amount(body.q2, body.unit, body.version),
        q3=_amount(body.q3, body.unit, body.version),
        fy=_amount(body.fy, body.unit, body.version),
    )
    pe = _pe(body.market_cap, body.profit)
    return EvaluationData(
        notice=SAMPLE_NOTICE,
        quarters=[_quarter(name, quarters[name]) for name in ("Q1", "Q2", "Q3", "Q4")],
        pe_value=None if pe is None or pe.value is None else decimal_text(pe.value),
        pe_display=None if pe is None else pe.display,
        pe_reason=None if pe is None else pe.reason,
    )


def _amount(raw: str | None, unit: str, version: str) -> ReportedAmount | None:
    if raw is None or not raw.strip():
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise ValueError("金额无法解析") from exc
    return ReportedAmount(value=value, unit=unit, version=version)


def _quarter(name: str, result: MetricResult) -> QuarterValue:
    return QuarterValue(
        quarter=name,
        value=None if result.value is None else decimal_text(result.value),
        reason=result.reason,
        display=result.display,
    )


def _pe(market_cap: str | None, profit: str | None) -> MetricResult | None:
    if not market_cap or not profit:
        return None
    try:
        return pe_ttm(Decimal(market_cap.strip()), Decimal(profit.strip()))
    except InvalidOperation as exc:
        raise ValueError("金额无法解析") from exc
