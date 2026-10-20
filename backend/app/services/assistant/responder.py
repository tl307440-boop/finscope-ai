"""助手应答器：灰度开关、模型客户端和编排器的装配（规格 §6、§17）。

`ASSISTANT_ENABLED` 关着（默认），或者没配模型，走原来的固定说明 / 白名单宏观转述，
行为与 1a 完全一样；打开且配了模型才进入带数据工具的编排器。开关每个回合读一次
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
    entity_notes,
)
from app.services.assistant.llm_tools import OpenAIToolModel, supports_tools
from app.services.assistant.orchestrator import Emit
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.turns import AssistantConfig, Reply, TurnInput
from app.services.llm_gateway import model_availability
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (Path(__file__).resolve().parents[2] / "prompts" / "assistant_v3.txt").read_text(
    encoding="utf-8"
)
HISTORY_TURNS = 3  # 最近 3 个回合 = 6 条消息
HISTORY_ANSWER_CHARS = 1200


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
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._registry = registry
        self._config = config
        self._legacy = legacy
        self._clock = clock
        self._load_settings = load_settings

    def __call__(self, turn: TurnInput, emit: Emit, cancelled: Callable[[], bool]) -> Reply:
        settings = self._load_settings(self._engine)
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
        request = AgentRequest(
            question=turn.question,
            history=_history_messages(history),
            entities=entity_notes([item.trace for item in history if isinstance(item.trace, dict)]),
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


def _history_messages(history: Sequence[HistoryTurn]) -> tuple[HistoryMessage, ...]:
    """旧回合只带文字，不带工具结果；没有文字回答的回合（只给了数据）整对跳过。"""

    messages: list[HistoryMessage] = []
    for item in history:
        answer = item.answer.strip()
        if not answer:
            continue
        messages.append(HistoryMessage("user", item.question))
        messages.append(HistoryMessage("assistant", _clip(answer)))
    return tuple(messages)


def _clip(text: str) -> str:
    return text if len(text) <= HISTORY_ANSWER_CHARS else text[:HISTORY_ANSWER_CHARS] + "…"


__all__ = ["SYSTEM_PROMPT", "AssistantResponder"]
