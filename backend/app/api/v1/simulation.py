"""多角色研究推演（规格书 §20.6）。

默认引擎是 FinScope 规则引擎（确定性规则，不调用语言模型）；配置了语言模型后可以选
TradingAgents 原版图（engine=tradingagents）。两种引擎的工具都只读 as_of 及以前的数据。
"""

import json
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.simulations import RunSummary, SimulationRepository, StoredRun
from app.services.auth_types import UserRecord
from app.services.runtime_settings import effective_settings
from app.trading_agents.graph import SimulationResult
from app.trading_agents.pipeline import ENGINE_LABEL, PipelineConfig
from app.trading_agents.service import evaluate, run_and_save, run_grid
from app.utils.time import utc_now

router = APIRouter(tags=["研究推演"])

DISCLAIMER = "仅用于研究与教学，不构成交易建议，也不会发送委托。"
SYMBOL_PATTERN = r"^[A-Za-z0-9]{1,10}(\.(SH|SZ|sh|sz))?$"


class SimulationCreate(BaseModel):
    symbol: str = Field(pattern=SYMBOL_PATTERN)
    as_of: date
    max_debate_rounds: int = Field(default=2, ge=1, le=3)
    holding_days: int = Field(default=30, ge=5, le=180)
    risk_strictness: Literal["moderate", "conservative"] = "moderate"
    use_memory: bool = True
    engine: Literal["rules", "tradingagents"] = "rules"


class SimulationData(BaseModel):
    run_id: UUID
    status: str
    symbol: str
    as_of: date
    rating: str
    decision: str
    nodes: list[str]
    bull: str = ""
    bear: str = ""
    disclaimer: str
    engine: str
    engine_label: str
    config: dict[str, Any]
    detail: dict[str, Any]
    created_at: str
    duration_ms: int | None = None


class RunItem(BaseModel):
    run_id: UUID
    symbol: str
    as_of: date
    status: str
    rating: str
    confidence: str | None
    engine: str
    created_at: str
    excess_return: str | None
    verdict: str | None


class RunList(BaseModel):
    runs: list[RunItem]


class BacktestData(BaseModel):
    quality_flag: str
    actual_return: str | None = None
    benchmark_symbol: str | None = None
    benchmark_return: str | None = None
    excess_return: str | None = None
    verdict: str | None = None
    reflection: str | None = None
    holding_days: int | None = None
    exit_date: str | None = None
    note: str


class ExperimentCreate(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=8)
    start: date
    end: date
    step: int = Field(default=10, ge=1, le=60)
    holding_days: int = Field(default=30, ge=5, le=120)
    max_debate_rounds: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def _check(self) -> "ExperimentCreate":
        if self.start > self.end:
            raise ValueError("start 不能晚于 end")
        for item in self.symbols:
            if not item or len(item) > 16:
                raise ValueError("标的代码不合法")
        return self


class ExperimentData(BaseModel):
    config: dict[str, Any]
    samples: int
    skipped: list[str]
    skipped_count: int
    truncated: bool
    modes: list[dict[str, Any]]
    by_rating: list[dict[str, Any]]
    rows: list[dict[str, Any]]


class ReportData(BaseModel):
    run_id: UUID
    markdown: str


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _llm_ready(engine: Engine) -> bool:
    settings = effective_settings(engine)
    return bool(settings.llm_api_key and settings.llm_model)


def _data(saved: StoredRun) -> SimulationData:
    engine = str(saved.config.get("engine", "tradingagents-mock"))
    label = str(saved.config.get("engine_label", ""))
    if not label:
        label = ENGINE_LABEL if engine == "finscope-rules" else "TradingAgents 图（旧版 Mock 记录）"
    return SimulationData(
        run_id=saved.id,
        status=saved.status,
        symbol=saved.symbol,
        as_of=saved.as_of,
        rating=saved.rating,
        decision=saved.decision,
        nodes=list(saved.nodes),
        bull=saved.bull,
        bear=saved.bear,
        disclaimer=DISCLAIMER,
        engine=engine,
        engine_label=label,
        config=dict(saved.config),
        detail=dict(saved.detail),
        created_at=saved.created_at,
        duration_ms=saved.duration_ms,
    )


@router.post("/simulation/runs", summary="跑一轮多角色研究推演")
def create_simulation(
    body: SimulationCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    if body.as_of > utc_now().date():
        raise AppError(code="VALIDATION_ERROR", message="as_of 不能晚于今天", status_code=422)
    engine = _engine(request)
    symbol = body.symbol.upper()
    # 集成测试用 app.state.simulation_runner 换成确定性桩（规格书 §14.2 不依赖外网）。
    runner = getattr(request.app.state, "simulation_runner", None)
    if runner is None and body.engine == "tradingagents":
        if not _llm_ready(engine):
            raise AppError(
                code="LLM_NOT_CONFIGURED",
                message=(
                    "没有配置语言模型（LLM_API_KEY / LLM_MODEL），不能使用 TradingAgents 原版图"
                ),
                status_code=409,
            )
        from app.trading_agents.upstream import run_upstream

        def runner(symbol: str, as_of: date, rounds: int) -> SimulationResult:
            return run_upstream(symbol, as_of, rounds, engine=engine)

    if runner is not None:
        result = runner(symbol, body.as_of, body.max_debate_rounds)
        saved = SimulationRepository(engine).save(
            user_id=user.id,
            symbol=symbol,
            as_of=body.as_of,
            result=result,
            rounds=body.max_debate_rounds,
        )
        request.app.state.last_simulation_events = list(result.events)
    else:
        saved = run_and_save(
            engine,
            user_id=user.id,
            symbol=symbol,
            as_of=body.as_of,
            config=PipelineConfig(
                max_debate_rounds=body.max_debate_rounds,
                holding_days=body.holding_days,
                risk_strictness=body.risk_strictness,
                use_memory=body.use_memory,
            ),
        )
        request.app.state.last_simulation_events = list(saved.events)
    return JSONResponse(status_code=200, content=success_response(request, _data(saved)))


@router.get("/simulation/runs", summary="我的推演历史")
def list_simulations(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = SimulationRepository(_engine(request)).list_owned(user.id)
    payload = RunList(runs=[_item(row) for row in rows])
    return JSONResponse(status_code=200, content=success_response(request, payload))


def _item(row: RunSummary) -> RunItem:
    return RunItem(
        run_id=row.id,
        symbol=row.symbol,
        as_of=row.as_of,
        status=row.status,
        rating=row.rating,
        confidence=row.confidence,
        engine=row.engine,
        created_at=row.created_at,
        excess_return=row.excess_return,
        verdict=row.verdict,
    )


@router.get("/simulation/runs/{run_id}", summary="查看推演结果")
def read_simulation(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    saved = _owned(request, user, run_id)
    return JSONResponse(status_code=200, content=success_response(request, _data(saved)))


@router.get("/simulation/runs/{run_id}/report", summary="推演综合报告（Markdown）")
def read_simulation_report(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    saved = _owned(request, user, run_id)
    payload = ReportData(run_id=saved.id, markdown=render_markdown(_data(saved)))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.post("/simulation/runs/{run_id}/backtest", summary="用已入库收盘价结算超额收益")
def record_simulation_backtest(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    engine = _engine(request)
    evaluation = evaluate(engine, user.id, run_id)
    if evaluation is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    flag = SimulationRepository(engine).record_backtest(
        user.id, run_id, actual=evaluation.actual, note=evaluation.note, evaluation=evaluation
    )
    if flag is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    payload = BacktestData(
        quality_flag=flag,
        actual_return=None if evaluation.actual is None else str(evaluation.actual),
        benchmark_symbol=evaluation.benchmark_symbol,
        benchmark_return=None if evaluation.benchmark is None else str(evaluation.benchmark),
        excess_return=None if evaluation.excess is None else str(evaluation.excess),
        verdict=evaluation.verdict,
        reflection=evaluation.reflection or None,
        holding_days=evaluation.holding_days,
        exit_date=None if evaluation.exit_date is None else evaluation.exit_date.isoformat(),
        note=evaluation.note,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.post("/simulation/experiments", summary="网格回测：多角色推演 vs 单一技术面信号")
def create_experiment(
    body: ExperimentCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    del user
    result = run_grid(
        _engine(request),
        symbols=[item.upper() for item in body.symbols],
        start=body.start,
        end=body.end,
        step=body.step,
        holding_days=body.holding_days,
        max_debate_rounds=body.max_debate_rounds,
    )
    payload = ExperimentData.model_validate(result)
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/simulation/runs/{run_id}/stream", summary="重放推演节点（SSE）")
def stream_simulation(
    run_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> StreamingResponse:
    saved = _owned(request, user, run_id)
    data = _data(saved)

    def events() -> Iterator[str]:
        timeline = data.detail.get("timeline")
        rows = (
            timeline
            if isinstance(timeline, list) and timeline
            else [
                {"seq": index, "node": node, "kind": ""} for index, node in enumerate(data.nodes, 1)
            ]
        )
        for row in rows:
            yield _sse(
                "node_started",
                {
                    "run_id": str(data.run_id),
                    "seq": row.get("seq"),
                    "node": row.get("node"),
                    "role": row.get("role", ""),
                },
            )
            kind = row.get("kind")
            if kind == "analyst":
                analyst = next(
                    (
                        item
                        for item in data.detail.get("analysts", [])
                        if item.get("role") == row.get("node")
                    ),
                    None,
                )
                if analyst is not None:
                    yield _sse(
                        "agent_signal",
                        {
                            "seq": row.get("seq"),
                            "analyst": analyst.get("analyst"),
                            "score": analyst.get("score"),
                            "stance": analyst.get("stance"),
                            "summary": analyst.get("summary"),
                        },
                    )
            elif kind == "debate":
                yield _sse(
                    "debate_turn",
                    {
                        "seq": row.get("seq"),
                        "speaker": row.get("role"),
                        "argument": row.get("content"),
                    },
                )
            elif kind == "risk":
                yield _sse(
                    "risk_verdict",
                    {
                        "seq": row.get("seq"),
                        "profile": row.get("role"),
                        "notice": row.get("content"),
                    },
                )
        yield _sse(
            "simulation_completed",
            {
                "run_id": str(data.run_id),
                "status": data.status,
                "final_rating": data.rating,
                "decision": {"rating": data.rating, "confidence": _confidence(data)},
            },
        )

    return StreamingResponse(events(), media_type="text/event-stream")


def _owned(request: Request, user: UserRecord, run_id: UUID) -> StoredRun:
    saved = SimulationRepository(_engine(request)).get_owned(user.id, run_id)
    if saved is None:
        raise AppError(code="NOT_FOUND", message="找不到这次推演", status_code=404)
    return saved


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _confidence(data: SimulationData) -> object:
    decision = data.detail.get("decision")
    return decision.get("confidence") if isinstance(decision, dict) else None


def render_markdown(data: SimulationData) -> str:
    detail = data.detail
    lines = [
        f"# {data.symbol} 多角色研究推演报告",
        "",
        f"- 时间基准 as_of：{data.as_of.isoformat()}（所有工具只读当日及以前已公开的数据）",
        f"- 引擎：{data.engine_label}",
        f"- 研究评级：**{data.rating}**，置信度 {_confidence(data) or '—'}",
        f"- 运行号：{data.run_id}",
        "",
        "## 一、分析师团队",
    ]
    for item in detail.get("analysts", []):
        score = item.get("score") or "不出分"
        lines.append(f"### {item.get('role')}（{item.get('stance')}，{score}）")
        lines.append(str(item.get("summary", "")))
        for finding in item.get("findings", []):
            refs = "；".join(cite.get("label", "") for cite in finding.get("citations", []))
            mark = {1: "利多", -1: "利空"}.get(finding.get("polarity"), "中性")
            lines.append(
                f"- [{mark}] {finding.get('text')}" + (f"（引用：{refs}）" if refs else "")
            )
        for gap in item.get("gaps", []):
            lines.append(f"- [缺口] {gap}")
        lines.append("")
    lines.append("## 二、多空辩论")
    for item in detail.get("debates", []):
        lines.append(f"### 第 {item.get('round')} 轮")
        lines.append(f"- 多方：{item.get('bull')}")
        lines.append(f"- 空方：{item.get('bear')}")
        lines.append(f"- 主导研究员：{item.get('arbitration')}")
    if detail.get("memo"):
        lines.extend(["", f"**辩论备忘录**：{detail.get('memo')}"])
    lines.extend(["", "## 三、交易员提案与风控委员会"])
    for item in detail.get("proposals", []):
        lines.append(
            f"- 提案 #{item.get('revision')}：{item.get('display_rating')}，{item.get('rationale')}"
        )
    for item in detail.get("risks", []):
        verdict = (
            "否决" if item.get("veto_reason") else ("通过" if item.get("approved") else "有保留")
        )
        lines.append(
            f"- {item.get('profile')}（第 {item.get('revision')} 版）"
            f"{verdict}：{item.get('content')}"
        )
    lines.extend(["", "## 四、组合经理决议", data.decision, ""])
    memory = detail.get("memory") or []
    if memory:
        lines.append("## 五、决策记忆")
        for note in memory:
            lines.append(f"- {note.get('reflection')}")
        lines.append("")
    lines.append(f"> {DISCLAIMER}")
    return "\n".join(lines)
