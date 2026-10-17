import asyncio
from datetime import date
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.repositories.board_market import BoardMarketRepository
from app.repositories.theses import ThesisRepository
from app.services.auth_types import UserRecord
from app.services.board_theses import link_theses
from app.services.industry_market import IndustryMarketService, get_service
from app.services.market_sentiment import MarketSentimentService, get_sentiment_service
from app.utils.market_time import shanghai_pit
from app.utils.time import utc_now

router = APIRouter(tags=["产业研究 · 板块行情"])


def _service(request: Request) -> IndustryMarketService:
    service = get_service()
    engine = getattr(request.app.state, "engine", None)
    if service.store is None and engine is not None:
        service.store = BoardMarketRepository(engine)
    return service


Service = Annotated[IndustryMarketService, Depends(_service)]
BoardCode = Annotated[str, Path(pattern=r"^[Bb][Kk]\d{4}$")]


def _ok(request: Request, payload: BaseModel) -> JSONResponse:
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _upstream(error: ProviderError) -> AppError:
    return AppError(code=error.code, message=error.message, status_code=error.status_code or 503)


@router.get("/industry-market/catalog", summary="东财行业 / 概念板块目录")
async def read_catalog(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.catalog())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/boards", summary="按层级读取板块行情快照；带 as_of 读收盘入库快照")
async def read_boards(
    request: Request,
    service: Service,
    level: Annotated[Literal["1", "2", "3", "concept"], Query()] = "1",
    as_of: Annotated[date | None, Query()] = None,
) -> JSONResponse:
    try:
        return _ok(request, await service.overview(level, as_of))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/snapshot-dates", summary="已入库的收盘快照日期（新到旧）")
async def read_snapshot_dates(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.snapshot_dates())


@router.get("/industry-market/rotation", summary="行业相对轮动图（RRG），基于入库日线")
async def read_rotation(
    request: Request,
    service: Service,
    level: Annotated[Literal["1", "2"], Query()] = "1",
) -> JSONResponse:
    try:
        return _ok(request, await service.rotation(level))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/boards/{code}", summary="板块详情：走势、成分、子行业与估值分位")
async def read_board(code: BoardCode, request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.detail(code))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/boards/{code}/reports", summary="板块近 180 天行业研报")
async def read_board_reports(code: BoardCode, request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.reports(code))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/boards/{code}/theses", summary="板块对应的逻辑追踪行业与我的假设")
async def read_board_theses(
    code: BoardCode,
    request: Request,
    service: Service,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    try:
        name = await service.board_name(code)
    except ProviderError as error:
        raise _upstream(error) from error
    repository = ThesisRepository(cast(Engine, request.app.state.engine))
    linked = await asyncio.to_thread(
        link_theses, repository, user.id, code.upper(), name, shanghai_pit(utc_now())
    )
    return _ok(request, linked)


@router.get("/industry-market/hot", summary="东财人气榜与同花顺热榜")
async def read_hot(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.hot())


Sentiment = Annotated[MarketSentimentService, Depends(get_sentiment_service)]


@router.get("/industry-market/limit-up", summary="涨停 / 炸板 / 跌停股池与连板梯队")
async def read_limit_up(
    request: Request,
    service: Sentiment,
    trade_date: Annotated[date | None, Query(alias="date")] = None,
) -> JSONResponse:
    try:
        return _ok(request, await service.limit_up(trade_date))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/industry-market/org-surveys", summary="近期机构调研统计")
async def read_org_surveys(
    request: Request,
    service: Sentiment,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> JSONResponse:
    try:
        return _ok(request, await service.surveys(days))
    except ProviderError as error:
        raise _upstream(error) from error
