import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.db.models.simulation import (
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
    ) -> StoredRun:
        now = utc_now()
        run_id = uuid.uuid4()
        with Session(self.engine) as session, session.begin():
            session.add(
                AgentSimulationRun(
                    id=run_id,
                    user_id=user_id,
                    symbol=symbol,
                    as_of=as_of,
                    status="succeeded",
                    current_node="Portfolio Manager",
                    config_snapshot={"max_debate_rounds": rounds, "orders": False},
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
                        output_content=trace.content,
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
                        approved=None,
                        created_at=now,
                    )
                )
            session.add(
                AgentSimulationDecision(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    simulated_rating=result.rating.lower(),
                    final_trade_decision=result.decision,
                    disclaimer=DISCLAIMER,
                    created_at=now,
                )
            )
        return StoredRun(
            id=run_id,
            symbol=symbol,
            as_of=as_of,
            status="succeeded",
            rating=result.rating,
            decision=result.decision,
            nodes=tuple(trace.node_name for trace in result.traces),
            events=result.events,
            bull=result.debates[0].bull_claim if result.debates else "",
            bear=result.debates[0].bear_claim if result.debates else "",
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
            debate = session.scalars(
                select(AgentDebateRound)
                .where(AgentDebateRound.run_id == run_id)
                .order_by(AgentDebateRound.round_no)
            ).first()
            return StoredRun(
                id=run.id,
                symbol=run.symbol,
                as_of=run.as_of,
                status=run.status,
                rating=DISPLAY_RATING.get(decision.simulated_rating, "REVIEW"),
                decision=decision.final_trade_decision,
                nodes=tuple(row.node_name for row in traces),
                events=(),
                bull="" if debate is None else debate.bull_claim,
                bear="" if debate is None else debate.bear_claim,
            )

    def record_backtest(
        self,
        user_id: UUID,
        run_id: UUID,
        *,
        actual: Decimal | None,
        note: str,
    ) -> str | None:
        with Session(self.engine) as session, session.begin():
            run = session.get(AgentSimulationRun, run_id)
            if run is None or run.user_id != user_id:
                return None
            decision = session.scalars(
                select(AgentSimulationDecision).where(AgentSimulationDecision.run_id == run_id)
            ).one()
            flag = "measured" if actual is not None else "insufficient"
            session.execute(
                text(
                    """
                    INSERT INTO agent_backtest_evaluations (
                        id, decision_id, evaluation_date, note, created_at,
                        actual_return, quality_flag
                    ) VALUES (
                        :id, :decision_id, :evaluation_date, :note, :created_at,
                        :actual_return, :quality_flag
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "decision_id": decision.id,
                    "evaluation_date": run.as_of,
                    "note": note,
                    "created_at": utc_now(),
                    "actual_return": actual,
                    "quality_flag": flag,
                },
            )
            return flag
