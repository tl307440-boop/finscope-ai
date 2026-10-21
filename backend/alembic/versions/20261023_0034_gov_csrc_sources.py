"""政策类官方文本来源：国务院「最新政策」、证监会要闻。

2026-10-21 核对：中国政府网 robots.txt 允许 `/zhengce/`，「最新政策」页的列表来自同目录的静态 JSON；
证监会没有 robots.txt（请求跳转到首页），按 RFC 9309 视为不限制，要闻栏目的列表页是旧版冻结页，
所以读首页上的要闻链接，发布时间取正文页的 `PubDate`。工信部（文件库靠 JS 渲染）、
发改委（robots.txt 返回 403）没有接入。

Revision ID: 20261023_0034
Revises: 20261022_0033
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0034"
down_revision: str | None = "20261022_0033"
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
            '00000000-0000-4000-8000-00000000c0aa', 'gov_policy', '中国政府网', 'government',
            'json', ARRAY['www.gov.cn'], 'hourly', true, NULL, DATE '2026-10-21',
            '官方公开文本，仅作研究引用，保留原文链接。'
            '「最新政策」列表 JSON 的最新 50 条，抓取前读 robots.txt。',
            true
        ),
        (
            '00000000-0000-4000-8000-00000000c0ab', 'csrc_news', '中国证监会', 'regulator',
            'html', ARRAY['www.csrc.gov.cn'], 'hourly', true, NULL, DATE '2026-10-21',
            '官方公开文本，仅作研究引用，保留原文链接。'
            '首页上的证监会要闻链接，抓取前读 robots.txt。',
            true
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM corpus_documents WHERE source_id IN (
            SELECT id FROM corpus_sources WHERE code IN ('gov_policy', 'csrc_news')
        )
        """
    )
    op.execute("DELETE FROM corpus_sources WHERE code IN ('gov_policy', 'csrc_news')")
