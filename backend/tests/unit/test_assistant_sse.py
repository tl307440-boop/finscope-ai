import json

from app.services.assistant.sse import (
    HEARTBEAT,
    delta_events,
    done_event,
    error_event,
    replay_events,
)


def _old_client_text(raw: str) -> tuple[str, bool]:
    """复刻旧前端 `readStream` 的解析：只认 `data:` 行，只读 `delta` 与 `done` 两个键。"""

    text: list[str] = []
    for line in raw.split("\n"):
        trimmed = line.strip()
        if not trimmed.startswith("data:"):
            continue
        payload = json.loads(trimmed[5:].strip())
        delta = payload.get("delta")
        if isinstance(delta, str):
            text.append(delta)
        if payload.get("done") is True:
            return "".join(text), True
    return "".join(text), False


def test_events_carry_type_and_keep_the_old_delta_and_done_keys() -> None:
    first, *_ = delta_events("你好")
    assert json.loads(first.removeprefix("data: ")) == {"type": "delta", "delta": "你好"}
    assert json.loads(done_event("completed").removeprefix("data: ")) == {
        "type": "done",
        "done": True,
        "status": "completed",
    }


def test_empty_text_still_sends_one_empty_delta() -> None:
    events = delta_events("")
    assert len(events) == 1
    assert json.loads(events[0].removeprefix("data: "))["delta"] == ""


def test_replay_is_byte_identical_and_old_clients_read_the_full_text() -> None:
    text = "美国10年期国债收益率最近一次观测是 4.02。" * 5
    first = "".join(replay_events(text, "completed"))
    second = "".join(replay_events(text, "completed"))
    assert first == second
    assert _old_client_text(first) == (text, True)


def test_heartbeat_comment_lines_are_ignored_by_the_old_client() -> None:
    body = "".join(replay_events("三五句话", "completed", size=2))
    with_pings = HEARTBEAT + body.replace("\n\n", "\n\n" + HEARTBEAT, 2)
    assert HEARTBEAT.startswith(":")
    assert _old_client_text(with_pings) == ("三五句话", True)


def test_failed_turn_replays_an_error_then_done_without_any_text() -> None:
    events = replay_events("", "failed")
    kinds = [json.loads(item.removeprefix("data: "))["type"] for item in events]
    assert kinds == ["error", "done"]
    assert json.loads(events[-1].removeprefix("data: "))["status"] == "failed"
    # 旧前端读不到 delta，但能拿到 done，不会一直转圈。
    assert _old_client_text("".join(events)) == ("", True)


def test_error_event_has_code_and_message_only() -> None:
    payload = json.loads(error_event("TURN_FAILED", "没有成功").removeprefix("data: "))
    assert payload == {"type": "error", "code": "TURN_FAILED", "message": "没有成功"}
