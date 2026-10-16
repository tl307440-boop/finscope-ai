from datetime import date, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.financial_metrics import FinancialMetricRepository
from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.schemas.freshness import FreshnessReport
from app.services.auth_types import UserRecord
from app.services.company_data import CompanyDataService, DatasetStatus
from app.services.runtime_settings import effective_settings
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
    rows = CompanyDataService(engine, effective_settings(engine)).status(instrument, now)
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
    rows = CompanyDataService(engine, effective_settings(engine)).ensure(instrument, now, user.id)
    return JSONResponse(
        status_code=200, content=success_response(request, _payload(instrument, now, rows))
    )


@router.get("/system/freshness", summary="全站数据新鲜度与后台进程状态")
def read_system_freshness(request: Request) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    report: FreshnessReport = build_report(engine, effective_settings(engine), utc_now())
    return JSONResponse(status_code=200, content=success_response(request, report))


class MetricSnapshotItem(BaseModel):
    metric_code: str
    period_type: str
    period_end: date
    value: str | None
    unit: str
    quality_flag: str
    reason: str | None
    published_at: datetime | None
    inputs: list[dict[str, object]]


class MetricSnapshotData(BaseModel):
    instrument_id: UUID
    as_of: date
    status: str
    notice: str
    items: list[MetricSnapshotItem]


@router.get(
    "/companies/{instrument_id}/metrics",
    summary="定时任务生成的财务体检快照（只含真实披露算出的指标，每项带取数来源）",
)
def read_company_metrics(
    instrument_id: UUID,
    request: Request,
    as_of: Annotated[date | None, Query()] = None,
) -> JSONResponse:
    instrument = _instrument(request, instrument_id)
    engine = cast(Engine, request.app.state.engine)
    day = as_of or utc_now().date()
    rows = FinancialMetricRepository(engine).latest_for_instrument(instrument.id, day)
    items = [
        MetricSnapshotItem(
            metric_code=row.metric_code,
            period_type=row.period_type,
            period_end=row.period_end,
            value=None if row.value is None else str(row.value),
            unit=row.unit,
            quality_flag=row.quality_flag,
            reason=_reason(row.source_facts),
            published_at=row.published_at,
            inputs=_inputs(row.source_facts),
        )
        for row in sorted(rows, key=lambda item: item.metric_code)
    ]
    payload = MetricSnapshotData(
        instrument_id=instrument.id,
        as_of=day,
        status="ready" if items else "empty",
        notice=(
            "指标由后端按已披露的财报事实计算，取数来源见每项 inputs。"
            if items
            else "还没有这家公司的指标快照：可能尚无真实财报，或定时任务还没跑到它。"
        ),
        items=items,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _reason(source: dict[str, object]) -> str | None:
    value = source.get("reason")
    return value if isinstance(value, str) else None


def _inputs(source: dict[str, object]) -> list[dict[str, object]]:
    value = source.get("inputs")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
