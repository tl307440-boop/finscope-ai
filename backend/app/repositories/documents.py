import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.db.models.document import Document
from app.utils.time import utc_now


@dataclass(frozen=True)
class DocumentRecord:
    id: UUID
    title: str
    sha256: str
    page_count: int | None
    parse_status: str
    parse_error: str | None
    uploaded_at: datetime


@dataclass(frozen=True)
class OwnedFile:
    id: UUID
    owner_id: UUID
    title: str
    storage_path: str | None
    parse_status: str
    parse_error: str | None
    page_count: int | None
    uploaded_at: datetime


class DocumentRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def find(self, owner_id: UUID, sha256: str) -> DocumentRecord | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(Document).where(
                    Document.owner_id == owner_id,
                    Document.sha256 == sha256,
                )
            ).one_or_none()
            if row is None:
                return None
            return _record(row)

    def add(
        self,
        *,
        owner_id: UUID,
        title: str,
        storage_path: str | None,
        sha256: str,
        page_count: int | None,
        size_bytes: int,
        parse_status: str,
        parse_error: str | None,
    ) -> UUID:
        with Session(self.engine) as session, session.begin():
            row = Document(
                id=uuid.uuid4(),
                owner_id=owner_id,
                title=title,
                storage_path=storage_path,
                sha256=sha256,
                mime_type="application/pdf",
                page_count=page_count,
                size_bytes=size_bytes,
                uploaded_at=utc_now(),
                parse_status=parse_status,
                parse_error=parse_error,
            )
            session.add(row)
            session.flush()
            return row.id

    def list_owned(self, owner_id: UUID) -> list[DocumentRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(Document)
                .where(Document.owner_id == owner_id)
                .order_by(Document.uploaded_at.desc())
            ).all()
            return [_record(row) for row in rows]

    def get_owned(self, owner_id: UUID, document_id: UUID) -> OwnedFile | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(Document).where(Document.id == document_id, Document.owner_id == owner_id)
            ).one_or_none()
            if row is None:
                return None
            return _owned(row)

    def claim_owned(self, owner_id: UUID, document_id: UUID) -> OwnedFile | None:
        return self._claim(
            """
            UPDATE documents
            SET parse_status = 'parsing', parse_error = NULL
            WHERE id = :document_id
              AND owner_id = :owner_id
              AND parse_status = 'queued'
            RETURNING id, owner_id, title, storage_path, parse_status, parse_error,
                      page_count, uploaded_at
            """,
            {"document_id": document_id, "owner_id": owner_id},
        )

    def claim_next_queued(self, path_prefix: str) -> OwnedFile | None:
        return self._claim(
            """
            UPDATE documents
            SET parse_status = 'parsing', parse_error = NULL
            WHERE id = (
                SELECT id
                FROM documents
                WHERE parse_status = 'queued'
                  AND storage_path IS NOT NULL
                  AND strpos(storage_path, :prefix) = 1
                ORDER BY uploaded_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, owner_id, title, storage_path, parse_status, parse_error,
                      page_count, uploaded_at
            """,
            {"prefix": path_prefix},
        )

    def _claim(self, statement: str, params: dict[str, object]) -> OwnedFile | None:
        with self.engine.begin() as connection:
            row = connection.execute(text(statement), params).mappings().one_or_none()
        if row is None:
            return None
        return OwnedFile(
            id=row["id"],
            owner_id=row["owner_id"],
            title=row["title"],
            storage_path=row["storage_path"],
            parse_status=row["parse_status"],
            parse_error=row["parse_error"],
            page_count=row["page_count"],
            uploaded_at=row["uploaded_at"],
        )


def _owned(row: Document) -> OwnedFile:
    return OwnedFile(
        id=row.id,
        owner_id=row.owner_id,
        title=row.title,
        storage_path=row.storage_path,
        parse_status=row.parse_status,
        parse_error=row.parse_error,
        page_count=row.page_count,
        uploaded_at=row.uploaded_at,
    )


def _record(row: Document) -> DocumentRecord:
    return DocumentRecord(
        id=row.id,
        title=row.title,
        sha256=row.sha256,
        page_count=row.page_count,
        parse_status=row.parse_status,
        parse_error=row.parse_error,
        uploaded_at=row.uploaded_at,
    )
