import asyncio
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine

from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.custom_analysis import CustomAnalysisRepository
from app.services.custom_analysis import (
    DEFAULT_PCR_DAYS,
    DEFAULT_VALUATION_DAYS,
    CustomAnalysisService,
    pcr_csv,
    valuation_csv,
)
from app.utils.market_time import shanghai_pit
from app.utils.time import utc_now

router = APIRouter(prefix="/custom-analysis", tags=["自定义数据分析"])

GroupCode = Annotated[str, Path(pattern=r"^[a-z0-9]{3,12}$")]


def _service(request: Request) -> CustomAnalysisService:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise AppError(code="DATABASE_UNAVAILABLE", message="数据库未就绪", status_code=503)
    return CustomAnalysisService(
        CustomAnalysisRepository(cast(Engine, engine)),
        today=lambda: shanghai_pit(utc_now()),
    )


Service = Annotated[CustomAnalysisService, Depends(_service)]


@router.get("/pcr/{group}", summary="四大宽基期权 PCR：成交量 / 持仓量、10 日均线、5 年分位与信号")
async def read_pcr(
    request: Request,
    service: Service,
    group: GroupCode,
    days: Annotated[int, Query(ge=20, le=2500)] = DEFAULT_PCR_DAYS,
) -> JSONResponse:
    view = await asyncio.to_thread(service.pcr, group, days)
    if view is None:
        raise AppError(code="NOT_FOUND", message="不支持的宽基分组", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, view))


@router.get("/valuation", summary="股债收益差、巴菲特指标、全市场中位数 PB")
async def read_valuation(
    request: Request,
    service: Service,
    days: Annotated[int, Query(ge=20, le=2500)] = DEFAULT_VALUATION_DAYS,
) -> JSONResponse:
    view = await asyncio.to_thread(service.valuation, days)
    return JSONResponse(status_code=200, content=success_response(request, view))


@router.get("/sources", summary="数据源清单：最新日期、行数、待审行数与近期质量问题")
async def read_sources(request: Request, service: Service) -> JSONResponse:
    view = await asyncio.to_thread(service.sources)
    return JSONResponse(status_code=200, content=success_response(request, view))


@router.get("/export/pcr/{group}.csv", summary="导出 PCR 监控表（pcr_monitor 结构）")
async def export_pcr(
    service: Service,
    group: GroupCode,
    kind: Annotated[Literal["etf", "index"], Query()] = "etf",
    days: Annotated[int, Query(ge=20, le=2500)] = 2500,
) -> Response:
    view = await asyncio.to_thread(service.pcr, group, days)
    if view is None:
        raise AppError(code="NOT_FOUND", message="不支持的宽基分组", status_code=404)
    body = pcr_csv(view, kind)
    if body is None:
        raise AppError(code="NOT_FOUND", message="该宽基没有这一类期权", status_code=404)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pcr_monitor_{group}_{kind}.csv"'},
    )


@router.get("/export/valuation.csv", summary="导出宏观估值汇总表（macro_valuation_summary 结构）")
async def export_valuation(
    service: Service,
    days: Annotated[int, Query(ge=20, le=2500)] = 2500,
) -> Response:
    view = await asyncio.to_thread(service.valuation, days)
    return Response(
        content=valuation_csv(view),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="macro_valuation_summary.csv"'},
    )
