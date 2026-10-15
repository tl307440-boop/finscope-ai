from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_health_service
from app.api.responses import success_response
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["观测"])


@router.get("/live", summary="进程存活")
def read_live(
    request: Request,
    service: Annotated[HealthService, Depends(get_health_service)],
) -> JSONResponse:
    return JSONResponse(status_code=200, content=success_response(request, service.live()))


@router.get("/ready", summary="依赖就绪")
def read_ready(
    request: Request,
    service: Annotated[HealthService, Depends(get_health_service)],
) -> JSONResponse:
    return JSONResponse(status_code=200, content=success_response(request, service.ready()))
