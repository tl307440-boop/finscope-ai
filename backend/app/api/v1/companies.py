from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.responses import success_response
from app.catalog.reference_data import reference_instruments
from app.core.exceptions import AppError
from app.providers.sec_edgar import load_fixture, parse_companyfacts, payload_sha256
from app.repositories.filings import FactRecord, FilingRepository
from app.repositories.instruments import InstrumentRepository
from app.schemas.research import build_research
from app.utils.identifiers import normalize_cik

router = APIRouter(tags=["公司研究"])


@router.get("/companies/{instrument_id}/research", summary="对比两个报告期")
def read_company_research(
    instrument_id: UUID,
    request: Request,
    as_of: Annotated[date, Query()],
    period_a: Annotated[date | None, Query()] = None,
    period_b: Annotated[date | None, Query()] = None,
) -> JSONResponse:
    if period_a is not None and period_b is not None and period_a == period_b:
        raise AppError(code="VALIDATION_ERROR", message="请选择两个不同的报告期", status_code=422)
    engine = cast(Engine, request.app.state.engine)
    instrument = InstrumentRepository(engine).get(instrument_id)
    if instrument is None:
        raise AppError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    facts: list[FactRecord] = []
    if instrument.cik:
        facts = _facts(engine, normalize_cik(instrument.cik), as_of)
    payload = build_research(
        instrument_id=instrument.id,
        name_cn=instrument.name_cn,
        facts=facts,
        period_a=period_a,
        period_b=period_b,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _facts(engine: Engine, cik: str, as_of: date) -> list[FactRecord]:
    InstrumentRepository(engine).upsert_reference(reference_instruments())
    payload = load_fixture()
    parsed = [fact for fact in parse_companyfacts(payload) if fact.cik == cik]
    repository = FilingRepository(engine)
    if parsed:
        repository.upsert_facts(parsed, payload_sha256(payload))
    return repository.list_facts(cik, as_of)
