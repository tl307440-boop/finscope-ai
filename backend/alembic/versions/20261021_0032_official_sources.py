"""官方文本来源：美联储三份 RSS，国家统计局数据发布。

Revision ID: 20261021_0032
Revises: 20261020_0031
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261021_0032"
down_revision: str | None = "20261020_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO corpus_sources (
            id, code, publisher, kind, method, allowed_hosts, frequency,
            store_full_text, retention_days, terms_note, enabled
        ) VALUES
        (
            '00000000-0000-4000-8000-00000000c0a4', 'fed_speeches', '美联储', 'central_bank',
            'rss', ARRAY['www.federalreserve.gov'], 'hourly', true, NULL,
            '官方讲话 RSS 与正文页。', true
        ),
        (
            '00000000-0000-4000-8000-00000000c0a5', 'fed_press', '美联储', 'central_bank',
            'rss', ARRAY['www.federalreserve.gov'], 'hourly', true, NULL,
            '新闻稿 RSS，含 FOMC 声明和纪要。', true
        ),
        (
            '00000000-0000-4000-8000-00000000c0a6', 'fed_testimony', '美联储', 'central_bank',
            'rss', ARRAY['www.federalreserve.gov'], 'hourly', true, NULL,
            '国会证词 RSS。', true
        ),
        (
            '00000000-0000-4000-8000-00000000c0a7', 'nbs_release', '国家统计局', 'statistics',
            'html', ARRAY['www.stats.gov.cn'], 'hourly', true, NULL,
            '数据发布栏目第一页。', true
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM corpus_documents WHERE source_id IN (
            SELECT id FROM corpus_sources
            WHERE code IN ('fed_speeches', 'fed_press', 'fed_testimony', 'nbs_release')
        )
        """
    )
    op.execute(
        """
        DELETE FROM corpus_sources
        WHERE code IN ('fed_speeches', 'fed_press', 'fed_testimony', 'nbs_release')
        """
    )
