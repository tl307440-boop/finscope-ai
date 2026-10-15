from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.repositories.documents import DocumentRecord
from app.services.document_index import INDEX_NOTICE, IndexOutcome, SearchHit
from app.services.document_upload import UploadOutcome
from app.services.text_embed import LOCAL_EMBEDDING_MODEL


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


class IndexData(BaseModel):
    status: str
    chunk_count: int
    message: str
    embedding_model: str


class SearchItem(BaseModel):
    document_id: UUID
    document_title: str
    chunk_id: UUID
    page_start: int
    page_end: int
    snippet: str


class SearchData(BaseModel):
    items: list[SearchItem]
    embedding_model: str
    notice: str


def build_index(outcome: IndexOutcome) -> IndexData:
    return IndexData(
        status=outcome.status,
        chunk_count=outcome.chunk_count,
        message=outcome.message,
        embedding_model=LOCAL_EMBEDDING_MODEL,
    )


def build_search(hits: list[SearchHit]) -> SearchData:
    return SearchData(
        items=[
            SearchItem(
                document_id=hit.document_id,
                document_title=hit.document_title,
                chunk_id=hit.chunk_id,
                page_start=hit.page_start,
                page_end=hit.page_end,
                snippet=hit.snippet,
            )
            for hit in hits
        ],
        embedding_model=LOCAL_EMBEDDING_MODEL,
        notice=INDEX_NOTICE,
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
