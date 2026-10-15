from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.responses import success_response
from app.catalog.reference_data import reference_instruments
from app.core.exceptions import AppError
from app.providers.sec_edgar import load_fixture, parse_companyfacts, payload_sha256
from app.repositories.filings import FilingRepository
from app.repositories.instruments import InstrumentRepository
from app.schemas.filings import build_facts, build_filings
from app.utils.identifiers import normalize_cik

router = APIRouter(prefix="/sec", tags=["SEC"])


def _repository(request: Request) -> FilingRepository:
    engine = cast(Engine, request.app.state.engine)
    return FilingRepository(engine)


def _ensure(request: Request, cik: str) -> str:
    engine = cast(Engine, request.app.state.engine)
    InstrumentRepository(engine).upsert_reference(reference_instruments())
    payload = load_fixture()
    facts = [fact for fact in parse_companyfacts(payload) if fact.cik == cik]
    if not facts:
        raise AppError(code="NOT_FOUND", message="样例里没有这个 CIK", status_code=404)
    FilingRepository(engine).upsert_facts(facts, payload_sha256(payload))
    return cik


@router.get("/filings", summary="查看 SEC 样例申报")
def read_filings(
    request: Request,
    cik: Annotated[str, Query(min_length=1, max_length=10)] = "320193",
) -> JSONResponse:
    normalized = _cik(cik)
    _ensure(request, normalized)
    payload = build_filings(normalized, _repository(request).list_filings(normalized))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/facts", summary="按申报日查看 SEC 样例事实")
def read_facts(
    request: Request,
    as_of: Annotated[date, Query()],
    cik: Annotated[str, Query(min_length=1, max_length=10)] = "320193",
) -> JSONResponse:
    normalized = _cik(cik)
    _ensure(request, normalized)
    payload = build_facts(normalized, as_of, _repository(request).list_facts(normalized, as_of))
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _cik(value: str) -> str:
    try:
        return normalize_cik(value)
    except ValueError as exc:
        raise AppError(
            code="VALIDATION_ERROR",
            message="CIK 必须是 1 到 10 位数字",
            status_code=422,
        ) from exc
