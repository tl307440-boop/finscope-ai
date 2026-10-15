import hashlib
import re
from dataclasses import dataclass

from app.services.pdf_text import PageText
from app.services.text_embed import estimate_tokens

_ATOM = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\u4e00-\u9fffA-Za-z0-9]+")
_HEADING_MARKS = "。！？.!?"


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_title: str | None
    content: str
    token_count: int
    content_hash: str


@dataclass(frozen=True)
class _Unit:
    page: int
    text: str
    tokens: int
    char_start: int
    char_end: int


def chunk_pages(
    pages: list[PageText],
    *,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[TextChunk]:
    if not (0 <= overlap_tokens < target_tokens <= max_tokens):
        raise ValueError("分块长度必须满足 0 ≤ 重叠 < 目标 ≤ 上限")
    units = _units(pages, max_tokens)
    packed = _pack(units, target_tokens, max_tokens, overlap_tokens)
    chunks: list[TextChunk] = []
    section: str | None = None
    for group in packed:
        heading = _heading(group[-1].text) or _heading(group[0].text)
        if heading:
            section = heading
        content = "\n\n".join(unit.text.strip() for unit in group if unit.text.strip())
        if not content:
            continue
        pages_in_group = [unit.page for unit in group]
        chunks.append(
            TextChunk(
                chunk_index=len(chunks),
                page_start=min(pages_in_group),
                page_end=max(pages_in_group),
                char_start=group[0].char_start,
                char_end=group[-1].char_end,
                section_title=section,
                content=content,
                token_count=sum(unit.tokens for unit in group),
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )
        )
    return chunks


def _units(pages: list[PageText], max_tokens: int) -> list[_Unit]:
    units: list[_Unit] = []
    cursor = 0
    started = False
    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        if started:
            cursor += 2
        units.extend(_split(text, page.page_number, cursor, max_tokens))
        cursor += len(text)
        started = True
    return units


def _split(text: str, page: int, origin: int, max_tokens: int) -> list[_Unit]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    units: list[_Unit] = []
    offset = 0
    for paragraph in paragraphs:
        found = text.find(paragraph, offset)
        start = found if found >= 0 else offset
        units.extend(_split_paragraph(paragraph, page, origin + start, max_tokens))
        offset = start + len(paragraph)
    return units


def _split_paragraph(text: str, page: int, origin: int, max_tokens: int) -> list[_Unit]:
    atoms = list(_ATOM.finditer(text))
    if not atoms:
        return []
    groups: list[list[re.Match[str]]] = []
    current: list[re.Match[str]] = []
    tokens = 0
    for match in atoms:
        cost = 1 if _token_atom(match.group()) else 0
        if current and tokens + cost > max_tokens and tokens > 0:
            groups.append(current)
            current = []
            tokens = 0
        current.append(match)
        tokens += cost
    if current:
        groups.append(current)
    units: list[_Unit] = []
    for group in groups:
        start = group[0].start()
        end = group[-1].end()
        raw = text[start:end].strip()
        count = estimate_tokens(raw)
        if raw and count:
            units.append(_Unit(page, raw, count, origin + start, origin + end))
    return units


def _pack(
    units: list[_Unit],
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> list[list[_Unit]]:
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    fresh = False
    for unit in units:
        if current and _tokens(current) + unit.tokens > max_tokens:
            if fresh:
                groups.append(current)
            current = _fit_tail(current, overlap_tokens, unit.tokens, max_tokens)
            fresh = False
        current.append(unit)
        fresh = True
        if _tokens(current) >= target_tokens and _tokens(current) > overlap_tokens:
            groups.append(current)
            current = _tail(current, overlap_tokens)
            fresh = False
    if fresh and current:
        groups.append(current)
    return groups


def _fit_tail(
    units: list[_Unit],
    overlap_tokens: int,
    next_tokens: int,
    max_tokens: int,
) -> list[_Unit]:
    tail = _tail(units, overlap_tokens)
    if tail and _tokens(tail) + next_tokens <= max_tokens:
        return tail
    return []


def _tail(units: list[_Unit], overlap_tokens: int) -> list[_Unit]:
    if overlap_tokens <= 0:
        return []
    kept: list[_Unit] = []
    count = 0
    for unit in reversed(units):
        if count >= overlap_tokens:
            break
        remaining = overlap_tokens - count
        if unit.tokens <= remaining:
            kept.append(unit)
            count += unit.tokens
            continue
        kept.append(_suffix(unit, remaining))
        break
    kept.reverse()
    return [unit for unit in kept if unit.tokens > 0]


def _suffix(unit: _Unit, tokens: int) -> _Unit:
    atoms = list(_ATOM.finditer(unit.text))
    kept: list[re.Match[str]] = []
    count = 0
    for match in reversed(atoms):
        cost = 1 if _token_atom(match.group()) else 0
        if count >= tokens and cost > 0:
            break
        kept.append(match)
        count += cost
    kept.reverse()
    if not kept:
        return unit
    start = kept[0].start()
    end = kept[-1].end()
    text = unit.text[start:end].strip()
    return _Unit(
        unit.page,
        text,
        estimate_tokens(text),
        unit.char_start + start,
        unit.char_start + end,
    )


def _tokens(units: list[_Unit]) -> int:
    return sum(unit.tokens for unit in units)


def _heading(text: str) -> str | None:
    line = text.strip()
    if not line or "\n" in line or len(line) > 40:
        return None
    if any(mark in line for mark in _HEADING_MARKS):
        return None
    return line[:120]


def _token_atom(piece: str) -> bool:
    if len(piece) == 1 and "\u4e00" <= piece <= "\u9fff":
        return True
    return piece.isalnum()
