"""新数据工具走完整个回合：按需加载 hotspot 技能、数字对得上资料才放行、快讯标题按来源引用。

模型用剧本假实现；`market_sentiment`、`news_flash` 用真工具加假数据服务；
`calc` 与 grounding 用真的。
"""

from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from app.schemas.industry_market import Section
from app.schemas.market_sentiment import CountItem, LadderStep, LimitUpView, PoolStockItem
from app.services.assistant.agent import Agent, AgentRequest, AgentResult, AgentSettings
from app.services.assistant.skills import LOAD_SKILL, SkillBook, read_skills
from app.services.assistant.tools import CalcTool, ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.flash import NewsFlashTool
from app.services.assistant.tools.sentiment import MarketSentimentTool
from app.services.market_cache import TtlCache
from app.services.market_sentiment import MarketSentimentService
from app.services.news_flash import NewsFlashService
from sqlalchemy import create_engine

from assistant_fakes import FakeModel, call, say
from live_fakes import ScriptedGateway, as_json

NOW = datetime(2026, 10, 21, 14, 30, tzinfo=UTC)  # 上海 22:30
DAY = date(2026, 10, 21)


class FakeSentiment:
    async def limit_up(self, day: date | None = None) -> LimitUpView:
        return LimitUpView(
            section=Section(status="ok"),
            trade_date=DAY,
            limit_up=58,
            broken=21,
            limit_down=3,
            seal_rate=None,
            max_streak=5,
            ladder=[LadderStep(streak=5, stocks=[PoolStockItem(code="000001", name="甲股份")])],
            industries=[CountItem(label="半导体", count=9)],
            seal_times=[],
            limit_up_stocks=[],
            broken_stocks=[],
            limit_down_stocks=[],
            rules=[],
            sources=[],
        )


FLASHES = as_json(
    {
        "code": "1",
        "data": {
            "fastNewsList": [
                {
                    "code": "F1",
                    "showTime": "2026-10-21 22:10:05",
                    "title": "半导体设备板块拉升 北方华创涨超5%",
                }
            ]
        },
    }
)


def _run(model: FakeModel) -> tuple[AgentResult, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    flash = NewsFlashService(
        gateway=ScriptedGateway({"getFastNewsList": FLASHES}), cache=TtlCache()
    )
    registry = ToolRegistry(
        [
            CalcTool(),
            MarketSentimentTool(cast(MarketSentimentService, FakeSentiment())),
            NewsFlashTool(flash),
        ]
    )
    agent = Agent(
        model=model,
        registry=registry,
        settings=AgentSettings(max_steps=4, turn_seconds=150.0, fast_path=False),
        tools_supported=True,
        system_prompt="PROMPT",
        skills=SkillBook(read_skills(), registry.names),
    )
    ctx = ToolContext(engine=create_engine("sqlite://"), user_id=uuid4(), now=NOW)
    result = agent.run(AgentRequest("现在市场最热的方向是什么"), ctx, events.append, lambda: False)
    return result, events


LOAD = call((LOAD_SKILL, {"names": ["hotspot"]}))
SENTIMENT = ("market_sentiment", {"view": "limit_up"})
FLASH = ("news_flash", {"keyword": "半导体"})


def test_the_hotspot_skill_opens_only_the_tools_that_exist() -> None:
    model = FakeModel(LOAD, say("好。"))
    _run(model)
    guide = model.calls[1][0][-1]["content"]
    assert '<技能 name="hotspot"' in guide
    assert "现在可以用的工具：market_sentiment、news_flash" in guide  # 注册表里没有的不开放


def test_numbers_from_the_pool_and_a_cited_headline_pass_grounding() -> None:
    text = "涨停 58 家，最高 5 连板，半导体方向有 9 家涨停。据东方财富快讯，半导体设备板块拉升[1]。"
    model = FakeModel(LOAD, call(SENTIMENT, FLASH), say(text))
    result, events = _run(model)
    assert (result.status, result.grounding, result.text) == ("completed", "full", text)
    assert result.trace["skills"] == ["hotspot"]
    labels = [event["label"] for event in events if event["type"] == "step"]
    assert any(label.startswith("查市场热度") for label in labels)
    assert any(label.startswith("查实时快讯") for label in labels)
    # 角标要有来源可点，但没核实的标题只在本回合展示，不写进回答记录
    shown = [item for e in events if e["type"] == "sources" for item in e["sources"]]
    assert [(s["n"], s["kind"], s["verified"]) for s in shown] == [(1, "web", False)]
    assert shown[0]["url"] == "https://finance.eastmoney.com/a/F1.html"
    assert result.sources == []


def test_a_number_that_is_in_no_evidence_is_blocked_and_rewritten() -> None:
    model = FakeModel(
        LOAD,
        call(SENTIMENT),
        say("今天涨停 99 家。"),
        say("请看下方的涨停与情绪表。"),
    )
    result, events = _run(model)
    assert result.trace["repaired"] is True and result.trace["unmatched"] == ["99"]
    assert "".join(e["delta"] for e in events if e["type"] == "delta") == "请看下方的涨停与情绪表。"


def test_a_headline_number_needs_the_citation_on_the_same_sentence() -> None:
    uncited = "北方华创涨超 5%。"
    model = FakeModel(
        LOAD, call(FLASH), say(uncited), say("半导体设备板块拉升，北方华创涨超 5%[1]。")
    )
    result, _ = _run(model)
    assert result.trace["repaired"] is True and result.trace["unmatched"] == ["5%"]
    assert result.text == "半导体设备板块拉升，北方华创涨超 5%[1]。"


@pytest.mark.parametrize("name", ["market_sentiment", "news_flash"])
def test_the_new_tools_are_in_the_step_labels(name: str) -> None:
    from app.services.assistant.agent import STEP_LABELS

    assert name in STEP_LABELS
