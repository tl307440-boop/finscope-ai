"""sec filings and facts

Revision ID: 20261015_0006
Revises: 20261015_0005
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0006"
down_revision: str | None = "20261015_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cik", sa.String(length=10), nullable=False),
        sa.Column("filing_type", sa.String(length=16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=8), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("accession_no", sa.String(length=24), nullable=False),
        sa.Column("source_url", sa.String(length=300), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint("accession_no", name="uq_filings_accession"),
    )
    op.create_index("ix_filings_cik_filed", "filings", ["cik", "filed"])
    op.create_table(
        "sec_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filing_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("concept", sa.String(length=160), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=8), nullable=False),
        sa.Column("frame", sa.String(length=16), nullable=True),
        sa.Column("filed", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"], ondelete="CASCADE"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_sec_facts_identity
        ON sec_facts (
            filing_id,
            concept,
            unit,
            period_end,
            COALESCE(period_start, DATE '0001-01-01')
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sec_facts_identity")
    op.drop_table("sec_facts")
    op.drop_index("ix_filings_cik_filed", table_name="filings")
    op.drop_table("filings")
