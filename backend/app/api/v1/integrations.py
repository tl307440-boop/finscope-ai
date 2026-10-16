from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.services.auth_types import UserRecord
from app.services.integration_probe import probe
from app.services.runtime_settings import (
    IntegrationItem,
    IntegrationService,
    effective_settings,
    managed_key,
)
from app.utils.time import utc_now

router = APIRouter(prefix="/admin/integrations", tags=["密钥与数据源配置"])


class IntegrationPublic(BaseModel):
    key: str
    label: str
    group: str
    secret: bool
    kind: str
    help: str
    help_url: str
    placeholder: str
    configured: bool
    source: str
    preview: str
    updated_at: datetime | None
    updated_by: str | None
    problem: str


class IntegrationList(BaseModel):
    items: list[IntegrationPublic]
    notice: str


class IntegrationValue(BaseModel):
    value: str = Field(min_length=1, max_length=512)


class IntegrationTest(BaseModel):
    """value 为空时测试当前生效的值；不为空时测试这个还没保存的候选值。"""

    value: str | None = Field(default=None, max_length=512)


class ProbeOutcome(BaseModel):
    ok: bool
    message: str


NOTICE = (
    "密钥加密后保存在数据库里，页面和接口都只显示末四位，不会返回明文。"
    "这里填的值优先于服务器环境变量，几秒内对所有后台进程生效，不用重启。"
)


def _service(request: Request) -> IntegrationService:
    return IntegrationService(cast(Engine, request.app.state.engine), get_settings())


def _public(item: IntegrationItem) -> IntegrationPublic:
    return IntegrationPublic(**item.__dict__)


@router.get("", summary="密钥与数据源配置（仅管理员，只返回掩码）")
def list_integrations(
    request: Request, admin: Annotated[UserRecord, Depends(require_admin)]
) -> JSONResponse:
    del admin
    payload = IntegrationList(
        items=[_public(item) for item in _service(request).list()], notice=NOTICE
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.put("/{key}", summary="保存一项配置（仅管理员）")
def save_integration(
    key: str,
    body: IntegrationValue,
    request: Request,
    admin: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    service = _service(request)
    service.save(key, body.value, user_id=admin.id, now=utc_now())
    item = next(entry for entry in service.list() if entry.key == key)
    return JSONResponse(status_code=200, content=success_response(request, _public(item)))


@router.delete("/{key}", summary="清除界面里的值，退回环境变量（仅管理员）")
def clear_integration(
    key: str,
    request: Request,
    admin: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    del admin
    service = _service(request)
    service.clear(key)
    item = next(entry for entry in service.list() if entry.key == key)
    return JSONResponse(status_code=200, content=success_response(request, _public(item)))


@router.post("/{key}/test", summary="测试连接（仅管理员，不回显密钥）")
def test_integration(
    key: str,
    body: IntegrationTest,
    request: Request,
    admin: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    del admin
    managed_key(key)
    service = _service(request)
    value = service.candidate_or_effective(key, body.value)
    if not value:
        outcome = ProbeOutcome(ok=False, message="还没有填写，也没有环境变量里的值可测")
    else:
        engine = cast(Engine, request.app.state.engine)
        effective = effective_settings(engine, use_cache=False)
        result = probe(
            key,
            value,
            sec_user_agent=effective.sec_user_agent,
            llm_base_url=effective.llm_api_base_url,
        )
        outcome = ProbeOutcome(ok=result.ok, message=result.message)
    return JSONResponse(status_code=200, content=success_response(request, outcome))
