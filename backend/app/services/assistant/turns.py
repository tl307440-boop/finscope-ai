"""回合生命周期（规格 §3）：受理、限流、两段式落库、重放。

原来 `ChatRepository.accept` 在数据库事务里同步调用模型，模型最长 30 秒，
期间一直占着连接和事务。现在拆成两段：

1. 受理：校验会话归属、重放、限流，通过后一个短事务写入「提问 + streaming 的空回答」，提交；
2. 执行：事务外在后台线程里跑 `compose`，结束时再用一个短事务定稿。

同一个 `client_message_id` 重复提交：已定稿的原样重放，还在生成的返回 409。
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.chat import ChatRepository, TurnExists
from app.services.assistant.limits import AssistantLimiter, daily_denial, shanghai_day_start
from app.services.assistant.orchestrator import TurnOutcome, TurnRunner
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
        compose: Callable[[str], str],
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

        def job() -> TurnOutcome:
            return self._run(session_id, client_message_id, text, compose)

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
        return iter(replay_events(turn.answer, turn.status))

    def _run(
        self,
        session_id: UUID,
        client_message_id: UUID,
        question: str,
        compose: Callable[[str], str],
    ) -> TurnOutcome:
        try:
            answer = compose(question)
        except Exception as exc:
            # 只记异常类型：模型与上游的异常文本可能带地址，不进日志。
            logger.warning("assistant compose failed: %s", type(exc).__name__)
            failed = self._repository.finish_turn(
                session_id, client_message_id, status="failed", content=""
            )
            return TurnOutcome(status=failed, text="")
        status = self._repository.finish_turn(
            session_id, client_message_id, status="completed", content=answer
        )
        return TurnOutcome(status=status, text=answer if status == "completed" else "")
