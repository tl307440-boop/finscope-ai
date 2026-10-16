"""宏观状态判断快照表。

Revision ID: 20261016_0022
Revises: 20261016_0021
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261016_0022"
down_revision: str | None = "20261016_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macro_regime_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("model_version", sa.String(32), nullable=False),
        sa.Column("rule_version", sa.String(32), nullable=False),
        sa.Column("regime_code", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("as_of", "model_version", name="uq_macro_regime_snapshots_identity"),
    )
    op.create_index("ix_macro_regime_snapshots_as_of", "macro_regime_snapshots", ["as_of"])


def downgrade() -> None:
    op.drop_index("ix_macro_regime_snapshots_as_of", table_name="macro_regime_snapshots")
    op.drop_table("macro_regime_snapshots")
