"""公告来源：巨潮与 SEC（知识库设计 §5.1）。

只登记来源和允许访问的主机。正文由 filing_fetch 和定时任务写入 corpus_documents。

Revision ID: 20261020_0031
Revises: 20261020_0030
Create Date: 2026-10-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261020_0031"
down_revision: str | None = "20261020_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CNINFO_SOURCE = "00000000-0000-4000-8000-00000000c0a2"
SEC_SOURCE = "00000000-0000-4000-8000-00000000c0a3"


def upgrade() -> None:
    op.execute(
        f"""
        INSERT INTO corpus_sources (
            id, code, publisher, kind, method, allowed_hosts, frequency,
            store_full_text, retention_days, terms_note, enabled
        ) VALUES
        (
            '{CNINFO_SOURCE}', 'cninfo', '巨潮资讯', 'exchange', 'http',
            ARRAY['www.cninfo.com.cn', 'static.cninfo.com.cn'], 'on_demand',
            true, NULL, '只下载公告 PDF 正文，每主机每秒最多 1 次。', true
        ),
        (
            '{SEC_SOURCE}', 'sec_edgar', '美国证券交易委员会', 'exchange', 'http',
            ARRAY['www.sec.gov', 'data.sec.gov'], 'on_demand',
            true, NULL, '只下载 8-K 正文，遵守 SEC 每秒 10 次以下的访问规则。', true
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM corpus_documents WHERE source_id IN (
            SELECT id FROM corpus_sources WHERE code IN ('cninfo', 'sec_edgar')
        )
        """
    )
    op.execute("DELETE FROM corpus_sources WHERE code IN ('cninfo', 'sec_edgar')")
