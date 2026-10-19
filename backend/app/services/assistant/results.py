"""工具结果的数据结构（规格 §7）。纯数据，不碰数据库和网络。

数字一律用字符串保存并保留入库精度：回答里的数字要和这里逐位比对（见 `grounding.py`），
不能先转成浮点再丢精度。
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

ColumnKind = Literal["date", "number", "text"]
ResultStatus = Literal["ok", "stale", "unavailable"]


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    unit: str = ""
    kind: ColumnKind = "text"
    # False：只给模型看（例如证券 id），不进渲染块。
    visible: bool = True


@dataclass(frozen=True)
class ToolResult:
    tool: str
    title: str
    columns: tuple[Column, ...]
    rows: tuple[dict[str, str | None], ...]
    as_of: date
    source: str
    status: ResultStatus = "ok"
    reason: str | None = None
    truncated: int = 0
    # 时间序列里要画线 / 出指标卡的那一列；为空表示按表格呈现。
    primary: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "unavailable"


def unavailable(tool: str, title: str, as_of: date, reason: str, source: str = "") -> ToolResult:
    """没取到数据时的统一结果：说明原因，不给任何行。"""

    return ToolResult(
        tool=tool,
        title=title,
        columns=(),
        rows=(),
        as_of=as_of,
        source=source,
        status="unavailable",
        reason=reason,
    )
