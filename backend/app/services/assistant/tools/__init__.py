"""工具注册表：把模型给的工具名和参数 JSON 变成 `ToolResult`（规格 §7）。

这里统一处理所有工具共有的事：参数校验、缓存、异常兜底。任何失败都变成 `unavailable`
的结果并说明原因，不抛异常，模型会照实转述；异常文本不进日志（可能带地址），只记类型。
"""

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from app.services.assistant.calc import CalcArgs, run_calc
from app.services.assistant.results import ToolResult, unavailable
from app.services.assistant.tool_cache import ToolCache, cache_key
from app.services.assistant.tools.analysis import CustomIndicatorTool, WatchlistTool
from app.services.assistant.tools.base import Tool, ToolContext, compact_schema
from app.services.assistant.tools.macro import MacroCompareTool, MacroSeriesTool
from app.services.assistant.tools.market import (
    BoardStrengthTool,
    FinancialMetricsTool,
    InstrumentSearchTool,
    PriceBarsTool,
)

logger = logging.getLogger(__name__)
MAX_ARGUMENT_CHARS = 4000


class CalcTool:
    name: ClassVar[str] = "calc"
    description: ClassVar[str] = (
        "做算术：差值、涨跌幅、比值、平均、分位、换算。事实数字只能引用本回合已有资料"
        "（资料编号 + 列 + 行）；做情景分析时可以用 assume + label 给出假设的数字。"
        "所有算术都用它，不要心算。结果会登记成新的资料，之后可以引用；"
        "用到假设的结果只能写在带「假设 / 若 / 如果」的句子里。"
    )
    args_model: ClassVar[type[BaseModel]] = CalcArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, CalcArgs)
        return run_calc(args, ctx.book, ctx.today_cn)


@dataclass(frozen=True)
class ToolOutcome:
    name: str
    args: dict[str, Any]
    result: ToolResult
    cached: bool
    millis: int


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool], cache: ToolCache | None = None) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}
        self._cache = cache

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def definitions(self) -> list[dict[str, Any]]:
        """给模型的工具列表：按名字排序、序列化方式固定，所有请求逐字节相同（前缀缓存）。"""

        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": self._tools[name].description,
                    "parameters": compact_schema(self._tools[name].args_model),
                },
            }
            for name in self.names
        ]

    def execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> ToolOutcome:
        started = time.monotonic()
        tool = self._tools.get(name)
        if tool is None:
            return self._failed(name, ctx, started, f"没有这个工具：{name[:40]}")
        parsed = _parse(raw_arguments)
        if isinstance(parsed, str):
            return self._failed(name, ctx, started, parsed, title=name)
        try:
            args = tool.args_model.model_validate(parsed)
        except ValidationError as exc:
            return self._failed(name, ctx, started, _explain(exc), title=name)
        dumped = args.model_dump(mode="json", exclude_none=True)
        key = cache_key(name, dumped, getattr(args, "as_of", None) or ctx.today_cn)
        cacheable = self._cache is not None and name != "calc"
        if cacheable and self._cache is not None:
            hit = self._cache.get(key)
            if hit is not None:
                ctx.note_result(hit)
                return ToolOutcome(name, dumped, hit, True, _millis(started))
        try:
            result = tool.run(ctx, args)
        except Exception as exc:
            logger.warning("assistant tool failed: %s %s", name, type(exc).__name__)
            return self._failed(name, ctx, started, "数据暂时读不出来，请稍后再试", dumped, name)
        if cacheable and self._cache is not None:
            self._cache.put(key, result)
        ctx.note_result(result)
        return ToolOutcome(name, dumped, result, False, _millis(started))

    @staticmethod
    def _failed(
        name: str,
        ctx: ToolContext,
        started: float,
        reason: str,
        args: dict[str, Any] | None = None,
        title: str | None = None,
    ) -> ToolOutcome:
        result = unavailable(name, title or name, ctx.today_cn, reason)
        return ToolOutcome(name, args or {}, result, False, _millis(started))


def default_registry(cache: ToolCache | None = None) -> ToolRegistry:
    return ToolRegistry(
        [
            BoardStrengthTool(),
            CalcTool(),
            CustomIndicatorTool(),
            FinancialMetricsTool(),
            InstrumentSearchTool(),
            MacroCompareTool(),
            MacroSeriesTool(),
            PriceBarsTool(),
            WatchlistTool(),
        ],
        cache,
    )


def _parse(raw: str) -> dict[str, Any] | str:
    text = raw.strip() or "{}"
    if len(text) > MAX_ARGUMENT_CHARS:
        return "参数太长"
    try:
        data = json.loads(text)
    except ValueError:
        return "参数不是合法的 JSON"
    if not isinstance(data, dict):
        return "参数必须是 JSON 对象"
    return data


def _explain(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:3]:
        where = ".".join(str(item) for item in error["loc"]) or "参数"
        message = str(error["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}：{message}")
    return "参数不合法：" + "；".join(parts)


def _millis(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
