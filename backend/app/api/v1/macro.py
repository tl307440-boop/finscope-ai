from datetime import date, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.providers.vintage import pit_date, select_visible
from app.repositories.macro import MacroRepository
from app.schemas.auth import fred_availability
from app.schemas.macro import build_catalog, build_observations
from app.utils.time import utc_now

router = APIRouter(prefix="/macro", tags=["宏观"])


def _repository(request: Request) -> MacroRepository:
    engine = cast(Engine, request.app.state.engine)
    return MacroRepository(engine)


@router.get("/series", summary="美国宏观序列目录")
def read_macro_series(request: Request) -> JSONResponse:
    seeds = _repository(request).list_series()
    payload = build_catalog(seeds, availability=fred_availability(get_settings()))
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
