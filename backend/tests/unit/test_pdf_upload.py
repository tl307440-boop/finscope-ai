from pathlib import Path
from uuid import UUID

import pymupdf
from app.services.pdf_upload import display_name, inspect_pdf, stored_pdf_path

TEXT_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Count 1 /Kids [3 0 R] >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 29 >>
stream
BT /F1 12 Tf (Hi) Tj ET
endstream
endobj
%%EOF
"""

SCANNED_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Count 1 /Kids [3 0 R] >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im0 4 0 R >> >> >>
endobj
4 0 obj
<< /Subtype /Image /Width 1 /Height 1 >>
endobj
%%EOF
"""


def test_non_pdf_magic_is_rejected() -> None:
    result = inspect_pdf(b"not a pdf", max_bytes=1000)
    assert result.status == "rejected"
    assert result.code == "REJECTED_FORMAT"


def test_oversized_pdf_is_rejected() -> None:
    result = inspect_pdf(TEXT_PDF, max_bytes=10)
    assert result.code == "REJECTED_SIZE"


def test_too_many_pages_are_rejected() -> None:
    # 用 PyMuPDF 生成一份真正的 3 页 PDF：页数由解析器实际读取，
    # 不再依赖 ``/Type /Page`` 文本出现次数这种会被正文误触发的启发式。
    with pymupdf.open() as document:
        for _ in range(3):
            document.new_page()
        data = document.tobytes()
    result = inspect_pdf(data, max_bytes=100_000, max_pages=2)
    assert result.code == "REJECTED_PAGES"
    assert result.page_count == 3


def test_scanned_pdf_requires_ocr_and_text_pdf_is_queued() -> None:
    scanned = inspect_pdf(SCANNED_PDF, max_bytes=10_000)
    assert scanned.status == "ocr_required"
    assert scanned.code == "OCR_REQUIRED"
    queued = inspect_pdf(TEXT_PDF, max_bytes=10_000)
    assert queued.status == "queued"
    assert queued.page_count == 1


def test_damaged_pdf_is_kept_as_a_failure() -> None:
    result = inspect_pdf(b"%PDF-1.4\nbroken", max_bytes=1000)
    assert result.status == "failed"
    assert result.code == "DAMAGED"


def test_storage_path_uses_hash_and_rejects_traversal() -> None:
    owner = UUID("11111111-1111-1111-1111-111111111111")
    root = Path("data/uploads")
    sha = "a" * 64
    path = stored_pdf_path(root, owner, sha)
    assert path.name == f"{sha}.pdf"
    assert owner.hex not in path.name
    assert str(owner) in path.parts
    assert display_name("../../secret.pdf") == "secret.pdf"
    try:
        stored_pdf_path(root, owner, "../" + "a" * 64)
    except ValueError:
        return
    raise AssertionError("非法摘要应当被拒绝")
