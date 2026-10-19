import json
import threading
import time
from collections.abc import Iterator

import pytest
from app.services.assistant.orchestrator import TurnOutcome, TurnRunner
from app.services.assistant.sse import HEARTBEAT, replay_events


@pytest.fixture
def runner() -> Iterator[TurnRunner]:
    created = TurnRunner(workers=2, heartbeat_seconds=0.05)
    yield created
    created.shutdown()


def _kinds(lines: list[str]) -> list[str]:
    return [
        json.loads(line.removeprefix("data: "))["type"]
        for line in lines
        if line.startswith("data:")
    ]


def test_events_match_a_replay_of_the_saved_answer(runner: TurnRunner) -> None:
    outcome = TurnOutcome(status="completed", text="结论先行。")
    lines = list(runner.stream(lambda: outcome, release=lambda: None))
    assert lines == replay_events("结论先行。", "completed")


def test_slot_is_released_before_the_client_sees_any_event(runner: TurnRunner) -> None:
    order: list[str] = []
    stream = runner.stream(
        lambda: TurnOutcome(status="completed", text="好"),
        release=lambda: order.append("release"),
    )
    for _line in stream:
        order.append("event")
    # 客户端看到 done 之后立刻再提问，不能因为名额还没还而被 429。
    assert order[0] == "release"
    assert order.count("release") == 1


def test_heartbeat_is_sent_while_the_job_is_slow(runner: TurnRunner) -> None:
    def slow() -> TurnOutcome:
        time.sleep(0.4)
        return TurnOutcome(status="completed", text="慢")

    lines = list(runner.stream(slow, release=lambda: None))
    assert lines.count(HEARTBEAT) >= 3
    # 心跳之外的事件不受影响。
    assert [line for line in lines if line != HEARTBEAT] == replay_events("慢", "completed")


def test_closing_the_response_does_not_cancel_the_turn(runner: TurnRunner) -> None:
    finished = threading.Event()
    released = threading.Event()

    def job() -> TurnOutcome:
        time.sleep(0.1)
        finished.set()
        return TurnOutcome(status="completed", text="照常跑完")

    stream = runner.stream(job, release=released.set)
    stream.close()  # 浏览器断开：只是响应生成器停了
    assert finished.wait(timeout=2)
    assert released.wait(timeout=2)


def test_a_crashing_job_ends_as_failed_and_still_releases(runner: TurnRunner) -> None:
    released = threading.Event()

    def boom() -> TurnOutcome:
        raise RuntimeError("secret upstream detail sk-123")

    lines = list(runner.stream(boom, release=released.set))
    assert released.is_set()
    assert _kinds(lines) == ["error", "done"]
    assert "secret" not in "".join(lines)
    assert json.loads(lines[-1].removeprefix("data: "))["status"] == "failed"


def test_submitting_after_shutdown_raises_so_the_caller_can_fail_the_turn() -> None:
    stopped = TurnRunner(workers=1, heartbeat_seconds=0.05)
    stopped.shutdown()
    with pytest.raises(RuntimeError):
        stopped.stream(lambda: TurnOutcome(status="completed", text=""), release=lambda: None)
