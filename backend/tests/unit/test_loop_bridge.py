"""同步工具调用异步服务的桥：在主事件循环上执行、没有主循环时的退路、超时、死锁保护。"""

import asyncio
import threading
from collections.abc import Iterator

import pytest
from app.providers.errors import ProviderError
from app.services.assistant import loop_bridge


@pytest.fixture
def main_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """另一个线程里跑着的「应用主事件循环」，和测试线程分开，像真实的回合线程那样。"""

    loop = asyncio.new_event_loop()
    started = threading.Event()

    def serve() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(started.set)
        loop.run_forever()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert started.wait(5)
    loop_bridge.bind_main_loop(loop)
    yield loop
    loop_bridge.bind_main_loop(None)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)
    loop.close()


def test_the_coroutine_runs_on_the_bound_main_loop(main_loop: asyncio.AbstractEventLoop) -> None:
    async def which_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    assert loop_bridge.call_async(which_loop) is main_loop  # 共用主循环：缓存和限速才共用


def test_without_a_bound_loop_it_falls_back_to_a_private_loop() -> None:
    loop_bridge.bind_main_loop(None)

    async def answer() -> int:
        await asyncio.sleep(0)
        return 42

    assert loop_bridge.call_async(answer) == 42


def test_upstream_errors_pass_through_unchanged(main_loop: asyncio.AbstractEventLoop) -> None:
    async def broken() -> None:
        raise ProviderError(code="UPSTREAM_ERROR", message="上游连接失败", status_code=502)

    with pytest.raises(ProviderError) as caught:
        loop_bridge.call_async(broken)
    assert caught.value.code == "UPSTREAM_ERROR"


def test_a_slow_call_times_out_and_the_coroutine_is_cancelled(
    main_loop: asyncio.AbstractEventLoop,
) -> None:
    cancelled = threading.Event()

    async def forever() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(ProviderError) as caught:
        loop_bridge.call_async(forever, timeout=0.05)
    assert (caught.value.code, caught.value.status_code) == ("UPSTREAM_TIMEOUT", 504)
    assert cancelled.wait(2)  # 超时后没有留下还在后台跑的请求


def test_calling_from_the_loop_thread_is_refused_instead_of_deadlocking(
    main_loop: asyncio.AbstractEventLoop,
) -> None:
    async def nested() -> str:
        async def inner() -> int:
            return 1

        try:
            loop_bridge.call_async(inner)
        except RuntimeError as error:
            return str(error)
        return "没有报错"

    assert "事件循环线程" in asyncio.run_coroutine_threadsafe(nested(), main_loop).result(5)
