from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.theses import EvidenceRecord, ThesisRecord, ThesisRepository
from app.services.auth_types import UserRecord

router = APIRouter(tags=["行业研究"])


class IndustryItem(BaseModel):
    id: UUID
    code: str
    name: str
    description: str


class IndustryList(BaseModel):
    industries: list[IndustryItem]


class ThesisCreate(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    hypothesis: str = Field(min_length=1, max_length=400)
    support_condition: str = Field(min_length=1, max_length=200)
    challenge_condition: str = Field(min_length=1, max_length=200)
    observation_window_days: int = Field(default=90, ge=1, le=3650)


class EvidenceCreate(BaseModel):
    fact_id: UUID
    stance: str = Field(pattern=r"^(support|challenge)$")


class AssessmentCreate(BaseModel):
    as_of: date


class EvidenceItem(BaseModel):
    id: UUID
    stance: str
    quote: str
    published_at: date
    source_url: str


class ThesisItem(BaseModel):
    id: UUID
    title: str
    hypothesis: str
    support_condition: str
    challenge_condition: str
    status: str
    rationale: str
    evidence: list[EvidenceItem]


class ThesisList(BaseModel):
    theses: list[ThesisItem]


class FactItem(BaseModel):
    id: UUID
    quote: str
    filed: date
    source_url: str


class FactList(BaseModel):
    facts: list[FactItem]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _evidence(row: EvidenceRecord) -> EvidenceItem:
    return EvidenceItem(
        id=row.id,
        stance=row.stance,
        quote=row.quote,
        published_at=row.published_at,
        source_url=row.source_url,
    )


def _thesis(row: ThesisRecord) -> ThesisItem:
    return ThesisItem(
        id=row.id,
        title=row.title,
        hypothesis=row.hypothesis,
        support_condition=row.support_condition,
        challenge_condition=row.challenge_condition,
        status=row.status,
        rationale=row.rationale,
        evidence=[_evidence(item) for item in row.evidence],
    )


@router.get("/industries", summary="行业分类")
def list_industries(
    request: Request,
    q: Annotated[str, Query(max_length=40)] = "",
) -> JSONResponse:
    rows = ThesisRepository(_engine(request)).list_industries(q)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            IndustryList(
                industries=[
                    IndustryItem(
                        id=row.id,
                        code=row.code,
                        name=row.name,
                        description=row.description,
                    )
                    for row in rows
                ]
            ),
        ),
    )


@router.get("/industries/{industry_id}/facts", summary="行业成员的已入库事实")
def list_industry_facts(
    industry_id: UUID,
    request: Request,
    as_of: Annotated[date, Query()],
) -> JSONResponse:
    rows = ThesisRepository(_engine(request)).member_facts(industry_id, as_of)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            FactList(
                facts=[
                    FactItem(id=row.id, quote=row.quote, filed=row.filed, source_url=row.source_url)
                    for row in rows
                ]
            ),
        ),
    )


@router.get("/industries/{industry_id}/theses", summary="我的研究假设")
def list_theses(
    industry_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    as_of: Annotated[date, Query()],
) -> JSONResponse:
    rows = ThesisRepository(_engine(request)).list_theses(user.id, industry_id, as_of)
    if rows is None:
        raise AppError(code="NOT_FOUND", message="找不到这个行业", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(request, ThesisList(theses=[_thesis(row) for row in rows])),
    )


@router.post("/industries/{industry_id}/theses", summary="写下可证伪的假设")
def create_thesis(
    industry_id: UUID,
    body: ThesisCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    created = ThesisRepository(_engine(request)).create_thesis(
        user_id=user.id,
        industry_id=industry_id,
        title=body.title,
        hypothesis=body.hypothesis,
        support_condition=body.support_condition,
        challenge_condition=body.challenge_condition,
        observation_window_days=body.observation_window_days,
    )
    if created is None:
        raise AppError(code="NOT_FOUND", message="找不到这个行业", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _thesis(created)))


@router.post("/theses/{thesis_id}/evidence", summary="为假设附上已入库依据")
def add_evidence(
    thesis_id: UUID,
    body: EvidenceCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    try:
        updated = ThesisRepository(_engine(request)).add_fact_evidence(
            user_id=user.id,
            thesis_id=thesis_id,
            fact_id=body.fact_id,
            stance=body.stance,
        )
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    if updated is None:
        raise AppError(code="NOT_FOUND", message="找不到可引用的假设或事实", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _thesis(updated)))


@router.post("/theses/{thesis_id}/assessments", summary="按截止日更新假设状态")
def assess_thesis(
    thesis_id: UUID,
    body: AssessmentCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    updated = ThesisRepository(_engine(request)).assess(user.id, thesis_id, body.as_of)
    if updated is None:
        raise AppError(code="NOT_FOUND", message="找不到这个假设", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _thesis(updated)))
