from collections.abc import Iterator
from datetime import date
from decimal import Decimal
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
from app.trading_agents.backtest import window_return
from app.trading_agents.upstream import run_upstream

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
    bull: str = ""
    bear: str = ""
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


class BacktestData(BaseModel):
    quality_flag: str
    actual_return: str | None = None
    note: str


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
    result = run_upstream(body.symbol, body.as_of, body.max_debate_rounds)
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
                bull=saved.bull,
                bear=saved.bear,
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
                bull=saved.bull,
                bear=saved.bear,
                disclaimer="仅用于研究与教学，不构成交易建议，也不会发送委托。",
            ),
        ),
    )


@router.post("/simulation/runs/{run_id}/backtest", summary="用已入库收盘价核算区间收益")
def record_simulation_backtest(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    saved = SimulationRepository(_engine(request)).get_owned(user.id, run_id)
    if saved is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    measured = window_return(_closes(_engine(request), saved.symbol), saved.as_of, 30)
    if measured is None:
        note = "已入库日线不足以计算区间收益，没有写入收益率"
        actual = None
    else:
        note = f"{measured.entry_date.isoformat()} 至 {measured.exit_date.isoformat()} 的收盘价变化"
        actual = measured.actual
    flag = SimulationRepository(_engine(request)).record_backtest(
        user.id,
        run_id,
        actual=actual,
        note=note,
    )
    if flag is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            BacktestData(
                quality_flag=flag,
                actual_return=None if actual is None else str(actual),
                note=note,
            ),
        ),
    )


def _closes(engine: Engine, symbol: str) -> list[tuple[date, Decimal]]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.db.models.market import Instrument, MarketBar

    code, _, suffix = symbol.partition(".")
    exchange = "SSE" if suffix == "SH" else "SZSE"
    with Session(engine) as session:
        instrument = session.scalars(
            select(Instrument).where(Instrument.exchange == exchange, Instrument.symbol == code)
        ).one_or_none()
        if instrument is None:
            return []
        rows = session.scalars(
            select(MarketBar)
            .where(MarketBar.instrument_id == instrument.id, MarketBar.timeframe == "1d")
            .order_by(MarketBar.trade_date)
        ).all()
        return [(row.trade_date, row.close) for row in rows if isinstance(row.close, Decimal)]


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
