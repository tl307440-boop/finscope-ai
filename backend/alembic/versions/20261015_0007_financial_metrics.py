"""financial metrics

Revision ID: 20261015_0007
Revises: 20261015_0006
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0007"
down_revision: str | None = "20261015_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_metrics",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_code", sa.String(length=64), nullable=False),
        sa.Column("period_type", sa.String(length=8), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("calculation_method", sa.String(length=16), nullable=False),
        sa.Column("source_facts", postgresql.JSONB(), nullable=False),
        sa.Column("metric_version", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quality_flag", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint(
            "instrument_id",
            "metric_code",
            "period_type",
            "period_end",
            "metric_version",
            name="uq_financial_metrics_identity",
        ),
    )


def downgrade() -> None:
    op.drop_table("financial_metrics")
