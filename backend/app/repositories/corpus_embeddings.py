"""语料块向量的读写（规格 §10 `corpus_embeddings`）。

只给官方文本、公告和参考资料补向量；用户上传的文档（`kind = 'user'`）不发给外部服务。
只补每个地址最新版本的块，按发布时间从新到旧，新资料先能语义检索。
对方拒绝向量化的块记一行空向量和原因，不再反复重试。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, text

from app.services.text_embed import vector_literal


@dataclass(frozen=True)
class PendingChunk:
    chunk_id: UUID
    title: str
    header: str
    content: str


@dataclass(frozen=True)
class ChunkVector:
    chunk_id: UUID
    # None：对方拒绝向量化这一块（比如内容审核），`skip_reason` 写原因。
    vector: list[float] | None
    truncated_chars: int = 0
    skip_reason: str | None = None


class CorpusEmbeddingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def pending(self, model_id: str, limit: int) -> list[PendingChunk]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.id, d.title, c.context_header, c.content
                    FROM corpus_chunks AS c
                    JOIN corpus_documents AS d ON d.id = c.document_id
                    WHERE d.status = 'indexed' AND d.kind <> 'user'
                      AND d.version = (
                          SELECT max(v.version) FROM corpus_documents AS v
                          WHERE v.source_id = d.source_id AND v.canonical_url = d.canonical_url
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM corpus_embeddings AS e
                          WHERE e.chunk_id = c.id AND e.embedding_model = :model
                      )
                    ORDER BY d.published_at DESC, c.document_id, c.chunk_index
                    LIMIT :limit
                    """
                ),
                {"model": model_id, "limit": limit},
            ).all()
        return [
            PendingChunk(chunk_id=row[0], title=row[1], header=row[2], content=row[3])
            for row in rows
        ]

    def save(self, model_id: str, rows: Sequence[ChunkVector], now: datetime) -> None:
        if not rows:
            return
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO corpus_embeddings (
                        chunk_id, embedding_model, embedding, truncated_chars, skip_reason,
                        embedded_at
                    ) VALUES (
                        :chunk, :model, CAST(:vector AS halfvec), :truncated, :reason, :now
                    )
                    ON CONFLICT (chunk_id, embedding_model) DO NOTHING
                    """
                ),
                [
                    {
                        "chunk": row.chunk_id,
                        "model": model_id,
                        "vector": None if row.vector is None else vector_literal(row.vector),
                        "truncated": row.truncated_chars,
                        "reason": row.skip_reason,
                        "now": now,
                    }
                    for row in rows
                ],
            )
