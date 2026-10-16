import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.services.pdf_text import PARSER_PDFPLUMBER, count_pages, parser_version

PDF_MAGIC = b"%PDF-"
MAX_PAGES = 200
# 规格书 §12.1 要求上传检查「解析时间预算」；上传阶段做结构校验，解析阶段按同一预算执行。
DEFAULT_PARSE_TIME_BUDGET_SECONDS = 30.0
# 允许的 mime：octet-stream 是浏览器对无扩展名/未知扩展名的兜底，不能据此直接判失败。
PDF_MIME_TYPES = frozenset({"application/pdf", "application/x-pdf", "application/octet-stream"})
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


def inspect_pdf(
    data: bytes,
    *,
    max_bytes: int,
    max_pages: int = MAX_PAGES,
    mime_type: str | None = None,
    parse_time_budget_seconds: float = DEFAULT_PARSE_TIME_BUDGET_SECONDS,
) -> PdfInspection:
    digest = hashlib.sha256(data).hexdigest()
    if not data.startswith(PDF_MAGIC):
        return PdfInspection("rejected", "REJECTED_FORMAT", "文件内容不是 PDF", digest, None)
    if len(data) > max_bytes:
        return PdfInspection("rejected", "REJECTED_SIZE", "文件超过大小限制", digest, None)
    if not mime_type_allowed(mime_type):
        return PdfInspection("rejected", "REJECTED_MIME", "文件类型不是 PDF", digest, None)
    if parse_time_budget_seconds <= 0:
        return PdfInspection("rejected", "REJECTED_BUDGET", "解析时间预算配置不合法", digest, None)
    if b"%%EOF" not in data:
        return PdfInspection(
            "failed", "DAMAGED", "文件损坏，已记录失败，不会进入解析", digest, None
        )
    checked = count_pages(data)
    if checked is None and b"/Encrypt" in data:
        return PdfInspection("failed", "ENCRYPTED", "PDF 已加密，无法解析", digest, None)
    pages = checked if checked is not None else len(_PAGE.findall(data))
    if pages < 1:
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


def mime_type_allowed(mime_type: str | None) -> bool:
    if mime_type is None or not mime_type.strip():
        return True
    return mime_type.strip().lower().split(";")[0].strip() in PDF_MIME_TYPES


def parse_document_identity(sha256: str, parser_version: str | None = None) -> str:
    """规格书 §11：parse_document 的幂等标识是 sha256+parser_version。

    ``parser_version`` 省略时取主提取器（pdfplumber）的版本；传别的解析器版本时
    得到不同的键——换了解析器自然就该重新解析同一份文件。
    """

    version = parser_version or default_parser_version()
    return f"parse_document:{sha256}:{version}"


def default_parser_version() -> str:
    return parser_version(PARSER_PDFPLUMBER)


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
