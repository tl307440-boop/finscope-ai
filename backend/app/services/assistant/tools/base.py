"""工具的公共部分：回合上下文、时点换算、参数 schema 精简。"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import Engine

from app.providers.vintage import pit_date
from app.services.assistant.evidence import EvidenceBook
from app.services.assistant.results import ToolResult
from app.utils.market_time import shanghai_pit


@dataclass
class ToolContext:
    """一个回合里工具共享的状态。每个回合新建一份，不跨回合复用。"""

    engine: Engine
    user_id: UUID
    now: datetime
    book: EvidenceBook = field(default_factory=EvidenceBook)
    # 本回合 instrument_search 返回过的证券 id；price_bars / financial_metrics 只认这里的，
    # 防止模型自己编 id。
    searched: set[UUID] = field(default_factory=set)
    # 本回合已经联网检索的次数；上限由 `web_search` 工具在代码里卡，不靠提示词。
    web_searches: int = 0
    # 本回合搜索服务报过错（密钥、额度、网络）时的原因。有了它，本回合不再重试。
    web_failure: str | None = None

    @property
    def today_cn(self) -> date:
        """A 股与中国宏观按上海日历日。"""

        return shanghai_pit(self.now)

    @property
    def today_us(self) -> date:
        """美国序列与美股按纽约日历日。"""

        return pit_date(self.now)

    def note_result(self, result: ToolResult) -> None:
        """登记 instrument_search 的结果。缓存命中时也要登记，所以从结果行里取，不放在工具里。"""

        if result.tool != "instrument_search":
            return
        for row in result.rows:
            raw = row.get("instrument_id")
            if raw:
                try:
                    self.searched.add(UUID(raw))
                except ValueError:
                    continue


class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]

    def run(self, ctx: ToolContext, args: Any) -> ToolResult: ...


def effective_as_of(requested: date | None, today: date) -> date:
    """没给用今天；晚于今天按今天（不能看未来）。"""

    if requested is None:
        return today
    return min(requested, today)


def compact_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 的 JSON Schema 精简成各家兼容的写法。

    展开 `$ref`、去掉 `title`、可选字段不用 `anyOf`。
    """

    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    cleaned = _clean(schema, defs)
    assert isinstance(cleaned, dict)
    return cleaned


def _clean(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_clean(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        merged = {**defs.get(name, {}), **{k: v for k, v in node.items() if k != "$ref"}}
        return _clean(merged, defs)
    if "anyOf" in node:
        options = [item for item in node["anyOf"] if item.get("type") != "null"]
        if len(options) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return _clean({**options[0], **rest}, defs)
    result: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and not isinstance(value, dict):
            continue
        if key == "default" and value is None:
            continue
        result[key] = _clean(value, defs)
    return result
