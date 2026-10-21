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
class Passage:
    """检索到的一段原文。登记进证据时会分到一个来源编号 [n]，叙述按编号引用它。"""

    key: str  # 去重用：同一段原文在一个回合里只占一个编号
    title: str
    publisher: str
    # filing / official / reference / user
    corpus_kind: str
    published: date
    content: str
    url: str = ""
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    # 联网摘要没有被系统核对过。这类来源只在本回合展示，不写入回答记录。
    verified: bool = True


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
    # 用到了情景假设的计算结果：它的数字不是事实，只能出现在写明假设的句子里。
    hypothetical: bool = False
    # (假设名, 假设值)，按出现顺序；连环计算会把上游的假设一起带上。
    assumptions: tuple[tuple[str, str], ...] = ()
    # 检索类工具的原文片段；片段里的数字和日期只对引用了它的句子放行（见 `grounding.py`）。
    passages: tuple[Passage, ...] = ()
    # 模型整理的原文摘录表（`quote_table`）：内容已核对过，但它是模型写的，不能反过来放行叙述。
    excerpt: bool = False

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
