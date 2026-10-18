"""自定义数据分析：期权认购认沽统计与估值序列。

Revision ID: 20261018_0028
Revises: 20261018_0027
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261018_0028"
down_revision: str | None = "20261018_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "option_daily_stats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("underlying_code", sa.String(16), nullable=False),
        sa.Column("underlying_name", sa.String(64), nullable=True),
        sa.Column("call_volume", sa.BigInteger(), nullable=False),
        sa.Column("put_volume", sa.BigInteger(), nullable=False),
        sa.Column("call_oi", sa.BigInteger(), nullable=False),
        sa.Column("put_oi", sa.BigInteger(), nullable=False),
        sa.Column("reported_ratio", sa.Numeric(14, 4), nullable=True),
        sa.Column("reported_ratio_kind", sa.String(16), nullable=True),
        sa.Column("quality_flag", sa.String(8), nullable=False),
        sa.Column("quality_note", sa.String(255), nullable=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "exchange", "underlying_code", "trade_date", name="uq_option_daily_stats_identity"
        ),
    )
    op.create_index("ix_option_daily_stats_trade_date", "option_daily_stats", ["trade_date"])
    op.create_table(
        "custom_series_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series_code", sa.String(40), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(28, 8), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("quality_flag", sa.String(8), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("series_code", "trade_date", name="uq_custom_series_points_identity"),
    )
    op.create_index(
        "ix_custom_series_points_series_date",
        "custom_series_points",
        ["series_code", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_series_points_series_date", table_name="custom_series_points")
    op.drop_table("custom_series_points")
    op.drop_index("ix_option_daily_stats_trade_date", table_name="option_daily_stats")
    op.drop_table("option_daily_stats")
