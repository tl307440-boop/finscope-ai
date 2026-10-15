from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.repositories.documents import DocumentRecord
from app.services.document_upload import UploadOutcome


class UploadData(BaseModel):
    status: str
    code: str
    message: str
    document_id: UUID | None = None
    sha256: str | None = None
    page_count: int | None = None


class DocumentItem(BaseModel):
    id: UUID
    title: str
    sha256: str
    page_count: int | None = None
    parse_status: str
    parse_error: str | None = None
    uploaded_at: datetime


class DocumentList(BaseModel):
    documents: list[DocumentItem]


def build_upload(outcome: UploadOutcome) -> UploadData:
    return UploadData(
        status=outcome.status,
        code=outcome.code,
        message=outcome.message,
        document_id=outcome.document_id,
        sha256=outcome.sha256,
        page_count=outcome.page_count,
    )


def build_documents(rows: list[DocumentRecord]) -> DocumentList:
    return DocumentList(
        documents=[
            DocumentItem(
                id=row.id,
                title=row.title,
                sha256=row.sha256,
                page_count=row.page_count,
                parse_status=row.parse_status,
                parse_error=row.parse_error,
                uploaded_at=row.uploaded_at,
            )
            for row in rows
        ]
    )
