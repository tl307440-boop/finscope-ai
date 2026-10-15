"""add cik to instruments

Revision ID: 20261015_0004
Revises: 20261015_0003
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0004"
down_revision: str | None = "20261015_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("instruments", sa.Column("cik", sa.String(length=10), nullable=True))
    op.create_index(
        "uq_instruments_cik",
        "instruments",
        ["cik"],
        unique=True,
        postgresql_where=sa.text("cik IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_instruments_cik", table_name="instruments")
    op.drop_column("instruments", "cik")
