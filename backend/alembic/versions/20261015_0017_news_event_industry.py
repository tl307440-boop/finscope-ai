"""industry tag for news events

Revision ID: 20261015_0017
Revises: 20261015_0016
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0017"
down_revision: str | None = "20261015_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "news_events",
        sa.Column("industry_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_news_events_industry_id",
        "news_events",
        "industries",
        ["industry_id"],
        ["id"],
    )
    op.create_index("ix_news_events_industry_id", "news_events", ["industry_id"])


def downgrade() -> None:
    op.drop_index("ix_news_events_industry_id", table_name="news_events")
    op.drop_constraint("fk_news_events_industry_id", "news_events", type_="foreignkey")
    op.drop_column("news_events", "industry_id")
