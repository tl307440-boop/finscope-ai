"""回合生命周期（规格 §3）：受理、限流、两段式落库、重放。

原来 `ChatRepository.accept` 在数据库事务里同步调用模型，模型最长 30 秒，
期间一直占着连接和事务。现在拆成两段：

1. 受理：校验会话归属、重放、限流，通过后一个短事务写入「提问 + streaming 的空回答」，提交；
2. 执行：事务外在后台线程里跑应答器（`Responder`），结束时再用一个短事务定稿。
   应答器边跑边通过 `emit` 推步骤、数据块和正文；数据块推送前先追加写库，
   这样中途断开或被取消也能从库里还原已有的块。

同一个 `client_message_id` 重复提交：已定稿的原样重放（含步骤和数据块），还在生成的返回 409。
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.repositories.chat import ChatRepository, TurnExists, TurnExtras
from app.services.assistant.limits import AssistantLimiter, daily_denial, shanghai_day_start
from app.services.assistant.orchestrator import Emit, TurnOutcome, TurnRunner
from app.services.assistant.sse import replay_events
from app.services.chat_reply import StoredTurn
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class SessionNotFound(Exception):
    pass


class TurnInProgress(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AssistantConfig:
    """路由层要用的助手配置，进程启动时从 Settings 取一份。"""

    daily_limit: int
    stale_seconds: int
    # 以下三项只影响带数据工具的回答（`ASSISTANT_ENABLED` 打开时）。
    max_steps: int = 4
    turn_seconds: float = 150.0
    fast_path: bool = True


@dataclass(frozen=True)
class TurnInput:
    """一个已受理的回合：应答器只需要知道这些。会话归属在受理时已确认。"""

    user_id: UUID
    session_id: UUID
    client_message_id: UUID
    question: str


@dataclass(frozen=True)
class Reply:
    """应答器的结果。`streamed=True` 表示步骤、块、正文已经通过 `emit` 推出去了。"""

    status: str  # completed / cancelled / failed
    text: str
    streamed: bool = False
    extras: TurnExtras | None = None


# 应答器：（回合, 事件推送, 是否已被取消）→ 结果。在后台线程里调用，可以放心做慢操作。
Responder = Callable[[TurnInput, Emit, Callable[[], bool]], Reply]


class TurnService:
    def __init__(
        self,
        repository: ChatRepository,
        runner: TurnRunner,
        limiter: AssistantLimiter,
        *,
        daily_limit: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._limiter = limiter
        self._daily_limit = daily_limit
        self._clock = clock

    def start(
        self,
        user_id: UUID,
        session_id: UUID,
        client_message_id: UUID,
        question: str,
        respond: Responder,
    ) -> Iterator[str]:
        """受理一问并返回事件迭代器。

        抛 `ValueError`（问题为空）、`SessionNotFound`、`TurnInProgress`、`RateLimited`。
        """

        text = question.strip()
        if not text:
            raise ValueError("请输入问题")
        lookup = self._repository.find_turn(user_id, session_id, client_message_id)
        if lookup is None:
            raise SessionNotFound
        if lookup.turn is not None:
            return self._replay(lookup.turn)

        denial = self._admit(user_id)
        if denial is not None:
            raise RateLimited(denial)
        try:
            created = self._repository.create_turn(user_id, session_id, client_message_id, text)
        except TurnExists:
            # 两个相同的请求几乎同时到达：后到的按已有回合处理。
            self._limiter.finish(user_id)
            return self._replay_existing(user_id, session_id, client_message_id)
        except BaseException:
            self._limiter.finish(user_id)
            raise
        if not created:
            self._limiter.finish(user_id)
            raise SessionNotFound

        turn = TurnInput(user_id, session_id, client_message_id, text)

        def job(emit: Emit) -> TurnOutcome:
            return self._run(turn, respond, emit)

        try:
            return self._runner.stream(job, release=lambda: self._limiter.finish(user_id))
        except RuntimeError:
            # 后台线程池已关闭（进程正在退出）：回合不会再有人执行，直接标失败。
            self._limiter.finish(user_id)
            self._repository.finish_turn(session_id, client_message_id, status="failed", content="")
            raise

    def _admit(self, user_id: UUID) -> str | None:
        now = self._clock()
        used = self._repository.count_assistant_turns_since(user_id, shanghai_day_start(now))
        return daily_denial(used, self._daily_limit) or self._limiter.try_start(user_id)

    def _replay_existing(
        self, user_id: UUID, session_id: UUID, client_message_id: UUID
    ) -> Iterator[str]:
        lookup = self._repository.find_turn(user_id, session_id, client_message_id)
        if lookup is None:
            raise SessionNotFound
        if lookup.turn is None:  # 刚冲突又查不到：不可能，按进行中处理让客户端稍后重试
            raise TurnInProgress
        return self._replay(lookup.turn)

    @staticmethod
    def _replay(turn: StoredTurn) -> Iterator[str]:
        if turn.status == "streaming":
            raise TurnInProgress
        return iter(
            replay_events(
                turn.answer,
                turn.status,
                steps=turn.steps,
                blocks=turn.blocks,
                as_of=turn.as_of,
                model=turn.model,
                grounding=turn.grounding,
            )
        )

    def _run(self, turn: TurnInput, respond: Responder, emit: Emit) -> TurnOutcome:
        session_id, client_message_id = turn.session_id, turn.client_message_id
        try:
            reply = respond(turn, self._saving_blocks(turn, emit), self._cancel_probe(turn))
        except Exception as exc:
            # 只记异常类型：模型与上游的异常文本可能带地址，不进日志。
            logger.warning("assistant turn failed: %s", type(exc).__name__)
            failed = self._repository.finish_turn(
                session_id, client_message_id, status="failed", content=""
            )
            # 可能已经推出过事件，收尾只补 error / done，不重放。
            return TurnOutcome(status=failed, text="", streamed=True)
        status = self._repository.finish_turn(
            session_id,
            client_message_id,
            status=reply.status,
            content=reply.text,
            extras=reply.extras,
        )
        extras = reply.extras or TurnExtras()
        return TurnOutcome(
            status=status,
            text=reply.text if status == "completed" else "",
            streamed=reply.streamed,
            as_of=extras.as_of,
            model=extras.model,
            grounding=extras.grounding,
        )

    def _saving_blocks(self, turn: TurnInput, emit: Emit) -> Emit:
        """数据块先写库再推给浏览器；写库失败只记日志，不影响这一回合。"""

        def push(payload: dict[str, Any]) -> None:
            if payload.get("type") == "block":
                try:
                    self._repository.append_block(
                        turn.session_id, turn.client_message_id, payload["block"]
                    )
                except Exception as exc:
                    logger.warning("assistant block save failed: %s", type(exc).__name__)
            emit(payload)

        return push

    def _cancel_probe(self, turn: TurnInput) -> Callable[[], bool]:
        """回答行不再是 streaming（用户取消了，或残留被判失败）就算被取消。"""

        def cancelled() -> bool:
            try:
                status = self._repository.turn_status(turn.session_id, turn.client_message_id)
            except Exception as exc:
                logger.warning("assistant cancel check failed: %s", type(exc).__name__)
                return False
            return status is not None and status != "streaming"

        return cancelled
