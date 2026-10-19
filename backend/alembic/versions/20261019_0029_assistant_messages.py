"""助手消息：渲染块、来源、过程、模型与校验结果（规格 §10，第 1a 期）。

全部可空：旧消息这些列为 NULL，前端按纯文本显示。
第 1a 期只写 finished_at，其余列留给后续期使用。

Revision ID: 20261019_0029
Revises: 20261018_0028
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261019_0029"
down_revision: str | None = "20261018_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("blocks", postgresql.JSONB(), nullable=True))
    op.add_column("chat_messages", sa.Column("sources", postgresql.JSONB(), nullable=True))
    op.add_column("chat_messages", sa.Column("steps", postgresql.JSONB(), nullable=True))
    op.add_column("chat_messages", sa.Column("as_of", sa.Date(), nullable=True))
    op.add_column("chat_messages", sa.Column("model", sa.String(length=80), nullable=True))
    # full / partial / none
    op.add_column("chat_messages", sa.Column("grounding", sa.String(length=8), nullable=True))
    op.add_column("chat_messages", sa.Column("tool_trace", postgresql.JSONB(), nullable=True))
    op.add_column(
        "chat_messages", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True)
    )
    # 每日额度按「当天助手消息数」统计，只看助手那一行。
    op.create_index(
        "ix_chat_messages_assistant_created",
        "chat_messages",
        ["created_at"],
        postgresql_where=sa.text("role = 'assistant'"),
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_assistant_created", table_name="chat_messages")
    for column in (
        "finished_at",
        "tool_trace",
        "grounding",
        "model",
        "as_of",
        "steps",
        "sources",
        "blocks",
    ):
        op.drop_column("chat_messages", column)
