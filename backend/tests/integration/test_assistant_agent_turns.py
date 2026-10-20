"""助手第 1b 期端到端：灰度开关、步骤 / 数据块 / 正文 / 落库、重放、取消、历史与实体。

走真实的 HTTP 接口和 PostgreSQL；模型用剧本假实现，数据工具用假的 `macro_series`
（真实数据工具对数据库的读法见 `test_assistant_tools.py`）。
"""

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import date
from itertools import groupby
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.v1 import assistant as assistant_api
from app.core.config import get_settings, reset_settings_cache
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.repositories.chat import ChatRepository, TurnExtras
from app.services.assistant import responder as responder_module
from app.services.assistant.llm_tools import ModelCallError, ModelReply
from app.services.assistant.tools import CalcTool, ToolRegistry
from app.services.assistant.tools.macro import SERIES
from app.services.runtime_settings import IntegrationService, invalidate
from app.utils.time import utc_now
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from assistant_fakes import FakeModel, FakeSeries, say

PASSWORD = "correct-password"
QUESTION = "美国CPI最近怎么走"  # 快速路径会直取 CPIAUCSL
MODEL_ENV = {
    "LLM_API_KEY": "test-key",
    "LLM_API_BASE_URL": "https://llm.example.test/v1",
    "LLM_MODEL": "m1",
}
NO_MODEL_ENV = {"LLM_API_KEY": "", "LLM_API_BASE_URL": "", "LLM_MODEL": ""}


def _alembic_config() -> Config:
    from pathlib import Path

    return Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        command.upgrade(_alembic_config(), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        for table in ("chat_sessions", "refresh_sessions", "user_preferences"):
            connection.execute(
                text(
                    f"DELETE FROM {table} "  # noqa: S608 - 表名是上面写死的常量
                    "WHERE user_id IN (SELECT id FROM users WHERE username = :username)"
                ),
                {"username": username},
            )
        connection.execute(
            text("DELETE FROM users WHERE username = :username"), {"username": username}
        )
    engine.dispose()


def _events(raw: str) -> list[dict[str, Any]]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in raw.split("\n\n")
        if block.startswith("data: ")
    ]


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if isinstance(e.get("delta"), str))


def _kinds(events: list[dict[str, Any]]) -> list[str]:
    """相邻同类事件合并，只看大的顺序。"""

    return [kind for kind, _ in groupby(e["type"] for e in events)]


@pytest.fixture
def db_ready() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")


@pytest.fixture
def engine(db_ready: None) -> Iterator[Engine]:
    created = create_db_engine(get_settings().database_url)
    yield created
    created.dispose()


@pytest.fixture
def make_user(engine: Engine) -> Iterator[Callable[[], str]]:
    names: list[str] = []

    def create() -> str:
        username = f"t30a-{uuid4().hex[:8]}"
        AccountRepository(engine).create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash(PASSWORD),
            role="researcher",
        )
        names.append(username)
        return username

    yield create
    for name in names:
        _cleanup(name)


@pytest.fixture
def open_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., TestClient]]:
    stack = ExitStack()

    def open_client(**env: str) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        reset_settings_cache()
        invalidate()  # 运行时设置有 5 秒缓存，不同测试的环境变量不能串
        client = stack.enter_context(TestClient(create_app()))
        app: Any = client.app
        app.state.assistant_registry = ToolRegistry([FakeSeries(), CalcTool()])
        return client

    yield open_client
    stack.close()
    reset_settings_cache()
    invalidate()


def _login(client: TestClient, username: str) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _new_session(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post("/api/v1/assistant/sessions", headers=headers, json={"title": "研究问答"})
    return str(created.json()["data"]["id"])


def _ask(
    client: TestClient,
    headers: dict[str, str],
    session_id: str,
    content: str,
    client_message_id: UUID | None = None,
) -> tuple[Any, UUID]:
    cid = client_message_id or uuid4()
    response = client.post(
        f"/api/v1/assistant/sessions/{session_id}/messages",
        headers=headers,
        json={"client_message_id": str(cid), "content": content},
    )
    return response, cid


def _answer_row(engine: Engine, session_id: str) -> dict[str, Any]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT content, completion_status, blocks, steps, as_of, model, grounding, "
                    "tool_trace, finished_at FROM chat_messages "
                    "WHERE session_id = :sid AND role = 'assistant' "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"sid": session_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def _use_model(
    monkeypatch: pytest.MonkeyPatch, model: FakeModel, *, tools_supported: bool = True
) -> None:
    monkeypatch.setattr(responder_module, "OpenAIToolModel", lambda **_kwargs: model)
    monkeypatch.setattr(responder_module, "supports_tools", lambda _m, _url: tools_supported)


def _forbid_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_kwargs: object) -> None:
        raise AssertionError("开关关闭时不该创建模型客户端")

    monkeypatch.setattr(responder_module, "OpenAIToolModel", refuse)


@pytest.fixture
def session_for(
    open_app: Callable[..., TestClient], make_user: Callable[[], str]
) -> Callable[..., tuple[TestClient, dict[str, str], str]]:
    def build(**env: str) -> tuple[TestClient, dict[str, str], str]:
        username = make_user()
        client = open_app(**env)
        headers = _login(client, username)
        return client, headers, _new_session(client, headers)

    return build


# ---------------------------------------------------------------- 灰度开关
def test_switch_off_keeps_the_1a_behaviour_even_with_a_model_configured(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_model(monkeypatch)
    monkeypatch.setattr(assistant_api, "_composer", lambda _engine: lambda _q: "固定回答。")
    client, headers, session_id = session_for(**MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    events = _events(response.text)
    assert {e["type"] for e in events} == {"delta", "done"}  # 没有 step / block
    assert events[-1] == {"type": "done", "done": True, "status": "completed"}
    assert _text(events) == "固定回答。"
    row = _answer_row(engine, session_id)
    assert (row["blocks"], row["steps"], row["grounding"], row["tool_trace"]) == (
        None,
        None,
        None,
        None,
    )


def test_enabled_without_a_configured_model_falls_back_to_the_legacy_answer(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_model(monkeypatch)
    monkeypatch.setattr(assistant_api, "_composer", lambda _engine: lambda _q: "未配置模型。")
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **NO_MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    assert _text(_events(response.text)) == "未配置模型。"
    assert {e["type"] for e in _events(response.text)} == {"delta", "done"}


def test_the_switch_flips_at_runtime_without_a_restart(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(say("最新是 323.0。"))
    _use_model(monkeypatch, model)
    monkeypatch.setattr(assistant_api, "_composer", lambda _engine: lambda _q: "固定回答。")
    client, headers, session_id = session_for(**MODEL_ENV)
    service = IntegrationService(engine)
    try:
        before, _ = _ask(client, headers, session_id, QUESTION)
        service.save("ASSISTANT_ENABLED", "true", user_id=None, now=utc_now())
        after, _ = _ask(client, headers, session_id, QUESTION)
        service.save("ASSISTANT_ENABLED", "false", user_id=None, now=utc_now())
        off_again, _ = _ask(client, headers, session_id, QUESTION)
    finally:
        service.clear("ASSISTANT_ENABLED")

    assert _text(_events(before.text)) == "固定回答。"
    assert _text(_events(after.text)) == "最新是 323.0。"
    assert "block" in {e["type"] for e in _events(after.text)}
    assert _text(_events(off_again.text)) == "固定回答。"
    assert len(model.calls) == 1


# ---------------------------------------------------------------- 一个完整回合
def test_enabled_turn_streams_steps_blocks_and_text_then_saves_and_replays_everything(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(say("最新是 323.0。"))
    _use_model(monkeypatch, model)
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, cid = _ask(client, headers, session_id, QUESTION)

    events = _events(response.text)
    assert _kinds(events) == ["step", "block", "delta", "done"]
    assert events[0] == {
        "type": "step",
        "id": "s1",
        "label": "查宏观序列…",
        "status": "running",
    }
    assert events[-1] == {
        "type": "done",
        "done": True,
        "status": "completed",
        "as_of": "2026-08-01",
        "model": "fake-1",
        "grounding": "full",
    }
    assert _text(events) == "最新是 323.0。"
    assert len(model.calls) == 1  # 快速路径已经取到数据，模型一次就答完

    row = _answer_row(engine, session_id)
    assert (row["content"], row["completion_status"], row["grounding"]) == (
        "最新是 323.0。",
        "completed",
        "full",
    )
    assert [b["type"] for b in row["blocks"]] == ["line_chart", "metric_cards"]
    assert row["steps"] == [{"id": "s1", "label": "查宏观序列：美国 CPI", "status": "ok"}]
    assert (str(row["as_of"]), row["model"]) == ("2026-08-01", "fake-1")
    assert row["finished_at"] is not None
    trace = row["tool_trace"]
    assert trace["prompt"] == "assistant_v2" and trace["mode"] == "tools"
    assert trace["fast_path"] == ["macro_series"]
    assert [(c["tool"], c["status"], c["evidence"]) for c in trace["calls"]] == [
        ("macro_series", "ok", "E1")
    ]
    assert trace["calls"][0]["args"] == {"code": "CPIAUCSL"}
    assert "rows" in trace["calls"][0] and "values" not in json.dumps(trace)  # 不存结果行

    # 刷新页面：消息接口带回数据块、步骤、时点、核对结论。
    history = client.get(f"/api/v1/assistant/sessions/{session_id}/messages", headers=headers)
    answer = next(m for m in history.json()["data"]["messages"] if m["role"] == "assistant")
    assert [b["type"] for b in answer["blocks"]] == ["line_chart", "metric_cards"]
    assert answer["steps"] == row["steps"]
    assert (answer["as_of"], answer["grounding"]) == ("2026-08-01", "full")
    assert "sources" not in answer  # 接口统一不输出 null：没有值的字段缺省
    user_row = next(m for m in history.json()["data"]["messages"] if m["role"] == "user")
    assert not ({"blocks", "steps", "as_of", "grounding", "sources"} & user_row.keys())

    # 同一个 client_message_id 再来：不再调模型，步骤、块、正文、结束信号原样还原。
    replay, _ = _ask(client, headers, session_id, "换了问法也不会重算", cid)
    again = _events(replay.text)
    assert len(model.calls) == 1
    assert [e for e in again if e["type"] == "block"] == [e for e in events if e["type"] == "block"]
    assert [e for e in again if e["type"] == "step"] == [{"type": "step", **row["steps"][0]}]
    assert _text(again) == _text(events)
    assert again[-1] == events[-1]


def test_blocks_are_saved_before_the_model_is_asked(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def look(messages: Any, tools: Any) -> ModelReply:
        # 另一条连接读：回合还在 streaming，但预取到的块已经写进回答行。
        seen["row"] = _answer_row(engine, seen["session_id"])
        return say("最新是 323.0。")

    _use_model(monkeypatch, FakeModel(look))
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)
    seen["session_id"] = session_id

    _ask(client, headers, session_id, QUESTION)

    assert seen["row"]["completion_status"] == "streaming"
    assert seen["row"]["content"] == ""
    assert [b["type"] for b in seen["row"]["blocks"]] == ["line_chart", "metric_cards"]


def test_grounding_blocks_a_made_up_number_before_it_reaches_the_browser(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(
        monkeypatch,
        FakeModel(say("最新是 323.0。同比涨了 9.99%。"), say("同比变化请看图表。")),
    )
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    assert _text(_events(response.text)) == "最新是 323.0。同比变化请看图表。"
    assert "9.99" not in response.text
    row = _answer_row(engine, session_id)
    assert row["content"] == "最新是 323.0。同比变化请看图表。"
    assert row["grounding"] == "full" and row["tool_trace"]["repaired"] is True
    assert row["tool_trace"]["unmatched"] == ["9.99%"]


# ---------------------------------------------------------------- 取消
def test_cancel_during_the_model_call_keeps_the_blocks_and_writes_no_text(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached = threading.Event()
    proceed = threading.Event()

    def slow(messages: Any, tools: Any) -> ModelReply:
        reached.set()
        assert proceed.wait(timeout=10)
        return say("取消之后不应该出现。")

    model = FakeModel(slow)
    _use_model(monkeypatch, model)
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)
    cid = uuid4()
    outcome: dict[str, Any] = {}

    def ask() -> None:
        outcome["response"], _ = _ask(client, headers, session_id, QUESTION, cid)

    worker = threading.Thread(target=ask)
    worker.start()
    try:
        assert reached.wait(timeout=10)
        cancelled = client.post(
            f"/api/v1/assistant/sessions/{session_id}/messages/{cid}/cancel", headers=headers
        )
        assert cancelled.json()["data"]["completion_status"] == "cancelled"
    finally:
        proceed.set()
        worker.join(timeout=10)

    events = _events(outcome["response"].text)
    assert events[-1]["status"] == "cancelled"
    assert "取消之后" not in outcome["response"].text
    assert "block" in {e["type"] for e in events}  # 取消前已推出的块保留
    row = _answer_row(engine, session_id)
    assert (row["completion_status"], row["content"]) == ("cancelled", "")
    assert [b["type"] for b in row["blocks"]] == ["line_chart", "metric_cards"]
    # 刷新后重放：块还在，没有文字。
    replay, _ = _ask(client, headers, session_id, QUESTION, cid)
    assert "block" in {e["type"] for e in _events(replay.text)}
    assert _events(replay.text)[-1]["status"] == "cancelled"


# ---------------------------------------------------------------- 失败与降级
def test_model_failure_before_any_data_is_a_clean_failed_turn(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(monkeypatch, FakeModel(ModelCallError("http", 500)))
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, cid = _ask(client, headers, session_id, "你好")

    events = _events(response.text)
    assert [e["type"] for e in events] == ["error", "done"]
    assert events[-1]["status"] == "failed"
    assert "ModelCallError" not in response.text and "500" not in response.text
    row = _answer_row(engine, session_id)
    assert (row["completion_status"], row["content"]) == ("failed", "")
    assert row["tool_trace"]["failure"] == "http"
    again, _ = _ask(client, headers, session_id, "你好", cid)
    assert again.text == response.text


def test_model_failure_after_data_still_completes_with_the_data(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(monkeypatch, FakeModel(ModelCallError("timeout")))
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    events = _events(response.text)
    assert events[-1]["status"] == "completed" and events[-1]["grounding"] == "none"
    assert _text(events) == ""
    row = _answer_row(engine, session_id)
    assert [b["type"] for b in row["blocks"]] == ["line_chart", "metric_cards", "notice"]
    assert row["blocks"][-1]["code"] == "NOT_GROUNDED"
    assert row["tool_trace"]["failure"] == "timeout"


def test_a_crash_inside_the_agent_fails_the_turn_without_leaking_details(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_model(
        monkeypatch, FakeModel(RuntimeError("upstream https://llm.internal/v1 key=sk-secret"))
    )
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    events = _events(response.text)
    assert _kinds(events)[-2:] == ["error", "done"]
    assert events[-1]["status"] == "failed"
    assert "sk-secret" not in response.text and "llm.internal" not in response.text
    assert _answer_row(engine, session_id)["completion_status"] == "failed"


# ---------------------------------------------------------------- 多轮与规划器模式
def test_a_follow_up_sees_the_earlier_exchange_and_the_entities_it_mentioned(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(say("最新是 323.0。"), say("没有新的数据。"))
    _use_model(monkeypatch, model)
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    _ask(client, headers, session_id, QUESTION)
    _ask(client, headers, session_id, "那上个月呢")

    first, _tools = model.calls[0]
    second, _tools = model.calls[1]
    assert second[0] == {"role": "system", "content": responder_module.SYSTEM_PROMPT}
    assert [m["role"] for m in second] == ["system", "user", "assistant", "system", "user"]
    assert second[1]["content"] == QUESTION and second[2]["content"] == "最新是 323.0。"
    assert second[0] == first[0]  # 提示词前缀逐字节相同
    note = second[3]["content"]
    assert f"序列 CPIAUCSL（{SERIES['CPIAUCSL'].name}）" in note
    assert second[-1] == {"role": "user", "content": "那上个月呢"}
    # 旧回合只带文字，不带工具结果。
    assert not any(m["role"] == "tool" or "tool_calls" in m for m in second)


def test_models_without_tool_calling_use_planner_mode(
    session_for: Callable[..., tuple[TestClient, dict[str, str], str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeModel(say('{"calls":[]}'), say("最新是 323.0。"))
    _use_model(monkeypatch, model, tools_supported=False)
    client, headers, session_id = session_for(ASSISTANT_ENABLED="true", **MODEL_ENV)

    response, _ = _ask(client, headers, session_id, QUESTION)

    assert _text(_events(response.text)) == "最新是 323.0。"
    assert all(tools is None for _messages, tools in model.calls)
    assert _answer_row(engine, session_id)["tool_trace"]["mode"] == "planner"


# ---------------------------------------------------------------- 仓储层
def test_append_block_only_writes_while_the_turn_is_streaming(
    engine: Engine, make_user: Callable[[], str]
) -> None:
    username = make_user()
    with engine.connect() as connection:
        user_id = connection.execute(
            text("SELECT id FROM users WHERE username = :n"), {"n": username}
        ).scalar_one()
    repository = ChatRepository(engine)
    session = repository.create_session(user_id, "t")
    cid = uuid4()
    assert repository.create_turn(user_id, session.id, cid, "问") is True

    assert repository.turn_status(session.id, cid) == "streaming"
    assert repository.append_block(session.id, cid, {"type": "notice", "n": 1}) is True
    assert repository.append_block(session.id, cid, {"type": "notice", "n": 2}) is True
    repository.finish_turn(session.id, cid, status="completed", content="答")
    assert repository.append_block(session.id, cid, {"type": "notice", "n": 3}) is False

    assert repository.turn_status(session.id, cid) == "completed"
    assert repository.turn_status(session.id, uuid4()) is None
    stored = repository.find_turn(user_id, session.id, cid)
    assert stored is not None and stored.turn is not None
    # 完成后的追加被拒绝，之前的两块保留。
    assert [b["n"] for b in stored.turn.blocks or []] == [1, 2]


def test_finish_turn_extras_round_trip_and_recent_turns_are_ordered_and_filtered(
    engine: Engine, make_user: Callable[[], str]
) -> None:
    username = make_user()
    with engine.connect() as connection:
        user_id = connection.execute(
            text("SELECT id FROM users WHERE username = :n"), {"n": username}
        ).scalar_one()
    repository = ChatRepository(engine)
    session = repository.create_session(user_id, "t")
    ids = [uuid4() for _ in range(5)]
    for number, cid in enumerate(ids):
        repository.create_turn(user_id, session.id, cid, f"问{number}")
    extras = TurnExtras(
        blocks=[{"type": "notice"}],
        steps=[{"id": "s1", "label": "x", "status": "ok"}],
        as_of=date(2026, 10, 16),
        model="m1",
        grounding="full",
        tool_trace={"calls": [{"tool": "macro_series", "args": {"code": "UNRATE"}}]},
    )
    repository.finish_turn(session.id, ids[0], status="completed", content="答0", extras=extras)
    repository.finish_turn(session.id, ids[1], status="failed", content="")  # 失败的不进历史
    repository.finish_turn(session.id, ids[2], status="completed", content="答2")
    repository.finish_turn(session.id, ids[3], status="completed", content="答3")
    # ids[4] 是当前回合，仍在 streaming

    stored = repository.find_turn(user_id, session.id, ids[0])
    assert stored is not None and stored.turn is not None
    turn = stored.turn
    assert (turn.as_of, turn.model, turn.grounding) == (date(2026, 10, 16), "m1", "full")
    assert turn.blocks == [{"type": "notice"}] and turn.steps == extras.steps

    recent = repository.recent_turns(session.id, ids[4], limit=3)
    assert [(h.question, h.answer) for h in recent] == [
        ("问0", "答0"),
        ("问2", "答2"),
        ("问3", "答3"),
    ]
    assert recent[0].trace == extras.tool_trace and recent[1].trace is None
    assert [h.question for h in repository.recent_turns(session.id, ids[3], limit=2)] == [
        "问0",
        "问2",
    ]
    assert repository.recent_turns(session.id, ids[4], limit=1)[0].question == "问3"
