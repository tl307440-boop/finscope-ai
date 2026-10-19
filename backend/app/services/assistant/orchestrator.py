"""回合执行器：在后台线程里跑回合，把事件放进队列，响应生成器按需取（规格 §4、§6）。

回合内容由调用方给的 `job` 生成。`job` 拿到一个 `emit`，边跑边把步骤、数据块、
正文片段推进队列，浏览器实时看到；旧的一次算完的回答则不推，结束后按已存内容重放。

要点：
- 浏览器断开只是响应生成器停了，后台线程照常跑完并落库；
- 超过心跳间隔没有事件就补一行 SSE 注释，代理不会按读超时切断；
- 名额（并发槽）在发出收尾事件（`done` / `error`）之前释放，
  客户端看到 `done` 之后立刻再提问不会被 429。
"""

import logging
import queue
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.assistant.sse import HEARTBEAT, encode, replay_events, tail_events

logger = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class TurnOutcome:
    status: str  # completed / cancelled / failed
    text: str  # 已保存的正文；cancelled / failed 为空
    # True：步骤、块、正文已经通过 emit 推给浏览器，只需再发收尾事件；
    # False：旧的一次算完的回答，结束后按 `text` 重放。
    streamed: bool = False
    as_of: date | None = None
    model: str | None = None
    grounding: str | None = None


class TurnRunner:
    def __init__(self, *, workers: int, heartbeat_seconds: float) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="assistant-turn")
        self._heartbeat = heartbeat_seconds

    def stream(
        self,
        job: Callable[[Emit], TurnOutcome],
        release: Callable[[], None],
    ) -> Generator[str]:
        """提交回合并返回事件迭代器。线程池已关闭时 `RuntimeError` 交给调用方处理。"""

        events: queue.Queue[str | None] = queue.Queue()

        def emit(payload: dict[str, Any]) -> None:
            events.put(encode(payload))

        def work() -> None:
            try:
                outcome = job(emit)
            except Exception as exc:
                # 只记异常类型：模型与上游的异常文本可能带地址，不进日志。
                logger.warning("assistant turn crashed: %s", type(exc).__name__)
                # 已经推出去的事件收不回；失败的收尾事件与重放的失败回合完全相同。
                outcome = TurnOutcome(status="failed", text="", streamed=True)
            finally:
                release()
            for line in _closing(outcome):
                events.put(line)
            events.put(None)

        self._pool.submit(work)
        return self._drain(events)

    def _drain(self, events: "queue.Queue[str | None]") -> Generator[str]:
        while True:
            try:
                item = events.get(timeout=self._heartbeat)
            except queue.Empty:
                yield HEARTBEAT
                continue
            if item is None:
                return
            yield item

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _closing(outcome: TurnOutcome) -> list[str]:
    if outcome.streamed:
        return tail_events(
            outcome.status,
            as_of=outcome.as_of,
            model=outcome.model,
            grounding=outcome.grounding,
        )
    return replay_events(outcome.text, outcome.status)
