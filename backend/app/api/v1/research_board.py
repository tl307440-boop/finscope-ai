from collections.abc import Iterator
from datetime import date
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.simulations import SimulationRepository
from app.services.auth_types import UserRecord
from app.services.china_macro import CHINA_SERIES, series_status
from app.services.daily_digest import build_digest
from app.services.disclosure_events import heat_label
from app.trading_agents.graph import run_mock_graph
from app.trading_agents.vendor import income_visible

router = APIRouter(tags=["研究板"])


class SimulationCreate(BaseModel):
    symbol: str = Field(pattern=r"^600519\.SH$")
    as_of: date
    max_debate_rounds: int = Field(default=1, ge=1, le=3)


class SimulationData(BaseModel):
    run_id: UUID
    status: str
    symbol: str
    rating: str
    decision: str
    nodes: list[str]
    disclaimer: str


class ChinaSeriesItem(BaseModel):
    code: str
    name: str
    status: str


class ChinaSeriesList(BaseModel):
    series: list[ChinaSeriesItem]
    notice: str


class HotspotData(BaseModel):
    label: str
    count: int


class DigestLine(BaseModel):
    title: str
    summary: str


class DigestData(BaseModel):
    items: list[DigestLine]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


@router.get("/market/hotspots", summary="合规公告热度")
def read_hotspots(request: Request) -> JSONResponse:
    payload = HotspotData(label=heat_label(0), count=0)
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/macro/china", summary="中国宏观序列状态")
def read_china_macro(request: Request) -> JSONResponse:
    payload = ChinaSeriesList(
        series=[
            ChinaSeriesItem(code=code, name=name, status=series_status(code, False))
            for code, name, _unit in CHINA_SERIES
        ],
        notice=(
            "国家统计局页面本次没有接通。"
            "没有正式 CSV 之前，这些序列保持不可用，不填入网上看到的数字。"
        ),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/me/digest", summary="站内每日摘要")
def read_digest(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    del user
    items = build_digest([], 0)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            DigestData(
                items=[DigestLine(title=item.title, summary=item.summary) for item in items]
            ),
        ),
    )


@router.post("/simulation/runs", summary="用 Mock 跑一轮研究推演")
def create_simulation(
    body: SimulationCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    context = income_visible(body.symbol, body.as_of)
    result = run_mock_graph(
        symbol=body.symbol,
        as_of_text=body.as_of.isoformat(),
        context=context,
        max_debate_rounds=body.max_debate_rounds,
    )
    saved = SimulationRepository(_engine(request)).save(
        user_id=user.id,
        symbol=body.symbol,
        as_of=body.as_of,
        result=result,
        rounds=body.max_debate_rounds,
    )
    request.app.state.last_simulation_events = list(result.events)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            SimulationData(
                run_id=saved.id,
                status=saved.status,
                symbol=saved.symbol,
                rating=saved.rating,
                decision=saved.decision,
                nodes=list(saved.nodes),
                disclaimer="仅用于研究与教学，不构成交易建议，也不会发送委托。",
            ),
        ),
    )


@router.get("/simulation/runs/{run_id}", summary="查看推演结果")
def read_simulation(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    saved = SimulationRepository(_engine(request)).get_owned(user.id, run_id)
    if saved is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            SimulationData(
                run_id=saved.id,
                status=saved.status,
                symbol=saved.symbol,
                rating=saved.rating,
                decision=saved.decision,
                nodes=list(saved.nodes),
                disclaimer="仅用于研究与教学，不构成交易建议，也不会发送委托。",
            ),
        ),
    )


@router.get("/simulation/runs/{run_id}/stream", summary="重放推演节点")
def stream_simulation(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> StreamingResponse:
    saved = SimulationRepository(_engine(request)).get_owned(user.id, run_id)
    if saved is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)

    def events() -> Iterator[str]:
        for node in saved.nodes:
            yield f'event: node_started\ndata: {{"node":"{node}"}}\n\n'
        yield f'event: rating_ready\ndata: {{"final_rating":"{saved.rating}"}}\n\n'

    return StreamingResponse(events(), media_type="text/event-stream")
