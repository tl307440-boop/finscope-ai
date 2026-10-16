"""agent simulation structured outputs (spec §20.5)

Revision ID: 20261016_0019
Revises: 20261015_0018
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261016_0019"
down_revision: str | None = "20261015_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = postgresql.JSONB()


def upgrade() -> None:
    op.add_column("agent_simulation_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.create_index(
        "idx_agent_sim_runs_user_symbol",
        "agent_simulation_runs",
        ["user_id", "symbol", "as_of"],
    )

    op.add_column("agent_node_traces", sa.Column("role_name", sa.String(64), nullable=True))
    op.add_column("agent_node_traces", sa.Column("structured_output", _JSON, nullable=True))
    op.add_column("agent_node_traces", sa.Column("citations", _JSON, nullable=True))

    op.add_column("agent_debate_rounds", sa.Column("bull_citations", _JSON, nullable=True))
    op.add_column("agent_debate_rounds", sa.Column("bear_citations", _JSON, nullable=True))
    op.add_column("agent_debate_rounds", sa.Column("lead_arbitration", sa.Text(), nullable=True))
    op.add_column("agent_debate_rounds", sa.Column("unsupported_claims", _JSON, nullable=True))
    op.add_column(
        "agent_debate_rounds",
        sa.Column("converged_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column("agent_risk_assessments", sa.Column("veto_reason", sa.Text(), nullable=True))
    op.add_column(
        "agent_risk_assessments", sa.Column("stress_test_scenario", sa.String(64), nullable=True)
    )
    op.add_column(
        "agent_risk_assessments",
        sa.Column("estimated_max_drawdown", sa.Numeric(10, 4), nullable=True),
    )
    op.add_column(
        "agent_risk_assessments", sa.Column("suggested_mitigations", _JSON, nullable=True)
    )
    op.add_column(
        "agent_risk_assessments",
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="0"),
    )

    op.add_column(
        "agent_simulation_decisions", sa.Column("confidence_score", sa.Numeric(5, 2), nullable=True)
    )
    op.add_column(
        "agent_simulation_decisions", sa.Column("target_horizon", sa.String(32), nullable=True)
    )
    op.add_column(
        "agent_simulation_decisions",
        sa.Column("suggested_allocation_range", sa.String(32), nullable=True),
    )
    op.add_column(
        "agent_simulation_decisions", sa.Column("stop_loss_benchmark", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_simulation_decisions", sa.Column("key_positive_drivers", _JSON, nullable=True)
    )
    op.add_column("agent_simulation_decisions", sa.Column("key_risk_factors", _JSON, nullable=True))

    op.add_column(
        "agent_backtest_evaluations", sa.Column("holding_days", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_backtest_evaluations",
        sa.Column("benchmark_symbol", sa.String(32), nullable=True),
    )
    op.add_column(
        "agent_backtest_evaluations",
        sa.Column("benchmark_return", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "agent_backtest_evaluations", sa.Column("excess_return", sa.Numeric(18, 8), nullable=True)
    )
    op.add_column(
        "agent_backtest_evaluations",
        sa.Column("prediction_verdict", sa.String(24), nullable=True),
    )
    op.add_column(
        "agent_backtest_evaluations", sa.Column("reflection_notes", sa.Text(), nullable=True)
    )
    op.add_column("agent_backtest_evaluations", sa.Column("exit_date", sa.Date(), nullable=True))


def downgrade() -> None:
    for column in (
        "exit_date",
        "reflection_notes",
        "prediction_verdict",
        "excess_return",
        "benchmark_return",
        "benchmark_symbol",
        "holding_days",
    ):
        op.drop_column("agent_backtest_evaluations", column)
    for column in (
        "key_risk_factors",
        "key_positive_drivers",
        "stop_loss_benchmark",
        "suggested_allocation_range",
        "target_horizon",
        "confidence_score",
    ):
        op.drop_column("agent_simulation_decisions", column)
    for column in (
        "revision_no",
        "suggested_mitigations",
        "estimated_max_drawdown",
        "stress_test_scenario",
        "veto_reason",
    ):
        op.drop_column("agent_risk_assessments", column)
    for column in (
        "converged_flag",
        "unsupported_claims",
        "lead_arbitration",
        "bear_citations",
        "bull_citations",
    ):
        op.drop_column("agent_debate_rounds", column)
    for column in ("citations", "structured_output", "role_name"):
        op.drop_column("agent_node_traces", column)
    op.drop_index("idx_agent_sim_runs_user_symbol", table_name="agent_simulation_runs")
    op.drop_column("agent_simulation_runs", "duration_ms")
