"""industry taxonomy and falsifiable theses

Revision ID: 20261015_0013
Revises: 20261015_0012
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0013"
down_revision: str | None = "20261015_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("taxonomy_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["industries.id"]),
        sa.UniqueConstraint("code", name="uq_industries_code"),
    )
    op.create_table(
        "industry_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("industry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["industry_id"], ["industries.id"]),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint(
            "industry_id",
            "instrument_id",
            "valid_from",
            name="uq_industry_memberships_window",
        ),
    )
    op.create_table(
        "industry_theses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("industry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("hypothesis", sa.String(length=400), nullable=False),
        sa.Column("support_condition", postgresql.JSONB(), nullable=False),
        sa.Column("challenge_condition", postgresql.JSONB(), nullable=False),
        sa.Column("observation_window_days", sa.Integer(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["industry_id"], ["industries.id"]),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_table(
        "thesis_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thesis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stance", sa.String(length=16), nullable=False),
        sa.Column("quote_text", sa.String(length=400), nullable=False),
        sa.Column("published_at", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["fact_id"], ["sec_facts.id"]),
        sa.ForeignKeyConstraint(["thesis_id"], ["industry_theses.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "industry_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("thesis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("rationale", sa.String(length=400), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thesis_id"], ["industry_theses.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("industry_assessments")
    op.drop_table("thesis_evidence")
    op.drop_table("industry_theses")
    op.drop_table("industry_memberships")
    op.drop_table("industries")
