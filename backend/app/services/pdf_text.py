"""PDF 文本与表格抽取。

规格书技术选型：主提取器 pdfplumber（文本 + 表格），PyMuPDF 作为第二提取器
（页数快速校验、pdfplumber 整体不可用时接管），pypdf 降级为最后回退。

对外只保留 ``PageText`` / ``extract_pages`` / ``has_extractable_text`` /
``PdfTextError`` 这几个历史接口，``pdf_chunks`` 与 ``document_index`` 继续可用。
"""

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from importlib import metadata
from io import BytesIO
from typing import Any, Protocol

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"
PARSER_PDFPLUMBER = "pdfplumber"
PARSER_PYMUPDF = "pymupdf"
PARSER_PYPDF = "pypdf"
DEFAULT_TIME_BUDGET_SECONDS = 30.0
TABLE_ROW_SEPARATOR = " | "
TABLE_PAGE_SEPARATOR = "\n"


class PdfTextError(Exception):
    """解析失败；消息面向用户，不包含本地路径或密钥。"""


class PdfParserTimeoutError(PdfTextError):
    """超过解析时间预算。"""


class _PdfSource(Protocol):
    pages: list[Any]
    metadata: dict[str, Any]

    def __enter__(self) -> "_PdfSource": ...

    def __exit__(self, *exc: object) -> None: ...


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str
    extractor: str = PARSER_PDFPLUMBER
    tables: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class ExtractionResult:
    pages: list[PageText]
    parser_name: str
    parser_version: str
    elapsed_seconds: float


def parser_version(name: str) -> str:
    """读取解析器版本，用于 §11 的 parse_document 幂等标识 sha256+parser_version。"""

    module = {"pdfplumber": "pdfplumber", "pymupdf": "pymupdf", "pypdf": "pypdf"}[name]
    try:
        return metadata.version(module)
    except metadata.PackageNotFoundError:  # pragma: no cover - 依赖缺失时兜底
        return "unknown"


def extract_pages(
    data: bytes,
    *,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    max_pages: int | None = None,
    on_elapsed: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> list[PageText]:
    """按页抽取正文，保持历史返回类型 ``list[PageText]``。"""

    result = extract_document(
        data,
        time_budget_seconds=time_budget_seconds,
        max_pages=max_pages,
        on_elapsed=on_elapsed,
        clock=clock,
    )
    return result.pages


def extract_document(
    data: bytes,
    *,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    max_pages: int | None = None,
    on_elapsed: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionResult:
    """返回页文本、表格，以及实际使用的解析器名称与版本。"""

    if not data.startswith(PDF_MAGIC):
        raise PdfTextError("文件内容不是 PDF")
    if max_pages is not None and max_pages < 1:
        raise PdfTextError("最大页数配置不合法")
    started = clock()
    pages, parser_name = _extract_with_best_parser(data, time_budget_seconds, max_pages, clock)
    if not pages:
        raise PdfTextError("无法从文字层抽取正文")
    elapsed = max(clock() - started, 0.0)
    if on_elapsed is not None:
        on_elapsed(elapsed)
    return ExtractionResult(
        pages=pages,
        parser_name=parser_name,
        parser_version=parser_version(parser_name),
        elapsed_seconds=elapsed,
    )


def _extract_with_best_parser(
    data: bytes,
    time_budget_seconds: float,
    max_pages: int | None,
    clock: Callable[[], float],
) -> tuple[list[PageText], str]:
    """pdfplumber 主选，PyMuPDF 次选，pypdf 兜底。

    三个解析器都失败时由最后一个抛出 ``PdfTextError``；中途失败一律换成下一个，
    这样损坏文件最终仍以 ``PdfTextError`` 结束，而不是漏出内部异常类型。
    """

    try:
        return _plumber_pages(data, time_budget_seconds, max_pages, clock), PARSER_PDFPLUMBER
    except PdfParserTimeoutError:
        raise
    except (PdfTextError, _PlumberUnavailable) as exc:
        logger.info("pdfplumber 抽取失败，改用 PyMuPDF error_type=%s", type(exc).__name__)
    try:
        return _mupdf_pages(data, time_budget_seconds, max_pages, clock), PARSER_PYMUPDF
    except PdfParserTimeoutError:
        raise
    except (PdfTextError, _PymupdfUnavailable) as exc:
        logger.warning("PyMuPDF 抽取失败，回退到 pypdf error_type=%s", type(exc).__name__)
    return _pypdf_pages(data), PARSER_PYPDF


def count_pages(data: bytes) -> int | None:
    """PyMuPDF 页数快速校验；加密或损坏时返回 None，由调用方给出明确状态。"""

    if not data.startswith(PDF_MAGIC):
        return None
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 依赖缺失时兜底
        return None
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        return None
    try:
        if bool(document.needs_pass):
            return None
        return int(document.page_count)
    finally:
        document.close()


def has_extractable_text(pages: list[PageText]) -> bool:
    return any(page.text.strip() for page in pages)


def table_to_text(table: Any) -> str:
    """把 pdfplumber 的表格单元格转成可检索的一行行文本。"""

    if not isinstance(table, list):
        return ""
    rows: list[str] = []
    for raw_row in table:
        if not isinstance(raw_row, list):
            continue
        cells = [
            str(cell).strip().replace("\n", " ")
            for cell in raw_row
            if cell is not None and str(cell).strip()
        ]
        if cells:
            rows.append(TABLE_ROW_SEPARATOR.join(cells))
    return TABLE_PAGE_SEPARATOR.join(rows)


class _PlumberUnavailable(Exception):
    """pdfplumber 缺少依赖或整体不可用，需要换解析器。"""


class _PymupdfUnavailable(Exception):
    """PyMuPDF 缺少依赖或整体不可用，需要回退 pypdf。"""


def _plumber_pages(
    data: bytes,
    time_budget_seconds: float,
    max_pages: int | None,
    clock: Callable[[], float],
) -> list[PageText]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - 依赖缺失时兜底
        raise _PlumberUnavailable from exc
    started = clock()
    pages: list[PageText] = []
    try:
        with _open_plumber(pdfplumber, data) as document:
            for number, page in enumerate(document.pages, start=1):
                _check_budget(started, time_budget_seconds, clock)
                if max_pages is not None and number > max_pages:
                    break
                pages.append(
                    PageText(
                        page_number=number,
                        text=page.extract_text() or "",
                        extractor=PARSER_PDFPLUMBER,
                        tables=_page_tables(page),
                    )
                )
    except PdfTextError:
        raise
    except _PlumberUnavailable:
        raise
    except Exception as exc:
        raise PdfTextError("PDF 正文抽取失败，文件可能损坏或加密") from exc
    return pages


@contextmanager
def _open_plumber(module: Any, data: bytes) -> Iterator[_PdfSource]:
    try:
        document: _PdfSource = module.open(BytesIO(data))
    except Exception as exc:
        raise _PlumberUnavailable from exc
    try:
        yield document
    finally:
        del document


def _mupdf_pages(
    data: bytes,
    time_budget_seconds: float,
    max_pages: int | None,
    clock: Callable[[], float],
) -> list[PageText]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - 依赖缺失时兜底
        raise _PymupdfUnavailable from exc
    started = clock()
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise _PymupdfUnavailable from exc
    try:
        if bool(document.needs_pass):
            raise PdfTextError("PDF 已加密，需要密码后才能解析")
        pages: list[PageText] = []
        page_total = int(document.page_count)
        for number in range(1, page_total + 1):
            _check_budget(started, time_budget_seconds, clock)
            if max_pages is not None and number > max_pages:
                break
            page: Any = document.load_page(number - 1)
            pages.append(
                PageText(
                    page_number=number,
                    text=page.get_text(),
                    extractor=PARSER_PYMUPDF,
                )
            )
        return pages
    finally:
        with suppress(Exception):
            document.close()


def _pypdf_pages(data: bytes) -> list[PageText]:
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted:
            raise PdfTextError("PDF 已加密，需要密码后才能解析")
        return [
            PageText(
                page_number=index,
                text=page.extract_text() or "",
                extractor=PARSER_PYPDF,
            )
            for index, page in enumerate(reader.pages, start=1)
        ]
    except PdfTextError:
        raise
    except PdfReadError as exc:
        raise PdfTextError("无法从文字层抽取正文") from exc
    except Exception as exc:
        raise PdfTextError("PDF 正文抽取失败，文件可能损坏或加密") from exc


def _page_tables(page: Any) -> tuple[str, ...]:
    try:
        found = page.extract_tables()
    except Exception as exc:
        logger.info("表格抽取失败，跳过该页表格 error_type=%s", type(exc).__name__)
        return ()
    lines = [table_to_text(table) for table in found]
    return tuple(line for line in lines if line)


def _check_budget(started: float, budget: float, clock: Callable[[], float]) -> None:
    if budget > 0 and clock() - started > budget:
        raise PdfParserTimeoutError("PDF 解析超出时间预算，已停止解析")
