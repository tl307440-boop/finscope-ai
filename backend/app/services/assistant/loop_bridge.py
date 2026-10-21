"""同步工具调用异步行情服务的桥。

助手的回合在后台线程里跑，工具是同步函数；东财行情、涨停池、研报这些服务是异步的，
缓存、限速和长连接都绑在应用的主事件循环上。这里把协程放回主事件循环去执行，
线程只等结果：助手和页面共用同一份缓存与限速，不会为一次工具调用另起事件循环、另建连接。

没有登记主事件循环时（单元测试、脚本）退回 `asyncio.run`。
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable

from app.providers.errors import ProviderError

DEFAULT_TIMEOUT_SECONDS = 25.0

_lock = threading.Lock()
_main: asyncio.AbstractEventLoop | None = None


def bind_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """应用启动时登记主事件循环，关闭时传 None。"""

    global _main
    with _lock:
        _main = loop


def call_async[T](
    factory: Callable[[], Awaitable[T]], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> T:
    """在线程里等一个异步调用的结果；超时按上游超时处理，并取消还没跑完的协程。"""

    async def run() -> T:
        return await factory()

    with _lock:
        loop = _main
    if loop is None or not loop.is_running():
        return asyncio.run(run())
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:  # 在事件循环线程里同步等它自己，会死锁
        raise RuntimeError("call_async 不能在事件循环线程里调用")
    future = asyncio.run_coroutine_threadsafe(run(), loop)
    try:
        return future.result(timeout)
    except TimeoutError as error:
        future.cancel()
        raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504) from error
