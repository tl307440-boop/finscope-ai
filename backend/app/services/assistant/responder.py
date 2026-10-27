"""助手应答器：灰度开关、模型客户端和编排器的装配（规格 §6、§17）。

`ASSISTANT_ENABLED` 关着（默认），或者没配模型，走原来的固定说明 / 白名单宏观转述，
行为与 1a 完全一样；打开且配了模型才进入带数据工具的编排器。带走势图的回合例外：
不论开关是否打开，都按 chart 技能读图。开关每个回合读一次
（运行时设置，管理员在界面上改了 5 秒内生效），不用重启，出问题随时关掉。

整个装配在后台线程里做：能力探测可能要发一次模型请求（按 (base_url, model) 缓存），
不能堵在受理请求的线程上。
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import Engine

from app.core.config import Settings
from app.repositories.chat import ChatRepository, HistoryTurn, TurnExtras
from app.services.assistant.agent import (
    Agent,
    AgentRequest,
    AgentSettings,
    HistoryMessage,
    carry_tools,
    entity_notes,
    query_notes,
)
from app.services.assistant.chart_image import save_chart
from app.services.assistant.chart_skill import (
    CHART_HISTORY,
    NO_MODEL_TEXT,
    ChartOutcome,
    load_chart_skill,
    read_chart,
    skill_notice,
    speak_note,
)
from app.services.assistant.llm_tools import OpenAIToolModel, ToolModel, supports_tools
from app.services.assistant.orchestrator import Emit
from app.services.assistant.skills import SkillBook, read_skills
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.turns import AssistantConfig, Reply, TurnInput
from app.services.llm_gateway import model_availability
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / "assistant_v4.txt").read_text(
    encoding="utf-8"
)
SKILLS = read_skills()
HISTORY_TURNS = 3  # 最近 3 个回合 = 6 条消息
HISTORY_ANSWER_CHARS = 1200
# 旧回合没有文字回答（回答没通过核对，只给了数据表）时，历史里用这句占位，让模型知道问过什么。
NO_TEXT_HISTORY = "（这一轮没有文字回答，只展示了查到的数据，见「上文查过的数据」。）"


class AssistantResponder:
    def __init__(
        self,
        *,
        engine: Engine,
        repository: ChatRepository,
        registry: ToolRegistry,
        config: AssistantConfig,
        legacy: Callable[[str], str],
        clock: Callable[[], datetime] = utc_now,
        load_settings: Callable[[Engine], Settings] = effective_settings,
        chart_root: Path = Path("data/uploads"),
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._registry = registry
        self._config = config
        self._legacy = legacy
        self._clock = clock
        self._load_settings = load_settings
        self._chart_root = chart_root

    def __call__(self, turn: TurnInput, emit: Emit, cancelled: Callable[[], bool]) -> Reply:
        settings = self._load_settings(self._engine)
        if turn.chart is not None:
            return self._read_chart(settings, turn, emit, cancelled)
        available, _message = model_availability(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base_url,
            model=settings.llm_model,
        )
        if not settings.assistant_enabled or not available:
            return Reply(status="completed", text=self._legacy(turn.question))
        return self._answer(settings, turn, emit, cancelled)

    def _answer(
        self, settings: Settings, turn: TurnInput, emit: Emit, cancelled: Callable[[], bool]
    ) -> Reply:
        history = self._repository.recent_turns(
            turn.session_id, turn.client_message_id, HISTORY_TURNS
        )
        traces = [item.trace for item in history if isinstance(item.trace, dict)]
        request = AgentRequest(
            question=turn.question,
            history=_history_messages(history),
            entities=entity_notes(traces),
            queried=query_notes(traces),
            carry_tools=carry_tools(traces),
        )
        ctx = ToolContext(engine=self._engine, user_id=turn.user_id, now=self._clock())
        with httpx.Client() as client:
            model = OpenAIToolModel(
                base_url=settings.llm_api_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                client=client,
            )
            agent = Agent(
                model=model,
                registry=self._registry,
                settings=AgentSettings(
                    max_steps=self._config.max_steps,
                    turn_seconds=self._config.turn_seconds,
                    fast_path=self._config.fast_path,
                ),
                tools_supported=supports_tools(model, settings.llm_api_base_url),
                system_prompt=SYSTEM_PROMPT,
                skills=SkillBook(SKILLS, self._registry.names),
            )
            result = agent.run(request, ctx, emit, cancelled)
        return Reply(
            status=result.status,
            text=result.text,
            streamed=True,
            extras=TurnExtras(
                blocks=result.blocks,
                steps=result.steps,
                as_of=result.as_of,
                model=result.model,
                grounding=result.grounding,
                tool_trace=result.trace,
                sources=result.sources,
            ),
        )

    def _read_chart(
        self, settings: Settings, turn: TurnInput, emit: Emit, cancelled: Callable[[], bool]
    ) -> Reply:
        """有图就走技能读图，不看 ASSISTANT_ENABLED，也不把图交给旧的宏观转述。"""

        chart = turn.chart
        if chart is None:
            return Reply(status="failed", text="", streamed=True)
        try:
            save_chart(self._chart_root, turn.user_id, turn.client_message_id, chart)
        except OSError:
            logger.warning("assistant chart save failed")
            text, stopped = speak_note("走势图没有保存成功，这次没有开始解读。", emit, cancelled)
            return Reply(status="cancelled" if stopped else "completed", text=text, streamed=True)
        vision_name = settings.llm_vision_model.strip() or settings.llm_model
        available, _message = model_availability(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base_url,
            model=vision_name,
        )
        try:
            skill = load_chart_skill()
        except (OSError, ValueError):
            logger.warning("assistant chart skill missing")
            text, stopped = speak_note(
                "走势解读技能没有加载成功，这次没有生成分析。", emit, cancelled
            )
            return Reply(status="cancelled" if stopped else "completed", text=text, streamed=True)
        if not available:
            notice = skill_notice(skill.name)
            emit({"type": "block", "block": notice})
            text, stopped = speak_note(NO_MODEL_TEXT, emit, cancelled)
            return _chart_reply(
                ChartOutcome(
                    "cancelled" if stopped else "completed",
                    text,
                    [notice],
                    None,
                    {"mode": "chart_reading", "skill": skill.name, "model": ""},
                )
            )
        with httpx.Client() as client:
            outcome = read_chart(
                self._open_model(settings, vision_name.strip(), client),
                skill=skill,
                question=turn.question,
                image=chart,
                emit=emit,
                cancelled=cancelled,
            )
        return _chart_reply(outcome)

    def _open_model(self, settings: Settings, model_name: str, client: httpx.Client) -> ToolModel:
        return OpenAIToolModel(
            base_url=settings.llm_api_base_url,
            api_key=settings.llm_api_key,
            model=model_name,
            client=client,
        )


def _chart_reply(outcome: ChartOutcome) -> Reply:
    return Reply(
        status=outcome.status,
        text=outcome.text,
        streamed=True,
        extras=TurnExtras(
            blocks=outcome.blocks,
            model=outcome.model,
            tool_trace=outcome.trace,
        ),
    )


def _history_messages(history: Sequence[HistoryTurn]) -> tuple[HistoryMessage, ...]:
    """旧回合只带文字，不带工具结果。

    没有文字回答的回合（只给了数据）也要留下：不然追问「这些票」时，模型看不到上一问，
    会说「上一轮没有给出过名单」。它查了什么由 `query_notes` 另行告诉模型。
    """

    messages: list[HistoryMessage] = []
    for item in history:
        messages.append(HistoryMessage("user", item.question))
        # 图上目测的价位不是入库资料，不能被后面的回合当成可以引用的数字。
        if isinstance(item.trace, dict) and item.trace.get("mode") == "chart_reading":
            messages.append(HistoryMessage("assistant", CHART_HISTORY))
            continue
        answer = item.answer.strip()
        messages.append(HistoryMessage("assistant", _clip(answer) if answer else NO_TEXT_HISTORY))
    return tuple(messages)


def _clip(text: str) -> str:
    return text if len(text) <= HISTORY_ANSWER_CHARS else text[:HISTORY_ANSWER_CHARS] + "…"


__all__ = ["SYSTEM_PROMPT", "AssistantResponder"]
