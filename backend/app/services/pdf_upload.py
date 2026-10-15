import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

PDF_MAGIC = b"%PDF-"
MAX_PAGES = 200
_PAGE = re.compile(rb"/Type\s*/Page(?!s)")
_SHA = re.compile(r"^[0-9a-f]{64}$")

UploadStatus = Literal["queued", "ocr_required", "failed", "rejected"]


@dataclass(frozen=True)
class PdfInspection:
    status: UploadStatus
    code: str
    message: str
    sha256: str
    page_count: int | None


def inspect_pdf(data: bytes, *, max_bytes: int, max_pages: int = MAX_PAGES) -> PdfInspection:
    digest = hashlib.sha256(data).hexdigest()
    if not data.startswith(PDF_MAGIC):
        return PdfInspection("rejected", "REJECTED_FORMAT", "文件内容不是 PDF", digest, None)
    if len(data) > max_bytes:
        return PdfInspection("rejected", "REJECTED_SIZE", "文件超过大小限制", digest, None)
    pages = len(_PAGE.findall(data))
    if b"%%EOF" not in data or pages < 1:
        return PdfInspection(
            "failed", "DAMAGED", "文件损坏，已记录失败，不会进入解析", digest, None
        )
    if pages > max_pages:
        return PdfInspection("rejected", "REJECTED_PAGES", "页数超过限制", digest, pages)
    if b"BT" not in data or (b"Tj" not in data and b"TJ" not in data):
        return PdfInspection(
            "ocr_required",
            "OCR_REQUIRED",
            "扫描件需要文字版 PDF，当前标记为待识别",
            digest,
            pages,
        )
    return PdfInspection("queued", "ACCEPTED", "已接收，等待解析", digest, pages)


def stored_pdf_path(root: Path, owner_id: UUID, sha256: str) -> Path:
    if not _SHA.fullmatch(sha256):
        raise ValueError("摘要格式不正确")
    base = root.resolve()
    path = (base / str(owner_id) / f"{sha256}.pdf").resolve()
    if not path.is_relative_to(base):
        raise ValueError("上传路径越界")
    return path


def display_name(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    cleaned = raw.replace("..", "")
    if not cleaned:
        return "未命名.pdf"
    return cleaned[:120]
