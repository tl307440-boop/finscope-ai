"""证据登记与给模型的资料文本（规格 §8）。纯函数，不碰数据库和网络。

每个工具结果登记为 E1、E2……全回合统一编号。叙述里的数字、日期都只能来自这里。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.assistant.results import Column, ToolResult

# 回填给模型的最大行数；更多时给首尾各一半并注明省略了多少行（规格 §6）。
MAX_RENDER_ROWS = 60
_HALF = MAX_RENDER_ROWS // 2
_ID = re.compile(r"^E(\d{1,3})$")


@dataclass(frozen=True)
class Evidence:
    id: str
    result: ToolResult


class EvidenceBook:
    """一个回合里已登记的证据。"""

    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def add(self, result: ToolResult) -> Evidence:
        item = Evidence(id=f"E{len(self._items) + 1}", result=result)
        self._items.append(item)
        return item

    def get(self, evidence_id: str) -> Evidence | None:
        if _ID.match(evidence_id) is None:
            return None
        for item in self._items:
            if item.id == evidence_id:
                return item
        return None

    def __iter__(self) -> Iterator[Evidence]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def numbers(self) -> list[Decimal]:
        """所有证据里数字列的值。叙述里的数字必须能在这里找到。"""

        found: list[Decimal] = []
        for item in self._items:
            numeric = [column.key for column in item.result.columns if column.kind == "number"]
            for row in item.result.rows:
                for key in numeric:
                    value = to_decimal(row.get(key))
                    if value is not None:
                        found.append(value)
        return found

    def dates(self) -> set[date]:
        """日期列里的日期，加上每条证据的 as_of。叙述里的日期按这个放行。"""

        found: set[date] = set()
        for item in self._items:
            found.add(item.result.as_of)
            keys = [column.key for column in item.result.columns if column.kind == "date"]
            for row in item.result.rows:
                for key in keys:
                    parsed = to_date(row.get(key))
                    if parsed is not None:
                        found.add(parsed)
        return found

    def name_text(self) -> str:
        """证据标题、列名、来源拼成的文字。序列名里的数字（「10年期」「沪深300」）按这里放行。"""

        parts: list[str] = []
        for item in self._items:
            parts.extend((item.result.title, item.result.source))
            parts.extend(f"{column.label}{column.unit}" for column in item.result.columns)
        return "\n".join(parts)


def to_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value.strip())
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def to_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def render(item: Evidence) -> str:
    """给模型看的资料文本：表头 + 行，最多 60 行；更多时首尾各 30 行并注明省略行数。"""

    result = item.result
    head = (
        f'<资料 id="{item.id}" 标题="{_attr(result.title)}" 来源="{_attr(result.source)}" '
        f'截止="{result.as_of.isoformat()}" 状态="{result.status}">'
    )
    if result.status == "unavailable":
        return f"{head}\n没有数据：{_clean(result.reason or '原因未知', 200)}\n</资料>"
    lines = [head, " | ".join(_header(column) for column in result.columns)]
    rows = result.rows
    omitted = 0
    if len(rows) > MAX_RENDER_ROWS:
        omitted = len(rows) - MAX_RENDER_ROWS
        shown = [*rows[:_HALF], None, *rows[-_HALF:]]
    else:
        shown = list(rows)
    for row in shown:
        if row is None:
            lines.append(f"…（中间省略 {omitted} 行）")
            continue
        lines.append(" | ".join(_clean(row.get(column.key) or "") for column in result.columns))
    if result.truncated:
        # 日线只记「被截断了」（1），其余工具记确切行数，所以说「至少」。
        lines.append(f"（更早的数据没有取完，至少还有 {result.truncated} 行未取）")
    if result.status == "stale" and result.reason:
        lines.append(f"注意：{_clean(result.reason, 200)}")
    lines.append("</资料>")
    return "\n".join(lines)


def _header(column: Column) -> str:
    return f"{column.label}({column.unit})" if column.unit else column.label


# 资料里的文字只是数据：不能换行、不能带尖括号（否则能伪造 </资料> 跳出数据块）、不能带列分隔符。
_INERT = str.maketrans({"<": "‹", ">": "›", "|": "｜", "\n": " ", "\r": " ", '"': "'"})
MAX_CELL_CHARS = 120


def _clean(text: str, limit: int = MAX_CELL_CHARS) -> str:
    return text.translate(_INERT)[:limit]


def _attr(text: str) -> str:
    return _clean(text)
