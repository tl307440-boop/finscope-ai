"""`app.services.pdf_text` 的解析器选择、页数校验与时间预算。

测试 PDF 全部由代码现场生成（PyMuPDF 写、手工拼字节流），仓库里不放二进制样本。
"""

from collections.abc import Callable

import pymupdf
import pytest
from app.services.pdf_text import (
    PARSER_PDFPLUMBER,
    PARSER_PYMUPDF,
    PARSER_PYPDF,
    PdfParserTimeoutError,
    PdfTextError,
    count_pages,
    extract_document,
    extract_pages,
    has_extractable_text,
    parser_version,
    table_to_text,
)
from tests.unit.pdf_samples import build_text_pdf

BODY = "FinScope 2026 annual report"
USER_PASSWORD = "finscope-test-pass"


def _text_pdf(page_count: int = 2) -> bytes:
    document = pymupdf.open()
    for number in range(1, page_count + 1):
        page = document.new_page()
        page.insert_text((72, 96), f"{BODY} page {number}", fontsize=12)
    data: bytes = document.tobytes()
    document.close()
    return data


def _encrypted_pdf() -> bytes:
    """带用户口令的 PDF：不提供口令就不该被解析。"""

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 96), "encrypted", fontsize=12)
    data: bytes = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw=USER_PASSWORD,
        user_pw=USER_PASSWORD,
        permissions=0,
    )
    document.close()
    return data


def _lattice_table_pdf() -> bytes:
    """手写最小 PDF：画 2x2 表格线并填四个单元格，供 pdfplumber 表格抽取用。"""

    content = (
        "1 w 0 0 0 RG\n"
        "50 750 200 60 re S\n"
        "50 780 200 30 re S\n"
        "150 750 100 60 re S\n"
        "50 780 100 30 re S\n"
        "BT /F1 12 Tf 60 790 Td (metric) Tj ET\n"
        "BT /F1 12 Tf 160 790 Td (2025) Tj ET\n"
        "BT /F1 12 Tf 60 760 Td (revenue) Tj ET\n"
        "BT /F1 12 Tf 160 760 Td (128) Tj ET\n"
    ).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 800] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    start_xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start_xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def test_text_pdf_is_extracted_by_pdfplumber_with_version() -> None:
    result = extract_document(_text_pdf(2))

    assert [page.page_number for page in result.pages] == [1, 2]
    assert BODY in result.pages[0].text
    assert result.parser_name == PARSER_PDFPLUMBER
    assert result.parser_version == parser_version(PARSER_PDFPLUMBER)
    assert result.parser_version not in {"", "unknown"}
    assert has_extractable_text(result.pages) is True
    assert count_pages(_text_pdf(2)) == 2


def test_table_cells_are_kept_as_searchable_text() -> None:
    pages = extract_pages(_lattice_table_pdf())

    assert "revenue" in pages[0].text
    tables = "\n".join(pages[0].tables)
    assert "revenue | 128" in tables
    assert pages[0].extractor == PARSER_PDFPLUMBER
    assert table_to_text([["a", None, " b \n c "], "noise", [None]]) == "a | b   c"


def test_non_pdf_bytes_are_rejected_before_any_parser() -> None:
    with pytest.raises(PdfTextError, match="不是 PDF"):
        extract_document(b"PK\x03\x04 not a pdf at all")


def test_page_limit_stops_extraction() -> None:
    pages = extract_pages(_text_pdf(3), max_pages=2)

    assert [page.page_number for page in pages] == [1, 2]
    assert len(pages) == 2


def test_encrypted_pdf_reports_a_clear_error() -> None:
    data = _encrypted_pdf()

    assert count_pages(data) is None
    with pytest.raises(PdfTextError, match="加密"):
        extract_document(data)


def test_parse_time_budget_is_enforced() -> None:
    ticks = iter([0.0, 0.1, 99.0])
    clock: Callable[[], float] = lambda: next(ticks)  # noqa: E731

    with pytest.raises(PdfParserTimeoutError, match="时间预算"):
        extract_document(_text_pdf(3), time_budget_seconds=1.0, clock=clock)


def test_degenerate_pdfs_raise_instead_of_silently_succeeding() -> None:
    with pytest.raises(PdfTextError):
        extract_document(b"")
    with pytest.raises(PdfTextError):
        extract_document(b"%PDF-1.4\nnot really a pdf")


def test_parser_selection_falls_back_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """pdfplumber 不可用时换 PyMuPDF，PyMuPDF 也不可用时才落到 pypdf。"""

    import app.services.pdf_text as module

    def plumber_down(*_args: object, **_kwargs: object) -> list[object]:
        raise module._PlumberUnavailable

    monkeypatch.setattr(module, "_plumber_pages", plumber_down)
    assert extract_document(_text_pdf(1)).parser_name == PARSER_PYMUPDF

    def mupdf_down(*_args: object, **_kwargs: object) -> list[object]:
        raise module._PymupdfUnavailable

    monkeypatch.setattr(module, "_mupdf_pages", mupdf_down)
    result = extract_document(build_text_pdf(["pypdf fallback body"]))
    assert result.parser_name == PARSER_PYPDF
    assert "pypdf fallback body" in result.pages[0].text
