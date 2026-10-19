"""助手限流（规格 §16）。与 `llm_gateway` 的并发槽分开，互不占用。

- 并发：每用户同时只有 1 个回合在跑；
- 线程池：同时在跑的回合不超过后台线程数，满了直接拒绝，不排队；
- 每分钟：进程内滑动窗口；
- 每日：由调用方查库得到当天已有条数，再交给 `daily_denial` 判断（重启不清零）。

这里的判断都是纯逻辑 + 一把锁，时钟可注入，便于离线测试。
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from app.utils.market_time import SHANGHAI, shanghai_pit

_WINDOW_SECONDS = 60.0


class AssistantLimiter:
    def __init__(
        self,
        *,
        per_minute: int,
        workers: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._per_minute = per_minute
        self._workers = workers
        self._clock = clock
        self._lock = threading.Lock()
        self._active: set[UUID] = set()
        self._recent: dict[UUID, deque[float]] = {}

    def try_start(self, user_id: UUID) -> str | None:
        """通过就占住名额并返回 None；不通过返回给用户看的原因，什么都不占。"""

        with self._lock:
            if user_id in self._active:
                return "上一条回答还在生成，请等它结束，或先取消它再提问。"
            if len(self._active) >= self._workers:
                return "助手现在同时处理的提问已满，请稍后再试。"
            now = self._clock()
            window = self._recent.setdefault(user_id, deque())
            while window and now - window[0] >= _WINDOW_SECONDS:
                window.popleft()
            if len(window) >= self._per_minute:
                wait = max(1, int(_WINDOW_SECONDS - (now - window[0]) + 0.999))
                return f"一分钟内最多提问 {self._per_minute} 次，请 {wait} 秒后再试。"
            window.append(now)
            self._active.add(user_id)
            return None

    def finish(self, user_id: UUID) -> None:
        with self._lock:
            self._active.discard(user_id)

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)


def shanghai_day_start(now: datetime) -> datetime:
    """上海日历日的零点（带时区）。每日额度从这一刻起算。"""

    midnight = datetime.min.time()
    return datetime.combine(shanghai_pit(now), midnight, tzinfo=SHANGHAI).astimezone(UTC)


def daily_denial(used_today: int, limit: int) -> str | None:
    if used_today < limit:
        return None
    return f"今天的提问次数已达上限（{limit} 次），按北京时间零点恢复。"
