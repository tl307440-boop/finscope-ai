"""午盘成交额快照表 ``market_noon_amounts``。

北证市场没有「11:30 成交额」的历史接口，只能由调度器在 11:30 收盘后、13:00 开盘前抓一次
当日累计值落库；全市场午盘 = 沪 + 深 + 京 才能算出来。按 ``(trade_date, market)`` 唯一，
同一交易日的补跑只会覆盖不会重复。

Revision ID: 20261119_0036
Revises: 20261024_0035
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261119_0036"
down_revision: str | None = "20261024_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "market_noon_amounts"
IDENTITY = "uq_market_noon_amounts_identity"
MARKET_DAY_INDEX = "ix_market_noon_amounts_market_trade_date"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("market", sa.String(length=8), nullable=False),
        sa.Column("amount_yuan", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("trade_date", "market", name=IDENTITY),
    )
    op.create_index(MARKET_DAY_INDEX, TABLE, ["market", "trade_date"])


def downgrade() -> None:
    op.drop_index(MARKET_DAY_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
