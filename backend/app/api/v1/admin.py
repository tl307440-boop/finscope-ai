from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.schemas.auth import list_provider_credentials
from app.services.auth_types import UserRecord

router = APIRouter(prefix="/admin", tags=["管理"])


@router.get("/providers", summary="数据源配置状态")
def read_providers(
    request: Request,
    _: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    payload = list_provider_credentials(get_settings())
    return JSONResponse(status_code=200, content=success_response(request, payload))
