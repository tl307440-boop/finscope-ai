"""东财板块收盘快照与板块日线。

Revision ID: 20261017_0023
Revises: 20261016_0022
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261017_0023"
down_revision: str | None = "20261016_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _numeric(name: str, precision: int = 20) -> sa.Column[object]:
    return sa.Column(name, sa.Numeric(precision, 6), nullable=True)


def upgrade() -> None:
    op.create_table(
        "board_daily_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("board_code", sa.String(8), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        _numeric("last"),
        _numeric("change_pct"),
        _numeric("change_5d_pct"),
        _numeric("change_10d_pct"),
        _numeric("change_60d_pct"),
        _numeric("change_ytd_pct"),
        _numeric("amount", 24),
        _numeric("turnover_pct"),
        _numeric("pe_ttm"),
        _numeric("market_cap", 24),
        _numeric("main_net_inflow", 24),
        _numeric("main_net_inflow_5d", 24),
        sa.Column("advancers", sa.Integer(), nullable=True),
        sa.Column("decliners", sa.Integer(), nullable=True),
        sa.Column("unchanged", sa.Integer(), nullable=True),
        sa.Column("leader_code", sa.String(16), nullable=True),
        sa.Column("leader_name", sa.String(64), nullable=True),
        _numeric("leader_change_pct"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("board_code", "trade_date", name="uq_board_daily_snapshots_identity"),
    )
    op.create_index(
        "ix_board_daily_snapshots_date_level",
        "board_daily_snapshots",
        ["trade_date", "kind", "level"],
    )
    op.create_table(
        "board_daily_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("secid", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(20, 6), nullable=False),
        sa.Column("high", sa.Numeric(20, 6), nullable=False),
        sa.Column("low", sa.Numeric(20, 6), nullable=False),
        sa.Column("close", sa.Numeric(20, 6), nullable=False),
        sa.Column("volume", sa.Numeric(24, 6), nullable=False),
        sa.Column("amount", sa.Numeric(24, 6), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("secid", "trade_date", name="uq_board_daily_bars_identity"),
    )


def downgrade() -> None:
    op.drop_table("board_daily_bars")
    op.drop_index("ix_board_daily_snapshots_date_level", table_name="board_daily_snapshots")
    op.drop_table("board_daily_snapshots")
