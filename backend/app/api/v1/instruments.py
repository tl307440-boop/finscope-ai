from datetime import date
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.instruments import InstrumentRepository
from app.schemas.instruments import (
    CalendarDay,
    SearchData,
    build_calendar_day,
    build_search_key,
    search_instruments,
)

router = APIRouter(tags=["证券"])
_EXCHANGES = {"SSE", "SZSE", "BSE", "NASDAQ", "NYSE"}


def _repository(request: Request) -> InstrumentRepository:
    engine = cast(Engine, request.app.state.engine)
    return InstrumentRepository(engine)


@router.get("/instruments/search", summary="搜索证券")
def search_instruments_route(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=64)],
    market: Annotated[Literal["CN", "US"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JSONResponse:
    records = _repository(request).search(build_search_key(q), market=market, limit=limit)
    payload: SearchData = search_instruments(records, query=q, limit=limit)
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/instruments/{instrument_id}", summary="证券元数据")
def read_instrument(instrument_id: UUID, request: Request) -> JSONResponse:
    record = _repository(request).get(instrument_id)
    if record is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    payload = search_instruments([record], query=record.canonical_code, limit=1).results[0]
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/market/calendar", summary="交易日")
def read_calendar(
    request: Request,
    exchange: Annotated[str, Query(min_length=2, max_length=16)],
    session_date: date,
) -> JSONResponse:
    normalized = exchange.strip().upper()
    if normalized not in _EXCHANGES:
        raise AppError(code="VALIDATION_ERROR", message="不认识这个交易所", status_code=422)
    repository = _repository(request)
    payload: CalendarDay = build_calendar_day(
        normalized,
        session_date,
        repository.calendar_open(normalized, session_date),
        repository.calendar_source(normalized, session_date),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))
