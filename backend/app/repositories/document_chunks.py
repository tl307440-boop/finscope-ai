import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, text

from app.services.pdf_chunks import TextChunk
from app.services.text_embed import LOCAL_EMBEDDING_MODEL
from app.utils.time import utc_now


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: UUID
    document_id: UUID
    document_title: str
    page_start: int
    page_end: int
    content: str
    content_hash: str


class ChunkRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def replace(
        self,
        document_id: UUID,
        chunks: list[TextChunk],
        embeddings: list[str],
        search_texts: list[str],
        *,
        page_count: int,
    ) -> int:
        now = utc_now()
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM document_chunks WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            for chunk, embedding, search_text in zip(chunks, embeddings, search_texts, strict=True):
                connection.execute(
                    text(
                        """
                        INSERT INTO document_chunks (
                            id, document_id, chunk_index, page_start, page_end,
                            char_start, char_end, section_title, content, content_hash,
                            token_count, embedding_model, embedding, tsv, created_at
                        ) VALUES (
                            :id, :document_id, :chunk_index, :page_start, :page_end,
                            :char_start, :char_end, :section_title, :content, :content_hash,
                            :token_count, :embedding_model,
                            CAST(:embedding AS vector),
                            to_tsvector('simple', :search_text),
                            :created_at
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "chunk_index": chunk.chunk_index,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                        "char_start": chunk.char_start,
                        "char_end": chunk.char_end,
                        "section_title": chunk.section_title,
                        "content": chunk.content,
                        "content_hash": chunk.content_hash,
                        "token_count": chunk.token_count,
                        "embedding_model": LOCAL_EMBEDDING_MODEL,
                        "embedding": embedding,
                        "search_text": search_text,
                        "created_at": now,
                    },
                )
            connection.execute(
                text(
                    """
                    UPDATE documents
                    SET parse_status = 'parsed', parse_error = NULL, page_count = :page_count
                    WHERE id = :document_id
                    """
                ),
                {"document_id": document_id, "page_count": page_count},
            )
        return len(chunks)

    def count_for(self, document_id: UUID) -> int:
        with self.engine.connect() as connection:
            value = connection.execute(
                text("SELECT count(*) FROM document_chunks WHERE document_id = :document_id"),
                {"document_id": document_id},
            ).scalar_one()
        return int(value)

    def finish(self, document_id: UUID, status: str, message: str | None) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM document_chunks WHERE document_id = :document_id"),
                {"document_id": document_id},
            )
            connection.execute(
                text(
                    """
                    UPDATE documents
                    SET parse_status = :status, parse_error = :message
                    WHERE id = :document_id
                    """
                ),
                {"document_id": document_id, "status": status, "message": message},
            )

    def search_text(
        self,
        *,
        owner_id: UUID,
        query: str,
        as_of: datetime,
        limit: int,
    ) -> list[StoredChunk]:
        return self._search(
            """
            SELECT c.id, c.document_id, d.title, c.page_start, c.page_end,
                   c.content, c.content_hash
            FROM document_chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            WHERE d.owner_id = :owner_id
              AND d.parse_status = 'parsed'
              AND d.uploaded_at <= :as_of
              AND c.embedding_model = :model
              AND c.tsv @@ plainto_tsquery('simple', :query)
            ORDER BY ts_rank_cd(c.tsv, plainto_tsquery('simple', :query)) DESC, c.id
            LIMIT :limit
            """,
            owner_id=owner_id,
            query=query,
            as_of=as_of,
            limit=limit,
            embedding=None,
        )

    def search_vector(
        self,
        *,
        owner_id: UUID,
        embedding: str,
        as_of: datetime,
        limit: int,
    ) -> list[StoredChunk]:
        return self._search(
            """
            SELECT c.id, c.document_id, d.title, c.page_start, c.page_end,
                   c.content, c.content_hash
            FROM document_chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            WHERE d.owner_id = :owner_id
              AND d.parse_status = 'parsed'
              AND d.uploaded_at <= :as_of
              AND c.embedding_model = :model
              AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> CAST(:embedding AS vector), c.id
            LIMIT :limit
            """,
            owner_id=owner_id,
            query="",
            as_of=as_of,
            limit=limit,
            embedding=embedding,
        )

    def _search(
        self,
        statement: str,
        *,
        owner_id: UUID,
        query: str,
        as_of: datetime,
        limit: int,
        embedding: str | None,
    ) -> list[StoredChunk]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(statement),
                {
                    "owner_id": owner_id,
                    "query": query,
                    "as_of": as_of,
                    "limit": limit,
                    "model": LOCAL_EMBEDDING_MODEL,
                    "embedding": embedding,
                },
            ).mappings()
            return [
                StoredChunk(
                    chunk_id=row["id"],
                    document_id=row["document_id"],
                    document_title=row["title"],
                    page_start=row["page_start"],
                    page_end=row["page_end"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                )
                for row in rows
            ]
