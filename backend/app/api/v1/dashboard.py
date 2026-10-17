import asyncio
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.repositories.watchlists import WatchlistRepository
from app.services.auth_types import UserRecord
from app.services.cockpit import (
    INDEX_NAMES,
    DashboardService,
    attach_repositories,
    get_dashboard_service,
)

router = APIRouter(prefix="/dashboard", tags=["总览驾驶舱"])


def _service(request: Request) -> DashboardService:
    service = get_dashboard_service()
    engine = getattr(request.app.state, "engine", None)
    if engine is not None:
        attach_repositories(service, engine)
    return service


Service = Annotated[DashboardService, Depends(_service)]


def _ok(request: Request, payload: BaseModel) -> JSONResponse:
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _upstream(error: ProviderError) -> AppError:
    return AppError(code=error.code, message=error.message, status_code=error.status_code or 503)


@router.get("/market", summary="A 股市场温度、主要指数与两市全景")
async def read_market(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.market())


@router.get("/index-bars", summary="指数分时 / 日K / 周K / 月K（含 MA5/10/20）")
async def read_index_bars(
    request: Request,
    service: Service,
    symbol: Annotated[str, Query(pattern=r"^(sh|sz|bj)\d{6}$")] = "sh000001",
    period: Annotated[Literal["min", "day", "week", "month"], Query()] = "day",
) -> JSONResponse:
    if symbol not in INDEX_NAMES:
        raise AppError(code="VALIDATION_ERROR", message="不支持的指数代码", status_code=422)
    try:
        return _ok(request, await service.index_bars(symbol, period))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/macro", summary="全球流动性、经济热度、宏观趋势与外盘资产")
async def read_macro(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.macro())


@router.get("/boards", summary="热点板块（热度指数）与板块涨幅榜")
async def read_boards(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.boards())


@router.get("/stocks", summary="人气热股与涨幅居前个股")
async def read_stocks(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.stocks())


@router.get("/announcements", summary="全市场重要公告与热门公告（巨潮资讯）")
async def read_announcements(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.announcements())


@router.get("/watch", summary="我的重点关注：自选分组的强弱分布、走势与信号")
async def read_watch(
    request: Request,
    service: Service,
    user: Annotated[UserRecord, Depends(get_current_user)],
    group_id: Annotated[UUID | None, Query()] = None,
) -> JSONResponse:
    repository = WatchlistRepository(cast(Engine, request.app.state.engine))
    groups = await asyncio.to_thread(repository.list_groups, user.id)
    if group_id is not None:
        group = next((item for item in groups if item.id == group_id), None)
        if group is None:
            raise AppError(code="NOT_FOUND", message="找不到这个分组", status_code=404)
    else:
        group = next((item for item in groups if item.items), groups[0] if groups else None)
    return _ok(request, await service.watch(group, groups, repository.recent_closes))
