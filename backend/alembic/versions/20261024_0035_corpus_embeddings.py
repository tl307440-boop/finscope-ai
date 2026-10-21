"""语料块向量（规格 §10 `corpus_embeddings`，3b 期）：智谱 embedding-3，1024 维。

- 向量放独立表，列用不定维度的 `halfvec`（半精度，1024 维约 2 KB / 块）；
  每个模型一个部分表达式 HNSW 索引，查询写成同样的表达式和条件才走得到索引。
- `embedding` 可以为空：对方拒绝向量化的块（如内容审核）记一行空向量和 `skip_reason`，不再重试。
- 用户上传的文档不补向量（由写入方保证，见 `CorpusEmbeddingRepository.pending`）。
- 依赖 pgvector ≥ 0.7（`halfvec`、`halfvec_cosine_ops`）；版本不够时升级直接失败并说明原因。

Revision ID: 20261024_0035
Revises: 20261023_0034
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20261024_0035"
down_revision: str | None = "20261023_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODEL_ID = "embedding-3@1024"
DIMS = 1024
INDEX = "ix_corpus_embeddings_embedding3_1024"


def _require_halfvec() -> None:
    if context.is_offline_mode():
        return
    version = (
        op.get_bind()
        .execute(sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        .scalar()
    )
    parts = [int(part) for part in str(version or "0").split(".")[:2] if part.isdigit()]
    if tuple(parts) < (0, 7):
        raise RuntimeError(
            f"语料向量需要 pgvector >= 0.7（halfvec），当前是 {version or '未安装'}；"
            "请先升级数据库镜像并执行 ALTER EXTENSION vector UPDATE"
        )


def upgrade() -> None:
    _require_halfvec()
    op.execute(
        """
        CREATE TABLE corpus_embeddings (
            chunk_id uuid NOT NULL REFERENCES corpus_chunks(id) ON DELETE CASCADE,
            embedding_model varchar(80) NOT NULL,
            embedding halfvec,
            truncated_chars integer NOT NULL DEFAULT 0,
            skip_reason text,
            embedded_at timestamptz NOT NULL,
            PRIMARY KEY (chunk_id, embedding_model),
            CONSTRAINT ck_corpus_embeddings_reason
                CHECK ((embedding IS NULL) = (skip_reason IS NOT NULL))
        )
        """
    )
    op.execute(
        f"""
        CREATE INDEX {INDEX} ON corpus_embeddings
        USING hnsw ((embedding::halfvec({DIMS})) halfvec_cosine_ops)
        WHERE embedding_model = '{MODEL_ID}'
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX}")
    op.execute("DROP TABLE IF EXISTS corpus_embeddings")
