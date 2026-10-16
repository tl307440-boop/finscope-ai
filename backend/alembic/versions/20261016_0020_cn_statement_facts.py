"""A 股三大报表科目表

东方财富 F10 的利润表、资产负债表、现金流量表映射成与 SEC companyfacts 同形的事实，
filed 取公告日，供同比、比率、规则读数和研究对比直接复用。

Revision ID: 20261016_0020
Revises: 20261016_0019
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261016_0020"
down_revision: str | None = "20261016_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cn_statement_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("concept", sa.String(160), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("form", sa.String(16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(8), nullable=False),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("source_field", sa.String(80), nullable=False),
        sa.Column("source_url", sa.String(300), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "instrument_id", "concept", "period_end", name="uq_cn_statement_facts_identity"
        ),
    )
    op.create_index(
        "ix_cn_statement_facts_instrument_filed", "cn_statement_facts", ["instrument_id", "filed"]
    )


def downgrade() -> None:
    op.drop_index("ix_cn_statement_facts_instrument_filed", table_name="cn_statement_facts")
    op.drop_table("cn_statement_facts")
