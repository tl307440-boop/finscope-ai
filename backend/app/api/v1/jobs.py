from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.deps import require_admin
from app.api.responses import success_response
from app.catalog.macro_series import catalog_codes
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.providers.akshare_bars import target_for
from app.repositories.macro import MacroRepository
from app.repositories.market_bars import MarketBarRepository
from app.repositories.sync_jobs import JobSnapshot, SyncJobRepository
from app.schemas.jobs import EnqueueData, SyncJobBody, SyncJobList, SyncJobPublic
from app.services.auth_types import UserRecord
from app.services.sync_queue import dedupe_key_for
from app.utils.time import utc_now

router = APIRouter(prefix="/admin", tags=["管理"])


def _repository(request: Request) -> SyncJobRepository:
    return SyncJobRepository(cast(Engine, request.app.state.engine))


@router.post("/sync-jobs", summary="提交同步任务")
def create_sync_job(
    body: SyncJobBody,
    request: Request,
    user: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    scope = _validated_scope(engine, body.job_type, body.scope)
    try:
        key = dedupe_key_for(body.job_type, scope)
    except (KeyError, ValueError) as exc:
        raise AppError(code="VALIDATION_ERROR", message="任务范围不完整", status_code=422) from exc
    provider_id = _provider_id(engine, body.job_type)
    job, created = _repository(request).enqueue(
        job_type=body.job_type,
        provider_id=provider_id,
        scope={key: value for key, value in scope.items()},
        dedupe_key=key,
        requested_by=user.id,
        now=utc_now(),
    )
    payload = EnqueueData(created=created, job=_public(job))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/sync-jobs", summary="同步任务列表")
def list_sync_jobs(
    request: Request,
    _: Annotated[UserRecord, Depends(require_admin)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> JSONResponse:
    jobs = _repository(request).list_recent(limit)
    payload = SyncJobList(jobs=[_public(job) for job in jobs])
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.post("/sync-jobs/{job_id}/retries", summary="重试失败的同步任务")
def retry_sync_job(
    job_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    repository = _repository(request)
    existing = repository.get(job_id)
    if existing is None:
        raise AppError(code="NOT_FOUND", message="没有这张任务", status_code=404)
    if existing.status in {"queued", "running"}:
        raise AppError(code="JOB_ACTIVE", message="任务还在执行，不能另开一条", status_code=409)
    if existing.status != "failed":
        raise AppError(code="JOB_CLOSED", message="只有失败的任务可以重试", status_code=409)
    job, created = repository.enqueue(
        job_type=existing.job_type,
        provider_id=existing.provider_id,
        scope=dict(existing.scope),
        dedupe_key=existing.dedupe_key,
        requested_by=user.id,
        now=utc_now(),
    )
    payload = EnqueueData(created=created, job=_public(job))
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _validated_scope(engine: Engine, job_type: str, scope: dict[str, str]) -> dict[str, str]:
    if job_type == "akshare_daily":
        symbol = scope.get("display_symbol", "").upper()
        if target_for(symbol) is None:
            raise AppError(
                code="VALIDATION_ERROR",
                message="这三只股票以外的日线尚未开放",
                status_code=422,
            )
        start = _date(scope.get("observation_start"))
        end = _date(scope.get("observation_end"))
        if end < start:
            raise AppError(code="VALIDATION_ERROR", message="观察区间无效", status_code=422)
        adjustment = scope.get("adjustment", "none")
        if adjustment not in {"none", "qfq", "hfq"}:
            raise AppError(code="VALIDATION_ERROR", message="不认识的复权方式", status_code=422)
        return {
            "display_symbol": symbol,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "adjustment": adjustment,
        }
    if job_type == "fred_series":
        if not get_settings().fred_api_key.strip():
            raise AppError(
                code="DATA_UNAVAILABLE",
                message="未配置 FRED 密钥，数据源不可用",
                status_code=503,
            )
        series_code = scope.get("series_code", "").upper()
        if series_code not in catalog_codes():
            raise AppError(
                code="VALIDATION_ERROR", message="不在已核对的宏观序列里", status_code=422
            )
        start = _date(scope.get("observation_start"))
        end = _date(scope.get("observation_end"))
        if end < start:
            raise AppError(code="VALIDATION_ERROR", message="观察区间无效", status_code=422)
        MacroRepository(engine).ensure_catalog()
        return {
            "series_code": series_code,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
        }
    raise AppError(code="VALIDATION_ERROR", message="不认识的任务类型", status_code=422)


def _date(value: str | None) -> date:
    if not value:
        raise AppError(code="VALIDATION_ERROR", message="任务日期无法解析", status_code=422)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_ERROR", message="任务日期无法解析", status_code=422
        ) from exc


def _provider_id(engine: Engine, job_type: str) -> UUID:
    if job_type == "akshare_daily":
        return MarketBarRepository(engine).ensure_akshare_provider()
    MacroRepository(engine).ensure_catalog()
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.models.provider import DataProvider

    with Session(engine) as session:
        provider = session.scalars(select(DataProvider).where(DataProvider.code == "fred")).one()
        return provider.id


def _public(job: JobSnapshot) -> SyncJobPublic:
    return SyncJobPublic(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        dedupe_key=job.dedupe_key,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        rows_written=job.rows_written,
        error_code=job.error_code,
        error_summary=job.error_summary,
        progress_stage=job.progress_stage,
        requested_at=job.requested_at,
    )
