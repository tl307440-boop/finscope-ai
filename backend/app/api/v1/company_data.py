from datetime import date, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.schemas.freshness import FreshnessReport
from app.services.auth_types import UserRecord
from app.services.company_data import CompanyDataService, DatasetStatus
from app.services.system_freshness import build_report
from app.utils.time import utc_now

router = APIRouter(tags=["数据就绪度"])


class DatasetPublic(BaseModel):
    dataset: str
    label: str
    source: str
    state: str
    sync: str
    latest_data_date: date | None
    last_synced_at: datetime | None
    message: str
    can_request: bool


class CompanyDataData(BaseModel):
    instrument_id: UUID
    generated_at: datetime
    syncing: bool
    datasets: list[DatasetPublic]


def _instrument(request: Request, instrument_id: UUID) -> InstrumentRecord:
    engine = cast(Engine, request.app.state.engine)
    found = InstrumentRepository(engine).get(instrument_id)
    if found is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    return found


def _payload(
    instrument: InstrumentRecord, now: datetime, rows: list[DatasetStatus]
) -> CompanyDataData:
    return CompanyDataData(
        instrument_id=instrument.id,
        generated_at=now,
        syncing=any(row.sync in {"queued", "running"} for row in rows),
        datasets=[DatasetPublic(**row.__dict__) for row in rows],
    )


@router.get(
    "/companies/{instrument_id}/data-status",
    summary="这家公司的财报与日线是否就绪、是否新鲜、是否正在后台抓取",
)
def read_company_data_status(instrument_id: UUID, request: Request) -> JSONResponse:
    instrument = _instrument(request, instrument_id)
    engine = cast(Engine, request.app.state.engine)
    now = utc_now()
    rows = CompanyDataService(engine, get_settings()).status(instrument, now)
    return JSONResponse(
        status_code=200, content=success_response(request, _payload(instrument, now, rows))
    )


@router.post(
    "/companies/{instrument_id}/data-requests",
    summary="首次打开或数据过期时，登记后台抓取（已在队列里则不重复登记）",
)
def request_company_data(
    instrument_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    instrument = _instrument(request, instrument_id)
    engine = cast(Engine, request.app.state.engine)
    now = utc_now()
    rows = CompanyDataService(engine, get_settings()).ensure(instrument, now, user.id)
    return JSONResponse(
        status_code=200, content=success_response(request, _payload(instrument, now, rows))
    )


@router.get("/system/freshness", summary="全站数据新鲜度与后台进程状态")
def read_system_freshness(request: Request) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    report: FreshnessReport = build_report(engine, get_settings(), utc_now())
    return JSONResponse(status_code=200, content=success_response(request, report))
