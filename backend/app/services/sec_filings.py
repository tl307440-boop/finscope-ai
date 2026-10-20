"""美股 8-K 正文（知识库设计 §5.1）。

目录用已有的 EDGAR submissions。正文优先取 Exhibit 99.1（新闻稿），没有就用 8-K 主文件。
HTML 用标准库去掉 script / style；PDF 交给现有的解析器。10-K / 10-Q 的 MD&A 不在这里抓。
"""

import json
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from app.providers.disclosures import SEC_SUBMISSIONS, parse_sec_submissions
from app.providers.polite import PoliteClient, PoliteError
from app.services.filing_meta import FilingHit
from app.services.pdf_text import PageText
from app.utils.identifiers import normalize_cik

SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
_SKIP_TAGS = {"script", "style", "nav"}


class SecCatalog:
    def __init__(self, client: PoliteClient) -> None:
        self._client = client

    def list_8k(self, *, cik: str, name: str, symbol: str, start: date) -> tuple[FilingHit, ...]:
        payload = self._client.get(SEC_SUBMISSIONS.format(cik=normalize_cik(cik)))
        drafts = parse_sec_submissions(payload, display_name=name, start=start)
        return tuple(
            FilingHit(
                code=symbol,
                title=draft.title,
                url=draft.canonical_url,
                published_at=draft.published_at,
                report_type="8-K",
                period=None,
            )
            for draft in drafts
            if draft.event_type == "earnings"
        )

    def download_body(self, primary_url: str) -> tuple[str, bytes]:
        """返回实际正文的地址和字节。优先 Exhibit 99.1，索引失败就用主文件。"""

        folder = primary_url.rsplit("/", 1)[0]
        body_url = primary_url
        try:
            name = exhibit_name(self._client.get(f"{folder}/index.json"))
        except PoliteError as exc:
            if exc.code == "BLOCKED":
                raise
            name = None
        if name:
            body_url = f"{folder}/{name}"
        return body_url, self._client.get(body_url)


def exhibit_name(payload: bytes) -> str | None:
    """从 EDGAR index.json 里挑 Exhibit 99.1 的文件名。没有就返回 None。"""

    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    directory = decoded.get("directory")
    items = directory.get("item") if isinstance(directory, dict) else None
    if not isinstance(items, list):
        return None
    for raw in items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "").upper().replace(" ", "")
        name = raw.get("name")
        if kind in {"EX-99.1", "EX-99.01"} and isinstance(name, str) and _safe_name(name):
            return name
    return None


def pages_from_body(
    data: bytes, *, extract: Any, time_budget_seconds: float, max_pages: int
) -> list[PageText]:
    if data.startswith(b"%PDF"):
        pages: list[PageText] = extract(
            data, time_budget_seconds=time_budget_seconds, max_pages=max_pages
        )
        return pages
    text = html_to_text(data)
    if not text.strip():
        return []
    return [PageText(page_number=1, text=text)]


def html_to_text(data: bytes) -> str:
    parser = _VisibleText()
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _safe_name(name: str) -> bool:
    """文件名只能是这一份归档目录里的相对路径，不能跳出去。"""

    if name.startswith("/") or "\\" in name or ".." in name.split("/"):
        return False
    return urlsplit(name).scheme == "" and urlsplit(name).netloc == ""


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag in {"p", "div", "br", "tr", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)
