"""助手测试共用的假模型与假数据工具（编排器单元测试、回合集成测试都用）。"""

import json
from datetime import date
from typing import Any

from app.services.assistant.llm_tools import ModelReply, ToolCall
from app.services.assistant.results import Column, Passage, ToolResult
from app.services.assistant.tools.base import ToolContext
from pydantic import BaseModel

ROWS = (
    {"obs_date": "2026-06-01", "value": "320.1"},
    {"obs_date": "2026-07-01", "value": "321.5"},
    {"obs_date": "2026-08-01", "value": "323.0"},
)


class SeriesArgs(BaseModel):
    code: str


class FakeSeries:
    """假的 `macro_series`：永远返回同一条三行的 CPI 序列。"""

    name = "macro_series"
    description = "假的宏观序列"
    args_model = SeriesArgs

    def __init__(self) -> None:
        self.runs = 0

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        self.runs += 1
        return ToolResult(
            tool="macro_series",
            title="美国 CPI",
            columns=(
                Column("obs_date", "日期", "", "date"),
                Column("value", "指数", "", "number"),
            ),
            rows=ROWS,
            as_of=date(2026, 8, 1),
            source="FRED",
            primary="value",
        )


class CorpusArgs(BaseModel):
    keywords_zh: list[str]


class FakeCorpus:
    """假的 `corpus_search`：永远返回同两段原文（用户上传的调研纪要）。"""

    name = "corpus_search"
    description = "假的语料检索"
    args_model = CorpusArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        return ToolResult(
            tool="corpus_search",
            title="良率",
            columns=(),
            rows=(),
            as_of=date(2026, 9, 30),
            source="本系统语料库",
            passages=(
                Passage(
                    key="chunk-a",
                    title="调研纪要.pdf",
                    publisher="你上传的文档",
                    corpus_kind="user",
                    published=date(2026, 9, 30),
                    content="管理层表示，三季度良率提升到 92%，产能利用率维持高位。",
                    page_start=2,
                    page_end=2,
                ),
                Passage(
                    key="chunk-b",
                    title="调研纪要.pdf",
                    publisher="你上传的文档",
                    corpus_kind="user",
                    published=date(2026, 9, 30),
                    content="公司计划在 2027 年新增两条产线。",
                ),
            ),
        )


CORPUS = ("corpus_search", {"keywords_zh": ["良率"]})


class FakeModel:
    """按剧本回复的模型：剧本每一项是 `ModelReply`、异常，或 `(messages, tools) -> ModelReply`。"""

    model_name = "fake-1"

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        timeout: float,
    ) -> ModelReply:
        self.calls.append((json.loads(json.dumps(messages)), tools))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages, tools)  # type: ignore[no-any-return]
        return step  # type: ignore[no-any-return]


def say(text: str) -> ModelReply:
    return ModelReply(content=text, usage={"total_tokens": 5})


def call(*items: tuple[str, dict[str, Any]]) -> ModelReply:
    return ModelReply(
        content="",
        tool_calls=tuple(
            ToolCall(f"c{index}", name, json.dumps(args))
            for index, (name, args) in enumerate(items)
        ),
    )


CPI = ("macro_series", {"code": "CPIAUCSL"})
DIFF_LAST_TWO = (
    "calc",
    {
        "op": "diff",
        "inputs": [
            {"evidence": "E1", "column": "value", "row": -1},
            {"evidence": "E1", "column": "value", "row": -2},
        ],
    },
)
