"""agent simulation tables

Revision ID: 20261015_0015
Revises: 20261015_0014
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0015"
down_revision: str | None = "20261015_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_simulation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("current_node", sa.String(length=64), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_table(
        "agent_node_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("node_name", sa.String(length=64), nullable=False),
        sa.Column("output_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_simulation_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "seq", name="uq_agent_node_traces_seq"),
    )
    op.create_table(
        "agent_debate_rounds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("bull_claim", sa.Text(), nullable=False),
        sa.Column("bear_claim", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_simulation_runs.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "agent_risk_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("risk_profile", sa.String(length=24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_simulation_runs.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "agent_simulation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulated_rating", sa.String(length=24), nullable=False),
        sa.Column("final_trade_decision", sa.Text(), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "simulated_rating in ('buy','overweight','hold','underweight','sell','review')",
            name="ck_agent_decisions_rating",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_simulation_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", name="uq_agent_decisions_run"),
    )
    op.create_table(
        "agent_backtest_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluation_date", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["agent_simulation_decisions.id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_backtest_evaluations")
    op.drop_table("agent_simulation_decisions")
    op.drop_table("agent_risk_assessments")
    op.drop_table("agent_debate_rounds")
    op.drop_table("agent_node_traces")
    op.drop_table("agent_simulation_runs")
