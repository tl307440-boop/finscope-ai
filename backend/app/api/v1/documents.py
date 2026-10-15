from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.repositories.documents import DocumentRepository
from app.schemas.documents import build_documents, build_upload
from app.services.auth_types import UserRecord
from app.services.document_upload import receive_pdf

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
