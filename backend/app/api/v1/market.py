from datetime import date, datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.instruments import InstrumentRepository
from app.repositories.market_bars import MarketBarRepository
from app.schemas.bars import build_bar_page
from app.utils.market_time import shanghai_pit
from app.utils.time import utc_now

router = APIRouter(tags=["行情"])


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


@router.get("/market/bars/{instrument_id}", summary="查询日线")
def read_daily_bars(
    instrument_id: UUID,
    request: Request,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    timeframe: Annotated[Literal["1d"], Query()] = "1d",
    adjustment: Annotated[Literal["none", "qfq", "hfq"], Query()] = "none",
    as_of: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=3000)] = 3000,
) -> JSONResponse:
    del timeframe
    engine = _engine(request)
    instrument = InstrumentRepository(engine).get(instrument_id)
    if instrument is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    pit = _parse_as_of(as_of)
    provider_id = MarketBarRepository(engine).ensure_akshare_provider()
    rows, truncated = MarketBarRepository(engine).list_daily(
        instrument_id=instrument.id,
        provider_id=provider_id,
        adjustment=adjustment,
        start=start,
        end=end,
        pit=pit,
        limit=limit,
    )
    payload = build_bar_page(
        instrument_id=str(instrument.id),
        display_symbol=instrument.display_symbol,
        name_cn=instrument.name_cn,
        adjustment=adjustment,
        pit=pit,
        rows=rows,
        truncated=truncated,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _parse_as_of(value: str | None) -> date:
    if value is None or not value.strip():
        return shanghai_pit(utc_now())
    text = value.strip()
    try:
        if len(text) == 10:
            return date.fromisoformat(text)
        return shanghai_pit(datetime.fromisoformat(text))
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message="as_of 无法解析", status_code=422) from exc
