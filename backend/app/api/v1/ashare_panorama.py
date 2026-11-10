"""A 股全景看板接口：路由只做参数校验与错误转换，统计全部在 `services.ashare_panorama`。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.responses import success_response
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.services import fuyao_board_index, jygs
from app.services.ashare_panorama import ASharePanoramaService, get_panorama_service

router = APIRouter(tags=["A股全景增强"])


def _panorama(request: Request) -> ASharePanoramaService:
    """同一个服务实例；首次请求时接上「界面保存的加密配置 > 环境变量」里的韭研会话与扶摇密钥。"""

    service = get_panorama_service()
    engine = cast(Engine, request.app.state.engine)
    if service.jygs_credentials is None:
        service.jygs_credentials = jygs.credentials_from(engine)
    if service.fuyao_credentials is None:
        service.fuyao_credentials = fuyao_board_index.credentials_from(engine)
    return service


Service = Annotated[ASharePanoramaService, Depends(_panorama)]
StockCode = Annotated[str, Path(pattern=r"^\d{6}(\.(SH|SZ|BJ))?$")]


def _ok(request: Request, payload: BaseModel) -> JSONResponse:
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _upstream(error: ProviderError) -> AppError:
    return AppError(code=error.code, message=error.message, status_code=error.status_code or 503)


@router.get("/ashare/global-quotes", summary="全球核心指数、黄金与原油（含分时走势）")
async def read_global_quotes(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.global_quotes())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/index-detail", summary="全球核心指数 / 商品详情：行情头、分时与近 90 根日K")
async def read_index_detail(
    request: Request,
    service: Service,
    code: Annotated[
        str,
        Query(
            min_length=1,
            max_length=32,
            description="全球行情的 symbol，如 000001.SH、HSI.HK、COMEX黄金",
        ),
    ],
) -> JSONResponse:
    try:
        return _ok(request, await service.index_detail(code))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get(
    "/ashare/sentiment", summary="短线情绪：涨跌分布、涨停炸板、连板晋级、极端波动与接力溢价"
)
async def read_sentiment(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.sentiment())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/volume-margin", summary="成交额（含 11:30 午盘）与两融余额拆解")
async def read_volume_margin(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.volume_margin())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/futures", summary="中金所四大股指期货机构席位多空持仓")
async def read_futures(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.futures())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/leading", summary="领涨板块与高热度人气股")
async def read_leading(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.leading())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/dragon-tiger", summary="龙虎榜：个股与营业部买卖席位明细")
async def read_dragon_tiger(
    request: Request,
    service: Service,
    trade_date: Annotated[date | None, Query(alias="date")] = None,
) -> JSONResponse:
    try:
        return _ok(request, await service.dragon_tiger(trade_date))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/ladder", summary="连板梯队、晋级率与断板股")
async def read_ladder(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.ladder())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/news", summary="热点消息：财联社 / 东财 / 同花顺 / 金十聚合")
async def read_news(request: Request, service: Service) -> JSONResponse:
    try:
        return _ok(request, await service.news())
    except ProviderError as error:
        raise _upstream(error) from error


@router.get(
    "/ashare/jygs-actions", summary="韭研公社异动解析：按板块的涨停 / 异动原因（需 JYGS_SESSION）"
)
async def read_jygs_actions(
    request: Request,
    service: Service,
    trade_date: Annotated[date | None, Query(alias="date")] = None,
) -> JSONResponse:
    try:
        return _ok(request, await service.jygs_actions(trade_date))
    except ProviderError as error:
        raise _upstream(error) from error


@router.get("/ashare/jygs-status", summary="韭研公社登录会话状态（不含会话值）")
async def read_jygs_status(request: Request, service: Service) -> JSONResponse:
    return _ok(request, await service.jygs_status())


@router.get("/ashare/stock-detail/{code}", summary="个股分时、日K、封单、板块与龙虎榜席位")
async def read_stock_detail(request: Request, service: Service, code: StockCode) -> JSONResponse:
    try:
        return _ok(request, await service.stock_detail(code))
    except ProviderError as error:
        raise _upstream(error) from error
