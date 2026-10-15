"""nullable realized returns for simulations

Revision ID: 20261015_0016
Revises: 20261015_0015
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0016"
down_revision: str | None = "20261015_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_backtest_evaluations",
        sa.Column("actual_return", sa.Numeric(18, 8), nullable=True),
    )
    op.add_column(
        "agent_backtest_evaluations",
        sa.Column(
            "quality_flag", sa.String(length=32), nullable=False, server_default="insufficient"
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_backtest_evaluations", "quality_flag")
    op.drop_column("agent_backtest_evaluations", "actual_return")
