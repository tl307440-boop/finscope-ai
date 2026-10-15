from datetime import UTC, date, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.api.v1.companies import visible_sample_facts
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.repositories.document_chunks import ChunkRepository
from app.repositories.instruments import InstrumentRepository
from app.repositories.reports import ReportRepository, SavedReport
from app.schemas.research import RESEARCH_NOTICE
from app.services.auth_types import UserRecord
from app.services.document_index import search_owned_documents
from app.services.filing_report import compose_filing_report
from app.services.llm_gateway import LanguageModel, model_availability
from app.utils.identifiers import normalize_cik

router = APIRouter(prefix="/analysis", tags=["分析"])


class ModelStatusData(BaseModel):
    available: bool
    message: str


class FilingAnalysisRequest(BaseModel):
    instrument_id: UUID
    as_of: date
    period_a: date
    period_b: date


class ReportSummary(BaseModel):
    report_id: UUID
    run_id: UUID
    title: str
    status: str
    as_of: date


class ReportListData(BaseModel):
    reports: list[ReportSummary]


class AnalysisRunData(BaseModel):
    run_id: UUID
    report_id: UUID
    status: str
    title: str
    message: str
    body: str
    notice: str


def _run_data(saved: SavedReport) -> AnalysisRunData:
    return AnalysisRunData(
        run_id=saved.run_id,
        report_id=saved.report_id,
        status=saved.status,
        title=saved.title,
        message=saved.message,
        body=saved.body,
        notice=RESEARCH_NOTICE,
    )


@router.get("/model-status", summary="模型是否已配置")
def model_status(request: Request) -> JSONResponse:
    settings = get_settings()
    available, message = model_availability(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base_url,
        model=settings.llm_model,
    )
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            ModelStatusData(available=available, message=message),
        ),
    )


@router.post("/filing", summary="整理一份已入库财报研究记录")
def create_filing_analysis(
    body: FilingAnalysisRequest,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    if body.period_a == body.period_b:
        raise AppError(code="VALIDATION_ERROR", message="请选择两个不同的报告期", status_code=422)
    engine = cast(Engine, request.app.state.engine)
    instrument = InstrumentRepository(engine).get(body.instrument_id)
    if instrument is None or not instrument.cik:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    facts = visible_sample_facts(engine, normalize_cik(instrument.cik), body.as_of)
    chunks = search_owned_documents(
        ChunkRepository(engine),
        owner_id=user.id,
        query=instrument.name_cn,
        as_of=datetime(body.as_of.year, body.as_of.month, body.as_of.day, 23, 59, tzinfo=UTC),
    )
    draft = compose_filing_report(
        user_id=user.id,
        symbol=instrument.canonical_code,
        name_cn=instrument.name_cn,
        as_of=body.as_of,
        facts=facts,
        period_a=body.period_a,
        period_b=body.period_b,
        chunks=chunks,
        model=_model(request),
        budget_remaining=get_settings().ai_daily_token_budget,
    )
    saved = ReportRepository(engine).save(
        user_id=user.id,
        instrument_id=instrument.id,
        symbol=instrument.canonical_code,
        as_of=body.as_of,
        period_a=body.period_a,
        period_b=body.period_b,
        draft=draft,
    )
    return JSONResponse(status_code=200, content=success_response(request, _run_data(saved)))


@router.get("/reports", summary="我的研究记录")
def list_reports(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    q: Annotated[str, Query(max_length=80)] = "",
) -> JSONResponse:
    rows = ReportRepository(cast(Engine, request.app.state.engine)).list_owned(user.id, q)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            ReportListData(
                reports=[
                    ReportSummary(
                        report_id=row.report_id,
                        run_id=row.run_id,
                        title=row.title,
                        status=row.status,
                        as_of=row.as_of,
                    )
                    for row in rows
                ]
            ),
        ),
    )


@router.delete("/reports/{report_id}", summary="删除研究记录")
def hide_report(
    report_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    hidden = ReportRepository(cast(Engine, request.app.state.engine)).hide(user.id, report_id)
    if hidden is None:
        raise AppError(code="NOT_FOUND", message="找不到这份研究记录", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(request, ReportListData(reports=[])),
    )


@router.get("/runs/{run_id}", summary="查看研究记录")
def read_analysis_run(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    saved = ReportRepository(cast(Engine, request.app.state.engine)).get_owned(user.id, run_id)
    if saved is None:
        raise AppError(code="NOT_FOUND", message="找不到这份研究记录", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _run_data(saved)))


def _model(request: Request) -> LanguageModel | None:
    if not hasattr(request.app.state, "language_model"):
        return None
    candidate = request.app.state.language_model
    if isinstance(candidate, LanguageModel):
        return candidate
    return None
