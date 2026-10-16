import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.simulation import (
    AgentBacktestEvaluation,
    AgentDebateRound,
    AgentNodeTrace,
    AgentRiskAssessment,
    AgentSimulationDecision,
    AgentSimulationRun,
)
from app.trading_agents.graph import SimulationResult
from app.utils.time import utc_now

DISCLAIMER = "仅用于研究与教学，不构成交易建议，也不会发送委托。"
DISPLAY_RATING = {
    "buy": "Buy",
    "overweight": "Overweight",
    "hold": "Hold",
    "underweight": "Underweight",
    "sell": "Sell",
    "review": "REVIEW",
}
PROFILES = {
    "Aggressive Analyst": "aggressive",
    "Conservative Analyst": "conservative",
    "Neutral Analyst": "neutral",
}


@dataclass(frozen=True)
class StoredRun:
    id: UUID
    symbol: str
    as_of: date
    status: str
    rating: str
    decision: str
    nodes: tuple[str, ...]
    events: tuple[str, ...]
    bull: str = ""
    bear: str = ""
    config: dict[str, object] = field(default_factory=dict)
    detail: dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    duration_ms: int | None = None


@dataclass(frozen=True)
class RunSummary:
    id: UUID
    symbol: str
    as_of: date
    status: str
    rating: str
    confidence: str | None
    engine: str
    created_at: str
    excess_return: str | None
    verdict: str | None


@dataclass(frozen=True)
class PastDecision:
    decision_id: UUID
    as_of: date
    rating: str
    holding_days: int
    drivers: tuple[str, ...]
    risks: tuple[str, ...]


@dataclass(frozen=True)
class Evaluation:
    actual: Decimal | None
    benchmark_symbol: str | None
    benchmark: Decimal | None
    excess: Decimal | None
    verdict: str | None
    reflection: str
    holding_days: int
    entry_date: date
    exit_date: date | None
    quality_flag: str
    note: str


class SimulationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self,
        *,
        user_id: UUID,
        symbol: str,
        as_of: date,
        result: SimulationResult,
        rounds: int,
        duration_ms: int | None = None,
    ) -> StoredRun:
        now = utc_now()
        run_id = uuid.uuid4()
        config = dict(result.config) or {"max_debate_rounds": rounds, "orders": False}
        rating_key = _rating_key(result.rating)
        with Session(self.engine) as session, session.begin():
            session.add(
                AgentSimulationRun(
                    id=run_id,
                    user_id=user_id,
                    symbol=symbol,
                    as_of=as_of,
                    status=result.status,
                    current_node="Portfolio Manager",
                    config_snapshot=config,
                    duration_ms=duration_ms,
                    created_at=now,
                    finished_at=now,
                )
            )
            session.flush()
            for index, trace in enumerate(result.traces, start=1):
                session.add(
                    AgentNodeTrace(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        seq=index,
                        node_name=trace.node_name,
                        role_name=trace.role or None,
                        output_content=trace.content,
                        structured_output=trace.structured,
                        citations=list(trace.citations) or None,
                        created_at=now,
                    )
                )
            for debate in result.debates:
                session.add(
                    AgentDebateRound(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        round_no=debate.round_no,
                        bull_claim=debate.bull_claim,
                        bear_claim=debate.bear_claim,
                        bull_citations=list(debate.bull_citations),
                        bear_citations=list(debate.bear_citations),
                        lead_arbitration=debate.arbitration or None,
                        unsupported_claims=list(debate.unsupported),
                        converged_flag=debate.converged,
                        created_at=now,
                    )
                )
            for risk in result.risks:
                session.add(
                    AgentRiskAssessment(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        risk_profile=PROFILES.get(risk.profile, "neutral"),
                        content=risk.content,
                        approved=risk.approved,
                        veto_reason=risk.veto_reason,
                        stress_test_scenario=risk.scenario,
                        estimated_max_drawdown=risk.drawdown,
                        suggested_mitigations=list(risk.mitigations),
                        revision_no=risk.revision,
                        created_at=now,
                    )
                )
            detail = result.detail
            session.add(
                AgentSimulationDecision(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    simulated_rating=rating_key,
                    final_trade_decision=result.decision,
                    confidence_score=_decimal(detail.get("confidence")),
                    target_horizon=_text(detail.get("horizon")),
                    suggested_allocation_range=_text(detail.get("allocation")),
                    stop_loss_benchmark=_text(detail.get("stop_loss")),
                    key_positive_drivers=_list(detail.get("drivers")),
                    key_risk_factors=_list(detail.get("risks")),
                    disclaimer=DISCLAIMER,
                    created_at=now,
                )
            )
        found = self.get_owned(user_id, run_id)
        if found is None:
            raise RuntimeError("推演记录写入后读不回来")
        return StoredRun(
            id=found.id,
            symbol=found.symbol,
            as_of=found.as_of,
            status=found.status,
            rating=found.rating,
            decision=found.decision,
            nodes=found.nodes,
            events=result.events,
            bull=found.bull,
            bear=found.bear,
            config=found.config,
            detail=found.detail,
            created_at=found.created_at,
            duration_ms=found.duration_ms,
        )

    def get_owned(self, user_id: UUID, run_id: UUID) -> StoredRun | None:
        with Session(self.engine) as session:
            run = session.get(AgentSimulationRun, run_id)
            if run is None or run.user_id != user_id:
                return None
            decision = session.scalars(
                select(AgentSimulationDecision).where(AgentSimulationDecision.run_id == run_id)
            ).one()
            traces = session.scalars(
                select(AgentNodeTrace)
                .where(AgentNodeTrace.run_id == run_id)
                .order_by(AgentNodeTrace.seq)
            ).all()
            debates = session.scalars(
                select(AgentDebateRound)
                .where(AgentDebateRound.run_id == run_id)
                .order_by(AgentDebateRound.round_no)
            ).all()
            risks = session.scalars(
                select(AgentRiskAssessment)
                .where(AgentRiskAssessment.run_id == run_id)
                .order_by(AgentRiskAssessment.revision_no, AgentRiskAssessment.created_at)
            ).all()
            evaluation = session.scalars(
                select(AgentBacktestEvaluation)
                .where(AgentBacktestEvaluation.decision_id == decision.id)
                .order_by(AgentBacktestEvaluation.created_at.desc())
            ).first()
            detail = _detail(traces, debates, risks, decision, evaluation)
            first = debates[0] if debates else None
            return StoredRun(
                id=run.id,
                symbol=run.symbol,
                as_of=run.as_of,
                status=run.status,
                rating=DISPLAY_RATING.get(decision.simulated_rating, "REVIEW"),
                decision=decision.final_trade_decision,
                nodes=tuple(row.node_name for row in traces),
                events=(),
                bull="" if first is None else first.bull_claim,
                bear="" if first is None else first.bear_claim,
                config=dict(run.config_snapshot),
                detail=detail,
                created_at=run.created_at.isoformat(),
                duration_ms=run.duration_ms,
            )

    def list_owned(self, user_id: UUID, limit: int = 30) -> list[RunSummary]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(AgentSimulationRun, AgentSimulationDecision)
                .join(
                    AgentSimulationDecision,
                    AgentSimulationDecision.run_id == AgentSimulationRun.id,
                )
                .where(AgentSimulationRun.user_id == user_id)
                .order_by(AgentSimulationRun.created_at.desc())
                .limit(limit)
            ).all()
            found: list[RunSummary] = []
            for run, decision in rows:
                evaluation = session.scalars(
                    select(AgentBacktestEvaluation)
                    .where(AgentBacktestEvaluation.decision_id == decision.id)
                    .order_by(AgentBacktestEvaluation.created_at.desc())
                ).first()
                found.append(
                    RunSummary(
                        id=run.id,
                        symbol=run.symbol,
                        as_of=run.as_of,
                        status=run.status,
                        rating=DISPLAY_RATING.get(decision.simulated_rating, "REVIEW"),
                        confidence=_plain(decision.confidence_score),
                        engine=str(run.config_snapshot.get("engine", "tradingagents-mock")),
                        created_at=run.created_at.isoformat(),
                        excess_return=None
                        if evaluation is None
                        else _plain(evaluation.excess_return),
                        verdict=None if evaluation is None else evaluation.prediction_verdict,
                    )
                )
            return found

    def past_decisions(self, user_id: UUID, symbol: str, before: date) -> list[PastDecision]:
        """同一用户、同一标的、as_of 早于 before 的决策，越近越靠前。"""

        with Session(self.engine) as session:
            rows = session.execute(
                select(AgentSimulationRun, AgentSimulationDecision)
                .join(
                    AgentSimulationDecision,
                    AgentSimulationDecision.run_id == AgentSimulationRun.id,
                )
                .where(
                    AgentSimulationRun.user_id == user_id,
                    AgentSimulationRun.symbol == symbol,
                    AgentSimulationRun.as_of < before,
                    AgentSimulationDecision.simulated_rating != "review",
                )
                .order_by(AgentSimulationRun.as_of.desc(), AgentSimulationRun.created_at.desc())
                .limit(12)
            ).all()
            found: list[PastDecision] = []
            seen: set[date] = set()
            for run, decision in rows:
                if run.as_of in seen:
                    continue
                seen.add(run.as_of)
                holding = run.config_snapshot.get("holding_days", 30)
                found.append(
                    PastDecision(
                        decision_id=decision.id,
                        as_of=run.as_of,
                        rating=decision.simulated_rating,
                        holding_days=holding if isinstance(holding, int) else 30,
                        drivers=_texts(decision.key_positive_drivers),
                        risks=_texts(decision.key_risk_factors),
                    )
                )
            return found

    def record_evaluation(self, decision_id: UUID, evaluation: Evaluation) -> None:
        with Session(self.engine) as session, session.begin():
            session.add(
                AgentBacktestEvaluation(
                    id=uuid.uuid4(),
                    decision_id=decision_id,
                    evaluation_date=evaluation.entry_date,
                    note=evaluation.note,
                    actual_return=evaluation.actual,
                    quality_flag=evaluation.quality_flag,
                    holding_days=evaluation.holding_days,
                    benchmark_symbol=evaluation.benchmark_symbol,
                    benchmark_return=evaluation.benchmark,
                    excess_return=evaluation.excess,
                    prediction_verdict=evaluation.verdict,
                    reflection_notes=evaluation.reflection,
                    exit_date=evaluation.exit_date,
                    created_at=utc_now(),
                )
            )

    def settled(self, decision_id: UUID) -> Evaluation | None:
        """已经结算过且持有期走完的评估。结算结果只依赖固定窗口，可以复用。"""

        with Session(self.engine) as session:
            row = session.scalars(
                select(AgentBacktestEvaluation)
                .where(
                    AgentBacktestEvaluation.decision_id == decision_id,
                    AgentBacktestEvaluation.quality_flag == "measured",
                    AgentBacktestEvaluation.prediction_verdict.is_not(None),
                )
                .order_by(AgentBacktestEvaluation.created_at.desc())
            ).first()
            if row is None:
                return None
            return Evaluation(
                actual=row.actual_return,
                benchmark_symbol=row.benchmark_symbol,
                benchmark=row.benchmark_return,
                excess=row.excess_return,
                verdict=row.prediction_verdict,
                reflection=row.reflection_notes or "",
                holding_days=row.holding_days or 30,
                entry_date=row.evaluation_date,
                exit_date=row.exit_date,
                quality_flag=row.quality_flag,
                note=row.note,
            )

    def decision_id(self, user_id: UUID, run_id: UUID) -> tuple[UUID, str, int] | None:
        with Session(self.engine) as session:
            run = session.get(AgentSimulationRun, run_id)
            if run is None or run.user_id != user_id:
                return None
            decision = session.scalars(
                select(AgentSimulationDecision).where(AgentSimulationDecision.run_id == run_id)
            ).one()
            holding = run.config_snapshot.get("holding_days", 30)
            return (
                decision.id,
                decision.simulated_rating,
                holding if isinstance(holding, int) else 30,
            )

    def record_backtest(
        self,
        user_id: UUID,
        run_id: UUID,
        *,
        actual: Decimal | None,
        note: str,
        evaluation: Evaluation | None = None,
    ) -> str | None:
        found = self.decision_id(user_id, run_id)
        if found is None:
            return None
        decision_id = found[0]
        with Session(self.engine) as session:
            run = session.get(AgentSimulationRun, run_id)
            as_of = run.as_of if run is not None else utc_now().date()
        if evaluation is None:
            evaluation = Evaluation(
                actual=actual,
                benchmark_symbol=None,
                benchmark=None,
                excess=None,
                verdict=None,
                reflection="",
                holding_days=found[2],
                entry_date=as_of,
                exit_date=None,
                quality_flag="measured" if actual is not None else "insufficient",
                note=note,
            )
        self.record_evaluation(decision_id, evaluation)
        return evaluation.quality_flag


def _detail(
    traces: Sequence[AgentNodeTrace],
    debates: Sequence[AgentDebateRound],
    risks: Sequence[AgentRiskAssessment],
    decision: AgentSimulationDecision,
    evaluation: AgentBacktestEvaluation | None,
) -> dict[str, object]:
    analysts: list[dict[str, object]] = []
    proposals: list[dict[str, object]] = []
    memory: list[object] = []
    memo = ""
    subject: object = None
    aborted = False
    timeline: list[dict[str, object]] = []
    for row in traces:
        structured = row.structured_output or {}
        kind = structured.get("kind")
        timeline.append(
            {
                "seq": row.seq,
                "node": row.node_name,
                "role": row.role_name or "",
                "kind": kind or "",
                "content": row.output_content,
            }
        )
        if kind == "analyst":
            analysts.append({key: value for key, value in structured.items() if key != "kind"})
        elif kind == "proposal":
            proposals.append({key: value for key, value in structured.items() if key != "kind"})
        elif kind == "memo":
            memo = row.output_content
        elif kind == "init":
            subject = structured.get("subject")
        elif kind == "decision":
            raw_memory = structured.get("memory")
            memory = list(raw_memory) if isinstance(raw_memory, list) else []
            aborted = bool(structured.get("aborted"))
    return {
        "subject": subject,
        "analysts": analysts,
        "debates": [
            {
                "round": row.round_no,
                "bull": row.bull_claim,
                "bear": row.bear_claim,
                "bull_citations": row.bull_citations or [],
                "bear_citations": row.bear_citations or [],
                "arbitration": row.lead_arbitration or "",
                "unsupported": row.unsupported_claims or [],
                "converged": row.converged_flag,
            }
            for row in debates
        ],
        "memo": memo,
        "proposals": proposals,
        "risks": [
            {
                "profile": row.risk_profile,
                "approved": row.approved,
                "content": row.content,
                "veto_reason": row.veto_reason,
                "scenario": row.stress_test_scenario,
                "drawdown": _plain(row.estimated_max_drawdown),
                "mitigations": row.suggested_mitigations or [],
                "revision": row.revision_no,
            }
            for row in risks
        ],
        "decision": {
            "confidence": _plain(decision.confidence_score),
            "horizon": decision.target_horizon,
            "allocation": decision.suggested_allocation_range,
            "stop_loss": decision.stop_loss_benchmark,
            "drivers": decision.key_positive_drivers or [],
            "risks": decision.key_risk_factors or [],
            "aborted": aborted,
        },
        "memory": memory,
        "evaluation": None
        if evaluation is None
        else {
            "quality_flag": evaluation.quality_flag,
            "actual_return": _plain(evaluation.actual_return),
            "benchmark_symbol": evaluation.benchmark_symbol,
            "benchmark_return": _plain(evaluation.benchmark_return),
            "excess_return": _plain(evaluation.excess_return),
            "verdict": evaluation.prediction_verdict,
            "reflection": evaluation.reflection_notes,
            "holding_days": evaluation.holding_days,
            "exit_date": None if evaluation.exit_date is None else evaluation.exit_date.isoformat(),
            "note": evaluation.note,
        },
        "timeline": timeline,
    }


def _rating_key(rating: str) -> str:
    key = rating.strip().lower()
    return key if key in DISPLAY_RATING else "review"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _list(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item.get("text", "")) for item in value if isinstance(item, dict))


def _plain(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
