import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select
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
