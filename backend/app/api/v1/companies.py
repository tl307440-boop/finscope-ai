from datetime import date
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.responses import success_response
from app.catalog.reference_data import reference_instruments
from app.core.exceptions import AppError
from app.providers.sec_edgar import load_fixture, parse_companyfacts, payload_sha256
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.filings import FactRecord, FilingRepository
from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.schemas.research import build_research, research_notice
from app.services.company_financials import build_overview
from app.utils.identifiers import normalize_cik

router = APIRouter(tags=["公司研究"])


class FinancialsData(BaseModel):
    instrument_id: UUID
    as_of: date
    data_status: Literal["edgar", "eastmoney", "sample", "empty"]
    notice: str
    reference_end: str | None
    compare_end: str | None
    delta_label: str
    tiles: list[dict[str, Any]]
    trend: dict[str, list[dict[str, Any]]]
    table: dict[str, dict[str, Any]]
    cash_vs_profit: list[dict[str, Any]]
    rule_observations: dict[str, str]


@router.get(
    "/companies/{instrument_id}/financials",
    summary="公司财务总览：指标卡、趋势与对比表（同比、比率均在服务端计算）",
)
def read_company_financials(
    instrument_id: UUID,
    request: Request,
    as_of: Annotated[date, Query()],
    reference: Annotated[date | None, Query()] = None,
    compare: Annotated[date | None, Query()] = None,
) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    instrument = InstrumentRepository(engine).get(instrument_id)
    if instrument is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    facts = facts_for_instrument(engine, instrument, as_of)
    live = [fact for fact in facts if fact.filing_status in LIVE_STATUSES]
    if live:
        facts = live
    status: Literal["edgar", "eastmoney", "sample", "empty"] = "empty"
    if facts:
        status = "sample"
    if live:
        status = "eastmoney" if instrument.market == "CN" else "edgar"
    overview = build_overview(facts, reference, compare)
    payload = FinancialsData(
        instrument_id=instrument.id,
        as_of=as_of,
        data_status=status,
        notice=research_notice(facts) if facts else "观察日之前还没有已申报的财务数据",
        reference_end=cast(str | None, overview["reference_end"]),
        compare_end=cast(str | None, overview["compare_end"]),
        delta_label=cast(str, overview["delta_label"]),
        tiles=cast(list[dict[str, Any]], overview["tiles"]),
        trend=cast(dict[str, list[dict[str, Any]]], overview["trend"]),
        table=cast(dict[str, dict[str, Any]], overview["table"]),
        cash_vs_profit=cast(list[dict[str, Any]], overview["cash_vs_profit"]),
        rule_observations=cast(dict[str, str], overview["rule_observations"]),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/companies/{instrument_id}/research", summary="对比两个报告期")
def read_company_research(
    instrument_id: UUID,
    request: Request,
    as_of: Annotated[date, Query()],
    period_a: Annotated[date | None, Query()] = None,
    period_b: Annotated[date | None, Query()] = None,
) -> JSONResponse:
    if period_a is not None and period_b is not None and period_a == period_b:
        raise AppError(code="VALIDATION_ERROR", message="请选择两个不同的报告期", status_code=422)
    engine = cast(Engine, request.app.state.engine)
    instrument = InstrumentRepository(engine).get(instrument_id)
    if instrument is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    facts = facts_for_instrument(engine, instrument, as_of)
    payload = build_research(
        instrument_id=instrument.id,
        name_cn=instrument.name_cn,
        facts=facts,
        period_a=period_a,
        period_b=period_b,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


LIVE_STATUSES = frozenset({"edgar", "eastmoney"})


def facts_for_instrument(
    engine: Engine, instrument: InstrumentRecord, as_of: date
) -> list[FactRecord]:
    """按市场取财务事实：A 股读东方财富入库的报表，美股读 SEC（没有真实数据时补样例）。"""

    if instrument.market == "CN":
        return CnFinancialRepository(engine).list_facts(instrument.id, as_of)
    if instrument.cik:
        return visible_sample_facts(engine, normalize_cik(instrument.cik), as_of)
    return []


def visible_sample_facts(engine: Engine, cik: str, as_of: date) -> list[FactRecord]:
    """已从 EDGAR 拉过真实 companyfacts 的公司直接读库；否则补上随包样例。"""
    repository = FilingRepository(engine)
    if repository.has_live_filings(cik):
        return repository.list_facts(cik, as_of)
    InstrumentRepository(engine).upsert_reference(reference_instruments())
    payload = load_fixture()
    parsed = [fact for fact in parse_companyfacts(payload) if fact.cik == cik]
    if parsed:
        repository.upsert_facts(parsed, payload_sha256(payload))
    return repository.list_facts(cik, as_of)
