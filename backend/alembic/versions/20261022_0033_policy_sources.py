"""政策类官方文本来源：深交所本所要闻、上交所监管动态。

两个来源的列表页都是静态 HTML，抓取前读 robots.txt（2026-10-21 核对：深交所 robots.txt 为空文件，
上交所 robots.txt 返回 404，都不限制）。国务院、证监会、工信部、发改委没有接入，原因见实现日志。

Revision ID: 20261022_0033
Revises: 20261021_0032
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261022_0033"
down_revision: str | None = "20261021_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO corpus_sources (
            id, code, publisher, kind, method, allowed_hosts, frequency,
            store_full_text, retention_days, robots_checked_at, terms_note, enabled
        ) VALUES
        (
            '00000000-0000-4000-8000-00000000c0a8', 'szse_news', '深圳证券交易所', 'exchange',
            'html', ARRAY['www.szse.cn'], 'hourly', true, NULL, DATE '2026-10-21',
            '官方公开文本，仅作研究引用，保留原文链接。本所要闻栏目第一页，抓取前读 robots.txt。',
            true
        ),
        (
            '00000000-0000-4000-8000-00000000c0a9', 'sse_supervision', '上海证券交易所',
            'exchange', 'html', ARRAY['www.sse.com.cn'], 'hourly', true, NULL,
            DATE '2026-10-21',
            '官方公开文本，仅作研究引用，保留原文链接。监管动态栏目第一页，抓取前读 robots.txt。',
            true
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM corpus_documents WHERE source_id IN (
            SELECT id FROM corpus_sources WHERE code IN ('szse_news', 'sse_supervision')
        )
        """
    )
    op.execute("DELETE FROM corpus_sources WHERE code IN ('szse_news', 'sse_supervision')")
