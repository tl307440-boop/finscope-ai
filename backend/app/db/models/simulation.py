import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentSimulationRun(Base):
    __tablename__ = "agent_simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_node: Mapped[str | None] = mapped_column(String(64))
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentNodeTrace(Base):
    __tablename__ = "agent_node_traces"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_node_traces_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role_name: Mapped[str | None] = mapped_column(String(64))
    output_content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_output: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    citations: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentDebateRound(Base):
    __tablename__ = "agent_debate_rounds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    bull_claim: Mapped[str] = mapped_column(Text, nullable=False)
    bear_claim: Mapped[str] = mapped_column(Text, nullable=False)
    bull_citations: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    bear_citations: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    lead_arbitration: Mapped[str | None] = mapped_column(Text)
    unsupported_claims: Mapped[list[str] | None] = mapped_column(JSONB)
    converged_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRiskAssessment(Base):
    __tablename__ = "agent_risk_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    risk_profile: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    approved: Mapped[bool | None] = mapped_column(Boolean)
    veto_reason: Mapped[str | None] = mapped_column(Text)
    stress_test_scenario: Mapped[str | None] = mapped_column(String(64))
    estimated_max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    suggested_mitigations: Mapped[list[str] | None] = mapped_column(JSONB)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentSimulationDecision(Base):
    __tablename__ = "agent_simulation_decisions"
    __table_args__ = (UniqueConstraint("run_id", name="uq_agent_decisions_run"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_simulation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    simulated_rating: Mapped[str] = mapped_column(String(24), nullable=False)
    final_trade_decision: Mapped[str] = mapped_column(Text, nullable=False)
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    target_horizon: Mapped[str | None] = mapped_column(String(32))
    suggested_allocation_range: Mapped[str | None] = mapped_column(String(32))
    stop_loss_benchmark: Mapped[str | None] = mapped_column(Text)
    key_positive_drivers: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    key_risk_factors: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentBacktestEvaluation(Base):
    __tablename__ = "agent_backtest_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_simulation_decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluation_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    actual_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    quality_flag: Mapped[str] = mapped_column(String(32), nullable=False)
    holding_days: Mapped[int | None] = mapped_column(Integer)
    benchmark_symbol: Mapped[str | None] = mapped_column(String(32))
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    excess_return: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    prediction_verdict: Mapped[str | None] = mapped_column(String(24))
    reflection_notes: Mapped[str | None] = mapped_column(Text)
    exit_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
