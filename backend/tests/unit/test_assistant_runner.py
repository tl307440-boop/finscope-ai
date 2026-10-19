import json
import threading
import time
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from app.services.assistant.orchestrator import Emit, TurnOutcome, TurnRunner
from app.services.assistant.sse import HEARTBEAT, replay_events


@pytest.fixture
def runner() -> Iterator[TurnRunner]:
    created = TurnRunner(workers=2, heartbeat_seconds=0.05)
    yield created
    created.shutdown()


def _payloads(lines: list[str]) -> list[dict[str, Any]]:
    return [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data:")]


def _kinds(lines: list[str]) -> list[str]:
    return [payload["type"] for payload in _payloads(lines)]


def test_events_match_a_replay_of_the_saved_answer(runner: TurnRunner) -> None:
    outcome = TurnOutcome(status="completed", text="结论先行。")
    lines = list(runner.stream(lambda emit: outcome, release=lambda: None))
    assert lines == replay_events("结论先行。", "completed")


def test_slot_is_released_before_the_client_sees_any_event(runner: TurnRunner) -> None:
    order: list[str] = []
    stream = runner.stream(
        lambda emit: TurnOutcome(status="completed", text="好"),
        release=lambda: order.append("release"),
    )
    for _line in stream:
        order.append("event")
    # 客户端看到 done 之后立刻再提问，不能因为名额还没还而被 429。
    assert order[0] == "release"
    assert order.count("release") == 1


def test_streamed_events_go_out_live_and_only_the_tail_waits_for_the_release(
    runner: TurnRunner,
) -> None:
    order: list[str] = []
    proceed = threading.Event()

    def job(emit: Emit) -> TurnOutcome:
        emit({"type": "step", "id": "s1", "label": "查询", "status": "running"})
        emit({"type": "delta", "delta": "先说结论。"})
        assert proceed.wait(timeout=2)
        return TurnOutcome(
            status="completed",
            text="先说结论。",
            streamed=True,
            as_of=date(2026, 10, 16),
            model="m1",
            grounding="full",
        )

    stream = runner.stream(job, release=lambda: order.append("release"))
    first = next(stream)
    # 回合还在跑，前面的事件已经到了浏览器，名额还没还。
    assert json.loads(first.removeprefix("data: "))["type"] == "step"
    assert order == []
    proceed.set()
    rest = list(stream)
    order.extend("event" for _ in rest)
    assert order[0] == "release"
    assert [p["type"] for p in _payloads([first, *rest])] == ["step", "delta", "done"]
    done = _payloads(rest)[-1]
    assert done == {
        "type": "done",
        "done": True,
        "status": "completed",
        "as_of": "2026-10-16",
        "model": "m1",
        "grounding": "full",
    }


def test_streamed_failure_ends_with_error_and_done(runner: TurnRunner) -> None:
    def job(emit: Emit) -> TurnOutcome:
        emit({"type": "step", "id": "s1", "label": "查询", "status": "running"})
        return TurnOutcome(status="failed", text="", streamed=True)

    lines = list(runner.stream(job, release=lambda: None))
    assert _kinds(lines) == ["step", "error", "done"]
    assert _payloads(lines)[-1]["status"] == "failed"


def test_heartbeat_is_sent_while_the_job_is_slow(runner: TurnRunner) -> None:
    def slow(emit: Emit) -> TurnOutcome:
        time.sleep(0.4)
        return TurnOutcome(status="completed", text="慢")

    lines = list(runner.stream(slow, release=lambda: None))
    assert lines.count(HEARTBEAT) >= 3
    # 心跳之外的事件不受影响。
    assert [line for line in lines if line != HEARTBEAT] == replay_events("慢", "completed")


def test_closing_the_response_does_not_cancel_the_turn(runner: TurnRunner) -> None:
    finished = threading.Event()
    released = threading.Event()

    def job(emit: Emit) -> TurnOutcome:
        time.sleep(0.1)
        emit({"type": "delta", "delta": "没人看也继续"})
        finished.set()
        return TurnOutcome(status="completed", text="照常跑完", streamed=True)

    stream = runner.stream(job, release=released.set)
    stream.close()  # 浏览器断开：只是响应生成器停了
    assert finished.wait(timeout=2)
    assert released.wait(timeout=2)


def test_a_crashing_job_ends_as_failed_and_still_releases(runner: TurnRunner) -> None:
    released = threading.Event()

    def boom(emit: Emit) -> TurnOutcome:
        raise RuntimeError("secret upstream detail sk-123")

    lines = list(runner.stream(boom, release=released.set))
    assert released.is_set()
    assert _kinds(lines) == ["error", "done"]
    assert "secret" not in "".join(lines)
    assert json.loads(lines[-1].removeprefix("data: "))["status"] == "failed"


def test_a_crash_after_some_events_does_not_replay_them(runner: TurnRunner) -> None:
    def boom(emit: Emit) -> TurnOutcome:
        emit({"type": "delta", "delta": "半句"})
        raise RuntimeError("upstream down")

    lines = list(runner.stream(boom, release=lambda: None))
    assert _kinds(lines) == ["delta", "error", "done"]


def test_submitting_after_shutdown_raises_so_the_caller_can_fail_the_turn() -> None:
    stopped = TurnRunner(workers=1, heartbeat_seconds=0.05)
    stopped.shutdown()
    with pytest.raises(RuntimeError):
        stopped.stream(lambda emit: TurnOutcome(status="completed", text=""), release=lambda: None)
