from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.repositories.document_chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.schemas.documents import build_documents, build_index, build_search, build_upload
from app.services.auth_types import UserRecord
from app.services.document_index import index_owned_document, search_owned_documents
from app.services.document_upload import receive_pdf
from app.utils.time import utc_now

router = APIRouter(prefix="/documents", tags=["文档"])


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _root(request: Request) -> Path:
    if hasattr(request.app.state, "upload_root"):
        override = request.app.state.upload_root
        if isinstance(override, Path):
            return override
    return Path("data/uploads")


@router.get("", summary="我的上传文件")
def list_documents(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = DocumentRepository(_engine(request)).list_owned(user.id)
    return JSONResponse(status_code=200, content=success_response(request, build_documents(rows)))


@router.post("", summary="上传 PDF")
async def upload_document(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
) -> JSONResponse:
    settings = get_settings()
    data = await file.read()
    outcome = receive_pdf(
        DocumentRepository(_engine(request)),
        owner_id=user.id,
        filename=file.filename,
        data=data,
        root=_root(request),
        max_bytes=settings.upload_max_mb * 1024 * 1024,
        max_pages=settings.upload_max_pages,
    )
    payload = build_upload(outcome)
    if outcome.status == "rejected":
        raise AppError(
            code=outcome.code,
            message=outcome.message,
            status_code=422,
            details={"sha256": outcome.sha256 or ""},
        )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/search", summary="检索已解析的 PDF")
def search_documents(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    q: Annotated[str, Query(min_length=1, max_length=200)],
    as_of: Annotated[datetime | None, Query()] = None,
) -> JSONResponse:
    hits = search_owned_documents(
        ChunkRepository(_engine(request)),
        owner_id=user.id,
        query=q,
        as_of=_aware(as_of) if as_of is not None else utc_now(),
    )
    return JSONResponse(status_code=200, content=success_response(request, build_search(hits)))


@router.post("/{document_id}/index", summary="抽取正文并建立索引")
def index_document(
    document_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    settings = get_settings()
    outcome = index_owned_document(
        DocumentRepository(_engine(request)),
        ChunkRepository(_engine(request)),
        owner_id=user.id,
        document_id=document_id,
        root=_root(request),
        target_tokens=settings.chunk_target_tokens,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        embedding_dim=settings.embedding_dim,
    )
    return JSONResponse(status_code=200, content=success_response(request, build_index(outcome)))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
