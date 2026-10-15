from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PdfTextError(Exception):
    pass


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


def extract_pages(data: bytes) -> list[PageText]:
    try:
        reader = PdfReader(BytesIO(data))
        pages = [
            PageText(index, page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except PdfReadError as exc:
        raise PdfTextError("无法从文字层抽取正文") from exc
    if not pages:
        raise PdfTextError("无法从文字层抽取正文")
    return pages


def has_extractable_text(pages: list[PageText]) -> bool:
    return any(page.text.strip() for page in pages)
