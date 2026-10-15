"""deduplicated disclosure events

Revision ID: 20261015_0014
Revises: 20261015_0013
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0014"
down_revision: str | None = "20261015_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("canonical_url", sa.String(length=300), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("usage_rights", sa.String(length=120), nullable=False),
        sa.Column("quality_flag", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("dedupe_hash", name="uq_news_events_dedupe"),
    )


def downgrade() -> None:
    op.drop_table("news_events")
