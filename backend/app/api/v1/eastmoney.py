from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.services.auth_types import UserRecord
from app.services.eastmoney_binding import (
    EastmoneyBindingService,
    SyncSummary,
    build_service,
)

router = APIRouter(prefix="/eastmoney", tags=["东方财富绑定"])


class BindingData(BaseModel):
    bound: bool
    # active 正常；expired 登录已失效，需要重新扫码。
    status: str | None = None
    alias: str | None = None
    bound_at: datetime | None = None
    last_sync_at: datetime | None = None
    message: str | None = None
    groups: int | None = None
    items: int | None = None


class SyncData(BaseModel):
    groups: int
    items: int
    added: int
    removed: int
    message: str
    synced_at: datetime | None = None
    skipped_groups: list[str] = []
    cached: bool = False


class QrData(BaseModel):
    login_id: str
    qr_png_base64: str
    expires_in: int


class QrStateData(BaseModel):
    # waiting 等待扫码；expired 二维码失效；success 已登录并完成首次同步。
    state: str
    alias: str | None = None
    sync: SyncData | None = None
    sync_error: str | None = None


class UnbindData(BaseModel):
    removed: bool


def get_service(request: Request) -> EastmoneyBindingService:
    state = request.app.state
    service = getattr(state, "eastmoney_service", None)
    if service is None:
        service = build_service(cast(Engine, state.engine), get_settings())
        state.eastmoney_service = service
    return cast(EastmoneyBindingService, service)


def _sync(summary: SyncSummary) -> SyncData:
    return SyncData(
        groups=summary.groups,
        items=summary.items,
        added=summary.added,
        removed=summary.removed,
        message=summary.message,
        synced_at=summary.synced_at,
        skipped_groups=list(summary.skipped_groups),
        cached=summary.cached,
    )


@router.get("", summary="东方财富绑定状态")
def binding_status(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[EastmoneyBindingService, Depends(get_service)],
) -> JSONResponse:
    binding = service.status(user.id)
    data = (
        BindingData(bound=False)
        if binding is None
        else BindingData(
            bound=True,
            status=binding.status,
            alias=binding.em_alias,
            bound_at=binding.bound_at,
            last_sync_at=binding.last_sync_at,
            message=binding.last_sync_message,
            groups=binding.last_sync_groups,
            items=binding.last_sync_items,
        )
    )
    return JSONResponse(status_code=200, content=success_response(request, data))


@router.post("/qr", summary="生成东方财富扫码登录二维码")
async def create_qr(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[EastmoneyBindingService, Depends(get_service)],
) -> JSONResponse:
    started = await service.start_qr(user.id)
    data = QrData(
        login_id=started.login_id,
        qr_png_base64=started.png_base64,
        expires_in=started.expires_in,
    )
    return JSONResponse(status_code=200, content=success_response(request, data))


@router.get("/qr/{login_id}", summary="查询扫码状态；成功后立即同步一次自选")
async def poll_qr(
    login_id: str,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[EastmoneyBindingService, Depends(get_service)],
) -> JSONResponse:
    state = await service.poll_qr(user.id, login_id)
    data = QrStateData(
        state=state.state,
        alias=state.alias or None,
        sync=None if state.sync is None else _sync(state.sync),
        sync_error=state.sync_error or None,
    )
    return JSONResponse(status_code=200, content=success_response(request, data))


@router.post("/sync", summary="立即把东方财富自选同步进来（单向：东财 → 本系统）")
async def sync_now(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[EastmoneyBindingService, Depends(get_service)],
) -> JSONResponse:
    summary = await service.sync(user.id)
    return JSONResponse(status_code=200, content=success_response(request, _sync(summary)))


@router.delete("", summary="解除东方财富绑定（立即删除登录凭证，已同步的自选保留）")
async def unbind(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[EastmoneyBindingService, Depends(get_service)],
) -> JSONResponse:
    removed = await service.unbind(user.id)
    if not removed:
        raise AppError(code="NOT_FOUND", message="还没有绑定东方财富", status_code=404)
    return JSONResponse(
        status_code=200, content=success_response(request, UnbindData(removed=True))
    )
