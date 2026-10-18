"""watchlist items may point at an external security (board, index, US stock, ETF ...)

Revision ID: 20261018_0026
Revises: 20261017_0025
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_0026"
down_revision: str | None = "20261017_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("watchlist_items", "instrument_id", existing_nullable=False, nullable=True)
    op.add_column("watchlist_items", sa.Column("ext_secid", sa.String(length=32), nullable=True))
    op.add_column("watchlist_items", sa.Column("ext_name", sa.String(length=128), nullable=True))
    # 每一行恰好指向一个对象：要么是本系统证券，要么是按东财 secid 保存的外部标的。
    op.create_check_constraint(
        "ck_watchlist_items_target",
        "watchlist_items",
        "num_nonnulls(instrument_id, ext_secid) = 1",
    )
    op.create_index(
        "uq_watchlist_items_group_secid",
        "watchlist_items",
        ["group_id", "ext_secid"],
        unique=True,
        postgresql_where=sa.text("ext_secid IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM watchlist_items WHERE instrument_id IS NULL")
    op.drop_index("uq_watchlist_items_group_secid", table_name="watchlist_items")
    op.drop_constraint("ck_watchlist_items_target", "watchlist_items", type_="check")
    op.drop_column("watchlist_items", "ext_name")
    op.drop_column("watchlist_items", "ext_secid")
    op.alter_column("watchlist_items", "instrument_id", existing_nullable=True, nullable=False)
