"""nullable bar amount for sources without turnover

腾讯行情只提供成交量，不提供成交额。要让第二个真实行情源能入库，
amount 必须可空——缺就留空，界面显示「—」，不用 量×价 之类的办法倒推。

Revision ID: 20261015_0018
Revises: 20261015_0017
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0018"
down_revision: str | None = "20261015_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("market_bars", "amount", existing_type=sa.Numeric(24, 6), nullable=True)


def downgrade() -> None:
    # 回滚前先把缺失值补成 0，否则 NOT NULL 加不回去。
    op.execute("UPDATE market_bars SET amount = 0 WHERE amount IS NULL")
    op.alter_column("market_bars", "amount", existing_type=sa.Numeric(24, 6), nullable=False)
