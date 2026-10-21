"""技能按需加载（渐进式披露）：技能文件、目录与注册表一致、编排器只开放用得上的工具。"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from app.services.assistant.agent import NO_MORE_TOOLS, Agent, AgentRequest, AgentSettings
from app.services.assistant.llm_tools import ModelReply, ToolCall
from app.services.assistant.results import Column, ToolResult
from app.services.assistant.skills import (
    CORE_TOOLS,
    LOAD_SKILL,
    Skill,
    SkillBook,
    SkillFileError,
    parse_skill,
    read_skills,
)
from app.services.assistant.tools import CalcTool, ToolRegistry, default_registry
from app.services.assistant.tools.base import ToolContext
from pydantic import BaseModel
from sqlalchemy import create_engine

from assistant_fakes import CPI, FakeModel, FakeSeries, call, say


class BoardArgs(BaseModel):
    queries: list[str]


class FakeBoard:
    name = "industry_board"
    description = "假的板块行情"
    args_model = BoardArgs

    def __init__(self) -> None:
        self.runs = 0

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        self.runs += 1
        return ToolResult(
            tool="industry_board",
            title="板块行情：半导体",
            columns=(
                Column("name", "板块", "", "text"),
                Column("change_ytd_pct", "年初至今涨跌幅", "%", "number"),
            ),
            rows=({"name": "半导体", "change_ytd_pct": "35.2"},),
            as_of=date(2026, 10, 19),
            source="东方财富",
        )


INDUSTRY = Skill("industry", "行业与板块", "问行业时用", ("industry_board",), "先看板块。")
SCENARIO = Skill("scenario", "情景推演", "问假设时用", (), "用 assume。")
NEWS = Skill("news", "联网新闻", "问新闻时用", ("web_search",), "查新闻。")


def tool_names(tools: list[dict[str, Any]] | None) -> list[str]:
    return [item["function"]["name"] for item in tools or []]


def run(
    model: FakeModel, *, max_steps: int = 4, board: FakeBoard | None = None
) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    registry = ToolRegistry([FakeSeries(), CalcTool(), board or FakeBoard()])
    agent = Agent(
        model=model,
        registry=registry,
        settings=AgentSettings(max_steps=max_steps, turn_seconds=150.0, fast_path=False),
        tools_supported=True,
        system_prompt="PROMPT",
        skills=SkillBook([INDUSTRY, SCENARIO, NEWS], registry.names),
    )
    ctx = ToolContext(
        engine=create_engine("sqlite://"),
        user_id=uuid4(),
        now=datetime(2026, 10, 19, 2, 0, tzinfo=UTC),
    )
    result = agent.run(AgentRequest("中国半导体还有救吗"), ctx, events.append, lambda: False)
    return result, events


def load(*names: str) -> ModelReply:
    return call((LOAD_SKILL, {"names": list(names)}))


# ---------------------------------------------------------------- 技能文件
def test_shipped_skill_files_parse_and_name_only_real_tools() -> None:
    skills = read_skills()
    registry = default_registry(corpus_min_rank=0.0)
    known = set(registry.names)
    assert {"industry", "company", "news", "documents", "scenario"} <= {s.name for s in skills}
    for skill in skills:
        assert set(skill.tools) <= known, f"{skill.name} 写了不存在的工具"
        assert skill.guide and skill.summary
    reachable = set(CORE_TOOLS) | {tool for skill in skills for tool in skill.tools}
    assert known <= reachable, f"这些工具既不是核心工具也不在任何技能里：{known - reachable}"


def test_skill_guides_carry_no_threshold_numbers_the_grounder_would_block() -> None:
    """技能说明里的阈值数字会被模型抄进回答，而资料里没有它，整句会被拦下。"""

    for skill in read_skills():
        assert "大于 1" not in skill.guide and "超过 50%" not in skill.guide


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("name: x", "第一行必须是 ---"),
        ("---\nname: x\ntitle: y\nsummary: z\n", "没有用 --- 结束"),
        ("---\nname: x\ntitle: y\n---\n正文", "缺 summary"),
        ("---\nname: x\ntitle: y\nsummary: z\nextra: 1\n---\n正文", "看不懂"),
        ("---\nname: x\ntitle: y\nsummary: z\n---\n", "没有正文"),
    ],
)
def test_broken_skill_files_fail_loudly(text: str, reason: str) -> None:
    with pytest.raises(SkillFileError, match=reason):
        parse_skill(text)


def test_skill_file_head_and_tools_are_parsed(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "---\nname: a\ntitle: 甲\nsummary: 用途\ntools: calc, price_bars\n---\n做法\n",
        encoding="utf-8",
    )
    (skill,) = read_skills(tmp_path)
    assert skill == Skill("a", "甲", "用途", ("calc", "price_bars"), "做法")


def test_skill_book_drops_skills_whose_tools_are_all_missing() -> None:
    book = SkillBook([INDUSTRY, SCENARIO, NEWS], ["industry_board", "calc", "price_bars"])
    assert book.names == ("industry", "scenario")  # 没有 web_search：news 不出现
    assert book.core == ("calc", "price_bars")
    definition = book.definition()["function"]
    assert definition["name"] == LOAD_SKILL
    assert definition["parameters"]["properties"]["names"]["items"]["enum"] == [
        "industry",
        "scenario",
    ]
    assert "industry（行业与板块）：问行业时用" in definition["description"]
    assert "news" not in definition["description"]


# ---------------------------------------------------------------- 编排器
def test_first_call_offers_only_core_tools_and_load_skill() -> None:
    model = FakeModel(say("好。"))
    run(model)
    assert tool_names(model.calls[0][1]) == ["calc", "macro_series", LOAD_SKILL]


def test_loading_a_skill_returns_its_guide_and_opens_its_tools() -> None:
    board = FakeBoard()
    model = FakeModel(
        load("industry"),
        call(("industry_board", {"queries": ["半导体"]})),
        say("半导体年初至今涨了 35.2%。"),
    )
    result, events = run(model, board=board)
    assert (result.status, result.grounding) == ("completed", "full")
    assert board.runs == 1
    guide = model.calls[1][0][-1]
    assert guide["role"] == "tool" and '<技能 name="industry"' in guide["content"]
    assert (
        "先看板块。" in guide["content"] and "现在可以用的工具：industry_board" in guide["content"]
    )
    assert "industry_board" in tool_names(model.calls[1][1])
    assert "industry_board" not in tool_names(model.calls[0][1])
    assert result.steps[0] == {"id": "s1", "label": "加载技能：行业与板块", "status": "ok"}
    assert result.trace["skills"] == ["industry"]
    assert result.trace["calls"][0]["tool"] == LOAD_SKILL
    # 加载技能不登记证据：板块行情仍是 E1。
    assert all(block.get("evidence") == "E1" for block in result.blocks if "evidence" in block)
    assert [e["label"] for e in events if e["type"] == "step"][0] == "加载技能…"


def test_skill_only_rounds_do_not_use_up_the_step_budget() -> None:
    model = FakeModel(load("industry"), call(CPI), say("最新是 323.0。"))
    result, _events = run(model, max_steps=1)
    assert result.text == "最新是 323.0。"
    assert model.calls[1][1] is not None  # 加载技能那一轮没占步数，还能查数据
    assert model.calls[2][1] is None


def test_skill_rounds_are_capped() -> None:
    model = FakeModel(load("industry"), load("scenario"), load("industry"), say("好。"))
    run(model, max_steps=1)
    assert [tools is None for _messages, tools in model.calls] == [False, False, False, True]


def test_unknown_skill_is_reported_back_with_the_catalog() -> None:
    model = FakeModel(load("astrology"), say("好。"))
    result, _events = run(model)
    told = model.calls[1][0][-1]["content"]
    assert "没有这个技能：astrology" in told and "industry、scenario" in told
    assert result.steps[0]["status"] == "unavailable"
    assert result.trace["skills"] == []


def test_loading_twice_does_not_repeat_the_guide() -> None:
    model = FakeModel(load("industry"), load("industry"), say("好。"))
    run(model)
    second = model.calls[2][0][-1]["content"]
    assert "本回合已经加载过" in second and "先看板块。" not in second


def test_tool_called_without_loading_its_skill_still_runs_and_stays_open() -> None:
    board = FakeBoard()
    model = FakeModel(call(("industry_board", {"queries": ["半导体"]})), say("好。"))
    run(model, board=board)
    assert board.runs == 1
    assert "industry_board" in tool_names(model.calls[1][1])


def test_load_skill_does_not_take_a_parallel_slot() -> None:
    model = FakeModel(call((LOAD_SKILL, {"names": ["industry"]}), CPI, CPI, CPI), say("好。"))
    result, _events = run(model)
    tool_texts = [m["content"] for m in model.calls[1][0] if m["role"] == "tool"]
    assert len(tool_texts) == 4 and all("忽略" not in text for text in tool_texts)
    assert len(result.trace["calls"]) == 4


def test_tool_call_written_into_the_final_answer_gets_one_more_nudge() -> None:
    dsml = ModelReply(content="", tool_calls=(ToolCall("dsml_0", "calc", json.dumps({})),))
    model = FakeModel(call(CPI), dsml, say("最新是 323.0。"))
    result, _events = run(model, max_steps=1)
    assert result.text == "最新是 323.0。"
    nudge = model.calls[2][0][-1]
    assert nudge == {"role": "system", "content": NO_MORE_TOOLS}
    assert model.calls[2][1] is None


def test_failed_calc_is_told_to_the_model_but_not_shown_to_the_reader() -> None:
    bad = ("calc", {"op": "diff", "inputs": [{"evidence": "E1", "column": "收盘"}]})
    model = FakeModel(call(CPI), call(bad), say("最新是 323.0。"))
    result, _events = run(model)
    assert "没有数字列 收盘" in model.calls[2][0][-1]["content"]
    assert not [b for b in result.blocks if b["type"] == "notice"]
