"""eastmoney account binding: encrypted session per user, group/item sync markers

Revision ID: 20261018_0027
Revises: 20261018_0026
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261018_0027"
down_revision: str | None = "20261018_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eastmoney_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("em_uid", sa.String(length=32), nullable=False),
        sa.Column("em_alias", sa.String(length=64), nullable=False),
        # 登录 cookie 用 SecretBox 加密后存放；明文不落库、不进日志。
        sa.Column("cookies_enc", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_message", sa.String(length=200), nullable=True),
        sa.Column("last_sync_groups", sa.Integer(), nullable=True),
        sa.Column("last_sync_items", sa.Integer(), nullable=True),
    )
    # 东财分组 id：改名后仍能对上同一个分组。
    op.add_column("watchlist_groups", sa.Column("em_gid", sa.String(length=32), nullable=True))
    op.create_index(
        "uq_watchlist_groups_owner_em_gid",
        "watchlist_groups",
        ["user_id", "em_gid"],
        unique=True,
        postgresql_where=sa.text("em_gid IS NOT NULL"),
    )
    # manual：用户自己加的；eastmoney：同步来的。同步只会移除后者，不动用户手动加的。
    op.add_column(
        "watchlist_items",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("watchlist_items", "source")
    op.drop_index("uq_watchlist_groups_owner_em_gid", table_name="watchlist_groups")
    op.drop_column("watchlist_groups", "em_gid")
    op.drop_table("eastmoney_bindings")
