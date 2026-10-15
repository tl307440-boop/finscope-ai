from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.repositories.macro import MacroRepository
from app.schemas.auth import list_provider_credentials
from app.schemas.macro import FredSyncRequest, FredSyncResult
from app.schemas.provider import ProbeRequest
from app.services.auth_types import UserRecord
from app.services.fred_sync import fetch_from_fred, sync_fred_series
from app.services.provider_probe import probe_details, probe_upstream
from app.utils.time import utc_now

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


@router.post("/fred-syncs", summary="同步 FRED 观测修订")
async def sync_fred(
    body: FredSyncRequest,
    request: Request,
    _: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    settings = get_settings()
    repository = MacroRepository(cast(Engine, request.app.state.engine))

    async def fetch(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
        return await _load_vintages(request, settings.fred_api_key, series_code, start, end)

    try:
        written = await sync_fred_series(
            repository,
            api_key=settings.fred_api_key,
            series_code=body.series_code,
            observation_start=body.observation_start,
            observation_end=body.observation_end,
            fetch=fetch,
            fetched_at=utc_now(),
        )
    except ProviderError as error:
        raise _sync_error(error) from error
    payload = FredSyncResult(
        series_code=body.series_code,
        rows_written=written,
        preserved_existing=True,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


async def _load_vintages(
    request: Request,
    api_key: str,
    series_code: str,
    start: date,
    end: date,
) -> tuple[FredVintage, ...]:
    override = getattr(request.app.state, "fred_fetch", None)
    if override is None:
        return await fetch_from_fred(api_key, series_code, start, end)
    fetched: object = await override(series_code, start, end)
    if isinstance(fetched, tuple) and all(isinstance(item, FredVintage) for item in fetched):
        return cast(tuple[FredVintage, ...], fetched)
    raise ProviderError(code="UPSTREAM_PAYLOAD", message="无法解析 FRED 响应", status_code=502)


def _sync_error(error: ProviderError) -> AppError:
    return AppError(
        code=error.code,
        message=error.message,
        status_code=error.status_code or 503,
        details={"rows_written": 0, "preserved_existing": True},
    )
