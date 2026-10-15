from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.repositories.documents import DocumentRepository
from app.services.pdf_upload import PdfInspection, display_name, inspect_pdf, stored_pdf_path


@dataclass(frozen=True)
class UploadOutcome:
    status: str
    code: str
    message: str
    document_id: UUID | None
    sha256: str | None
    page_count: int | None


def receive_pdf(
    repository: DocumentRepository,
    *,
    owner_id: UUID,
    filename: str | None,
    data: bytes,
    root: Path,
    max_bytes: int,
    max_pages: int,
) -> UploadOutcome:
    inspection = inspect_pdf(data, max_bytes=max_bytes, max_pages=max_pages)
    if inspection.status == "rejected":
        return _from_inspection(inspection, None)
    existing = repository.find(owner_id, inspection.sha256)
    if existing is not None:
        return UploadOutcome(
            status="duplicate",
            code="DUPLICATE",
            message="这份文件已经上传过",
            document_id=existing.id,
            sha256=inspection.sha256,
            page_count=existing.page_count,
        )
    storage = _write_if_needed(root, owner_id, inspection, data)
    document_id = repository.add(
        owner_id=owner_id,
        title=display_name(filename),
        storage_path=None if storage is None else str(storage),
        sha256=inspection.sha256,
        page_count=inspection.page_count,
        size_bytes=len(data),
        parse_status=inspection.status,
        parse_error=None if inspection.status == "queued" else inspection.message,
    )
    return _from_inspection(inspection, document_id)


def _write_if_needed(
    root: Path,
    owner_id: UUID,
    inspection: PdfInspection,
    data: bytes,
) -> Path | None:
    if inspection.status not in {"queued", "ocr_required"}:
        return None
    path = stored_pdf_path(root, owner_id, inspection.sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _from_inspection(inspection: PdfInspection, document_id: UUID | None) -> UploadOutcome:
    return UploadOutcome(
        status=inspection.status,
        code=inspection.code,
        message=inspection.message,
        document_id=document_id,
        sha256=inspection.sha256,
        page_count=inspection.page_count,
    )
