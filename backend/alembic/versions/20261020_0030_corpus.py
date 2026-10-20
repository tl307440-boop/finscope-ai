"""语料库：来源、文档（含版本）、块（全文索引）、抓取记录（规格 §10，知识库设计 §4）。

四类文档共用这几张表，用 `corpus_documents.kind` 区分：filing / official / reference / user。
`user` 是用户上传文档的副本，只对上传者可见（`owner_user_id`），原文件删除时随之删除。
第 3a-1 期不建向量表（规格 3b）。

Revision ID: 20261020_0030
Revises: 20261019_0029
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261020_0030"
down_revision: str | None = "20261019_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_UPLOAD_SOURCE = "00000000-0000-4000-8000-00000000c0a1"


def upgrade() -> None:
    op.create_table(
        "corpus_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("publisher", sa.String(80), nullable=False),
        # central_bank / statistics / regulator / exchange / filing / intl / user
        sa.Column("kind", sa.String(24), nullable=False),
        # rss / api / page / upload
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("allowed_hosts", postgresql.ARRAY(sa.Text()), nullable=False),
        # hourly / daily / weekly / on_demand
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("store_full_text", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("robots_checked_at", sa.Date(), nullable=True),
        sa.Column("terms_note", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "corpus_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpus_sources.id"),
            nullable=False,
        ),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        # filing / official / reference / user
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        # statement / minutes / speech / press / report / filing / upload
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.CHAR(64), nullable=False),
        sa.Column("has_full_text", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # indexed / metadata_only / failed
        sa.Column("status", sa.String(16), nullable=False),
        # official（官方与公司公告本身）/ reference（管理员导入）/ user
        sa.Column("authority", sa.String(16), nullable=False),
        sa.Column(
            "owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "user_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id"),
            nullable=True,
        ),
        sa.Column("symbol", sa.String(32), nullable=True),
        # 2026Q3 / 2026H1 / 2026FY
        sa.Column("period", sa.String(16), nullable=True),
        # annual / semiannual / q1 / q3 / forecast / express / inquiry / other
        sa.Column("report_type", sa.String(16), nullable=True),
        sa.Column("license_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("source_id", "canonical_url", "version"),
        sa.CheckConstraint(
            "(kind = 'user') = (owner_user_id IS NOT NULL)", name="ck_corpus_documents_owner"
        ),
    )
    op.create_index(
        "ix_corpus_documents_published", "corpus_documents", [sa.text("published_at DESC")]
    )
    op.create_index("ix_corpus_documents_owner", "corpus_documents", ["owner_user_id"])
    op.create_index(
        "ix_corpus_documents_user_document",
        "corpus_documents",
        ["user_document_id"],
        unique=True,
        postgresql_where=sa.text("user_document_id IS NOT NULL"),
    )
    op.create_table(
        "corpus_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpus_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        # 模板拼的块头（公司、标题、期次、章节）：写进 tsv，不显示，不参与逐字核对。
        sa.Column("context_header", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index"),
    )
    op.create_index("ix_corpus_chunks_tsv", "corpus_chunks", ["tsv"], postgresql_using="gin")
    op.create_table(
        "corpus_fetch_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpus_sources.id"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO corpus_sources (
                id, code, publisher, kind, method, allowed_hosts, frequency,
                store_full_text, retention_days, terms_note, enabled
            ) VALUES (
                CAST(:id AS uuid), 'user_upload', '你上传的文档', 'user', 'upload',
                ARRAY[]::text[], 'on_demand', true, NULL,
                '用户自己上传的文件，只对上传者本人可见；原文件删除时一并删除。', true
            )
            """
        ).bindparams(id=USER_UPLOAD_SOURCE)
    )


def downgrade() -> None:
    op.drop_table("corpus_fetch_runs")
    op.drop_index("ix_corpus_chunks_tsv", table_name="corpus_chunks")
    op.drop_table("corpus_chunks")
    op.drop_index("ix_corpus_documents_user_document", table_name="corpus_documents")
    op.drop_index("ix_corpus_documents_owner", table_name="corpus_documents")
    op.drop_index("ix_corpus_documents_published", table_name="corpus_documents")
    op.drop_table("corpus_documents")
    op.drop_table("corpus_sources")
