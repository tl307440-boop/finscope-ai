"""回合执行器：在后台线程里跑回合，把事件放进队列，响应生成器按需取（规格 §4、§6）。

第 1a 期只换底座：回合内容仍由原来的 `compose` 一次算完。工具调用循环（第 1b 期）
会放进同一个后台线程里，事件协议、心跳与"断开不等于取消"都不用再改。

要点：
- 浏览器断开只是响应生成器停了，后台线程照常跑完并落库；
- 超过心跳间隔没有事件就补一行 SSE 注释，代理不会按读超时切断；
- 名额（并发槽）在发出任何事件之前释放，客户端看到 `done` 之后立刻再提问不会被 429。
"""

import logging
import queue
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.services.assistant.sse import HEARTBEAT, replay_events

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnOutcome:
    status: str  # completed / cancelled / failed
    text: str  # 已保存的正文；cancelled / failed 为空


class TurnRunner:
    def __init__(self, *, workers: int, heartbeat_seconds: float) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="assistant-turn")
        self._heartbeat = heartbeat_seconds

    def stream(
        self,
        job: Callable[[], TurnOutcome],
        release: Callable[[], None],
    ) -> Generator[str]:
        """提交回合并返回事件迭代器。线程池已关闭时 `RuntimeError` 交给调用方处理。"""

        events: queue.Queue[str | None] = queue.Queue()

        def work() -> None:
            try:
                outcome = job()
            except Exception as exc:
                # 只记异常类型：模型与上游的异常文本可能带地址，不进日志。
                logger.warning("assistant turn crashed: %s", type(exc).__name__)
                outcome = TurnOutcome(status="failed", text="")
            finally:
                release()
            for line in replay_events(outcome.text, outcome.status):
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
