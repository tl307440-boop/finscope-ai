from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.schemas.auth import list_provider_credentials
from app.schemas.provider import ProbeRequest
from app.services.auth_types import UserRecord
from app.services.provider_probe import probe_details, probe_upstream

router = APIRouter(prefix="/admin", tags=["管理"])


@router.get("/providers", summary="数据源配置状态")
def read_providers(
    request: Request,
    _: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    payload = list_provider_credentials(get_settings())
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.post("/provider-probes", summary="模拟上游故障")
async def probe_provider(
    body: ProbeRequest,
    _: Annotated[UserRecord, Depends(require_admin)],
) -> None:
    error = await probe_upstream(body.scenario)
    raise AppError(
        code=error.code,
        message=error.message,
        status_code=error.status_code or 503,
        details=probe_details(error),
    )
