"""韭研公社扫码登录接口（仅管理员）：取二维码、查扫码进度；成功后会话加密存库。"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.services.ashare_panorama import get_panorama_service
from app.services.auth_types import UserRecord
from app.services.jygs_login import LOGIN_TIMEOUT_SECONDS, Saver, get_login_manager
from app.services.runtime_settings import IntegrationService
from app.utils.time import utc_now

router = APIRouter(prefix="/admin/jygs-login", tags=["韭研公社扫码登录"])

Admin = Annotated[UserRecord, Depends(require_admin)]


class LoginStarted(BaseModel):
    loginId: str
    qrImage: str  # data URL，前端直接放进 <img>
    expiresInSeconds: int


class LoginProgress(BaseModel):
    status: str
    message: str
    expiresAt: str | None = None


def _saver(engine: Engine, user_id: uuid.UUID) -> Saver:
    def save(session: str, expires: datetime | None) -> None:
        integrations = IntegrationService(engine, get_settings())
        now = utc_now()
        integrations.save("JYGS_SESSION", session, user_id=user_id, now=now)
        if expires is not None:
            integrations.save("JYGS_SESSION_EXPIRES", expires.isoformat(), user_id=user_id, now=now)
        else:
            integrations.clear("JYGS_SESSION_EXPIRES")
        get_panorama_service().jygs_reset()

    return save


@router.post("", summary="获取韭研公社登录二维码（仅管理员）")
async def start_login(request: Request, admin: Admin) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    try:
        attempt = await get_login_manager().start(_saver(engine, admin.id))
    except ProviderError as error:
        raise AppError(
            code=error.code, message=error.message, status_code=error.status_code or 502
        ) from error
    image = base64.b64encode(attempt.qr_image).decode()
    payload = LoginStarted(
        loginId=attempt.login_id,
        qrImage=f"data:{attempt.qr_mime};base64,{image}",
        expiresInSeconds=int(LOGIN_TIMEOUT_SECONDS),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/{login_id}", summary="查询扫码进度（仅管理员）")
async def login_progress(
    request: Request, admin: Admin, login_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
) -> JSONResponse:
    del admin
    attempt = get_login_manager().status(login_id)
    if attempt is None:
        raise AppError(
            code="NOT_FOUND", message="这次登录已失效，请重新获取二维码", status_code=404
        )
    payload = LoginProgress(
        status=attempt.status,
        message=attempt.message,
        expiresAt=attempt.expires_at.isoformat() if attempt.expires_at else None,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))
