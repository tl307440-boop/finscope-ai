from datetime import date, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import require_admin
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.providers.vintage import pit_date, select_visible
from app.repositories.macro import MacroRepository
from app.repositories.macro_china import MacroChinaRepository
from app.repositories.macro_regime import MacroRegimeRepository
from app.schemas.auth import fred_availability
from app.schemas.macro import build_assessment, build_catalog, build_observations
from app.services.auth_types import UserRecord
from app.services.china_macro import CHINA_SERIES, parse_macro_csv, public_status
from app.services.macro_regime import assess_as_of
from app.services.macro_rules import RULE_NOTICE
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

router = APIRouter(prefix="/macro", tags=["宏观"])


class ChinaObservationImport(BaseModel):
    """正式来源 CSV 的原文。字段与 parse_macro_csv 要求的一致。"""

    csv_text: str = Field(min_length=1)


class ChinaSeriesStatus(BaseModel):
    code: str
    name: str
    unit: str
    observation_count: int
    status: str
    latest_value: str | None = None
    latest_observation_date: date | None = None


class ChinaObservationImportResult(BaseModel):
    accepted: int
    duplicate_count: int
    errors: list[str]
    series: list[ChinaSeriesStatus]
    notice: str


def _repository(request: Request) -> MacroRepository:
    engine = cast(Engine, request.app.state.engine)
    return MacroRepository(engine)


@router.get("/series", summary="美国宏观序列目录")
def read_macro_series(request: Request) -> JSONResponse:
    seeds = _repository(request).list_series()
    settings = effective_settings(cast(Engine, request.app.state.engine))
    payload = build_catalog(seeds, availability=fred_availability(settings))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/series/{series_code}/observations", summary="按历史时点查看宏观观测")
def read_macro_observations(
    series_code: str,
    request: Request,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    as_of: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> JSONResponse:
    pit = _parse_as_of(as_of)
    rows = _repository(request).list_vintages(series_code, start=start, end=end)
    if rows is None:
        raise AppError(code="NOT_FOUND", message="暂无该宏观序列", status_code=404)
    visible = select_visible(rows, pit)
    payload = build_observations(series_code=series_code, pit=pit, rows=visible, limit=limit)
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/assessments", summary="按历史时点计算宏观规则分数")
def read_macro_assessment(
    request: Request,
    as_of: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    pit = _parse_as_of(as_of)
    payload = build_assessment(assess_as_of(_repository(request), pit), pit)
    return JSONResponse(status_code=200, content=success_response(request, payload))


class RegimeHistoryItem(BaseModel):
    as_of: date
    model_version: str
    rule_version: str
    regime_code: str
    growth: int | None = None
    inflation: int | None = None
    liquidity: int | None = None
    computed_at: datetime


class RegimeHistory(BaseModel):
    items: list[RegimeHistoryItem]
    notice: str


def _score_of(payload: dict[str, object], key: str) -> int | None:
    block = payload.get(key)
    value = block.get("value") if isinstance(block, dict) else None
    return value if isinstance(value, int) else None


@router.get("/regime-history", summary="定时任务留下的宏观状态判断快照（按观察日倒序）")
def read_regime_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=366)] = 60,
) -> JSONResponse:
    engine = cast(Engine, request.app.state.engine)
    rows = MacroRegimeRepository(engine).history(limit)
    payload = RegimeHistory(
        items=[
            RegimeHistoryItem(
                as_of=row.as_of,
                model_version=row.model_version,
                rule_version=row.rule_version,
                regime_code=row.regime_code,
                growth=_score_of(row.payload, "growth"),
                inflation=_score_of(row.payload, "inflation"),
                liquidity=_score_of(row.payload, "liquidity"),
                computed_at=row.computed_at,
            )
            for row in rows
        ],
        notice=RULE_NOTICE,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _parse_as_of(value: str | None) -> date:
    if value is None or not value.strip():
        return pit_date(utc_now())
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        moment = datetime.fromisoformat(text)
        return pit_date(moment)
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message="as_of 无法解析", status_code=422) from exc


@router.post("/china/observations", summary="导入中国宏观正式 CSV（仅管理员）")
def import_china_observations(
    body: ChinaObservationImport,
    request: Request,
    admin: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    """CSV 原文入库的正式入口。解析失败的行走 errors，不落库。"""

    del admin
    rows, errors = parse_macro_csv(body.csv_text)
    engine = cast(Engine, request.app.state.engine)
    repository = MacroChinaRepository(engine)
    if rows:
        accepted, duplicates = repository.import_observations(rows)
    else:
        repository.ensure_catalog()
        accepted, duplicates = 0, []
    summaries = {item.series_code: item for item in repository.list_series_summary()}
    today = utc_now().date()
    series: list[ChinaSeriesStatus] = []
    for code, name, unit in CHINA_SERIES:
        summary = summaries.get(code)
        count = 0 if summary is None else summary.observation_count
        latest = None if summary is None else summary.latest
        series.append(
            ChinaSeriesStatus(
                code=code,
                name=name,
                unit=unit,
                observation_count=count,
                status=public_status(
                    code,
                    observation_count=count,
                    latest_observation_date=None if latest is None else latest.observation_date,
                    today=today,
                ),
                latest_value=None if latest is None else str(latest.value),
                latest_observation_date=None if latest is None else latest.observation_date,
            )
        )
    payload = ChinaObservationImportResult(
        accepted=accepted,
        duplicate_count=len(duplicates),
        errors=[*errors, *duplicates],
        series=series,
        notice=(
            "accepted 只统计本次真正入库的条数；重复行与不合规行在 errors 里逐条说明。"
            "没有导入成功之前，页面上的序列仍然是 unavailable。"
        ),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))
