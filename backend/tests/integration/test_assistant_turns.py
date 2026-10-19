"""助手回合底座（规格 §3、§4、§16，第 1a 期）：两段式落库、重放、残留、限流、心跳、迁移。"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
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
from app.repositories.chat import ChatRepository
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

PASSWORD = "correct-password"


def _alembic_config() -> Config:
    root = Path(__file__).resolve().parents[2]
    return Config(str(root / "alembic.ini"))


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


def _deltas(raw: str) -> str:
    parts: list[str] = []
    for block in raw.split("\n\n"):
        if block.startswith("data: "):
            payload = json.loads(block.removeprefix("data: "))
            if isinstance(payload.get("delta"), str):
                parts.append(payload["delta"])
    return "".join(parts)


def _events(raw: str) -> list[dict[str, object]]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in raw.split("\n\n")
        if block.startswith("data: ")
    ]


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
def make_user(engine: Engine) -> Iterator[Callable[[], tuple[UUID, str]]]:
    names: list[str] = []

    def create() -> tuple[UUID, str]:
        username = f"t29-{uuid4().hex[:8]}"
        record = AccountRepository(engine).create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash(PASSWORD),
            role="researcher",
        )
        names.append(username)
        return record.id, username

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
        return stack.enter_context(TestClient(create_app()))

    yield open_client
    stack.close()
    reset_settings_cache()


def _login(client: TestClient, username: str) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": PASSWORD}
    ).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _new_session(client: TestClient, headers: dict[str, str], title: str = "研究问答") -> str:
    created = client.post("/api/v1/assistant/sessions", headers=headers, json={"title": title})
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


def _assistant_rows(
    client: TestClient, headers: dict[str, str], session_id: str
) -> list[dict[str, Any]]:
    history = client.get(f"/api/v1/assistant/sessions/{session_id}/messages", headers=headers)
    return [m for m in history.json()["data"]["messages"] if m["role"] == "assistant"]


def _stub_composer(monkeypatch: pytest.MonkeyPatch, compose: Callable[[str], str]) -> None:
    monkeypatch.setattr(assistant_api, "_composer", lambda _engine: compose)


def test_turn_is_committed_before_the_model_runs(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    seen: dict[str, object] = {}

    def compose(question: str) -> str:
        # 用另一条连接去读：看得见说明提问和 streaming 回答已经提交，
        # 也就是模型调用时没有占着那个写事务。
        with engine.connect() as other:
            rows = other.execute(
                text(
                    "SELECT role, completion_status, content FROM chat_messages "
                    "WHERE session_id = :sid ORDER BY role"
                ),
                {"sid": seen["session_id"]},
            ).all()
        seen["rows"] = [tuple(row) for row in rows]
        return "已按资料回答。"

    _stub_composer(monkeypatch, compose)
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)
    seen["session_id"] = session_id

    response, _ = _ask(client, headers, session_id, "营收是多少\n请回答")

    assert response.status_code == 200
    assert seen["rows"] == [
        ("assistant", "streaming", ""),
        ("user", "completed", "营收是多少\n请回答"),
    ]
    assert _deltas(response.text) == "已按资料回答。"
    [answer] = _assistant_rows(client, headers, session_id)
    assert answer["completion_status"] == "completed"
    assert answer["content"] == "已按资料回答。"
    with engine.connect() as connection:
        finished = connection.execute(
            text(
                "SELECT finished_at FROM chat_messages "
                "WHERE session_id = :sid AND role = 'assistant'"
            ),
            {"sid": session_id},
        ).scalar_one()
    assert finished is not None


def test_first_question_becomes_the_session_title_once(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    _stub_composer(monkeypatch, lambda _question: "好")
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers, title="新的问答")

    _ask(
        client,
        headers,
        session_id,
        "美国10年期与2年期国债收益率\n最近怎么走？请分点说明并给出数据来源和日期",
    )
    _ask(client, headers, session_id, "第二个问题不应改标题")

    titles = client.get("/api/v1/assistant/sessions", headers=headers).json()["data"]["sessions"]
    assert (
        titles[0]["title"]
        == "美国10年期与2年期国债收益率 最近怎么走？请分点说明并给出数据来源和日期"[:30]
    )


def test_replay_is_identical_and_does_not_call_the_model_again(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    calls: list[str] = []

    def compose(question: str) -> str:
        calls.append(question)
        return "只算一次。" * 10

    _stub_composer(monkeypatch, compose)
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    first, cid = _ask(client, headers, session_id, "营收是多少")
    second, _ = _ask(client, headers, session_id, "换了问法也不会重算", cid)

    assert second.text == first.text
    assert calls == ["营收是多少"]
    assert len(_assistant_rows(client, headers, session_id)) == 1


def test_resubmitting_while_streaming_is_409_then_replays_once_finished(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    engine: Engine,
) -> None:
    user_id, username = make_user()
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)
    repository = ChatRepository(engine)
    cid = uuid4()
    assert repository.create_turn(user_id, UUID(session_id), cid, "营收是多少") is True

    busy, _ = _ask(client, headers, session_id, "营收是多少", cid)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "TURN_IN_PROGRESS"

    status = repository.finish_turn(UUID(session_id), cid, status="completed", content="定稿")
    assert status == "completed"
    replay, _ = _ask(client, headers, session_id, "营收是多少", cid)
    assert replay.status_code == 200
    assert _deltas(replay.text) == "定稿"


def test_stale_streaming_turn_is_read_as_failed_and_persisted_on_next_write(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    engine: Engine,
) -> None:
    user_id, username = make_user()
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)
    cid = uuid4()
    ChatRepository(engine).create_turn(user_id, UUID(session_id), cid, "进程重启前的提问")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chat_messages SET created_at = now() - interval '10 minutes' "
                "WHERE session_id = :sid"
            ),
            {"sid": session_id},
        )

    # 读：只在返回时当作 failed，不改库。
    [answer] = _assistant_rows(client, headers, session_id)
    assert answer["completion_status"] == "failed"
    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT completion_status FROM chat_messages WHERE role = 'assistant' "
                "AND session_id = :sid"
            ),
            {"sid": session_id},
        ).scalar_one()
    assert stored == "streaming"

    # 写：再次提交同一回合时落库为 failed，并按失败重放，旧前端也能收到 done。
    replay, _ = _ask(client, headers, session_id, "进程重启前的提问", cid)
    events = _events(replay.text)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[-1]["status"] == "failed"
    with engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT completion_status FROM chat_messages WHERE role = 'assistant' "
                "AND session_id = :sid"
            ),
            {"sid": session_id},
        ).scalar_one()
    assert stored == "failed"


def test_cancel_during_streaming_wins_over_a_late_answer(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    engine: Engine,
) -> None:
    user_id, username = make_user()
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)
    repository = ChatRepository(engine)
    cid = uuid4()
    repository.create_turn(user_id, UUID(session_id), cid, "营收是多少")

    cancelled = client.post(
        f"/api/v1/assistant/sessions/{session_id}/messages/{cid}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["completion_status"] == "cancelled"

    late = repository.finish_turn(UUID(session_id), cid, status="completed", content="迟到的回答")
    assert late == "cancelled"
    [answer] = _assistant_rows(client, headers, session_id)
    assert answer["completion_status"] == "cancelled"
    assert answer["content"] == ""

    replay, _ = _ask(client, headers, session_id, "营收是多少", cid)
    assert _events(replay.text)[-1]["status"] == "cancelled"
    assert "迟到" not in replay.text


def test_a_crashing_model_call_marks_the_turn_failed_without_leaking_details(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()

    def compose(_question: str) -> str:
        raise RuntimeError("upstream https://llm.internal/v1 key=sk-secret")

    _stub_composer(monkeypatch, compose)
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    response, cid = _ask(client, headers, session_id, "营收是多少")

    assert response.status_code == 200
    events = _events(response.text)
    assert [event["type"] for event in events] == ["error", "done"]
    assert "sk-secret" not in response.text
    [answer] = _assistant_rows(client, headers, session_id)
    assert answer["completion_status"] == "failed"
    again, _ = _ask(client, headers, session_id, "营收是多少", cid)
    assert again.text == response.text


def test_sse_headers_disable_proxy_buffering_and_slow_turns_get_heartbeats(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()

    def compose(_question: str) -> str:
        time.sleep(0.45)
        return "慢回答。"

    _stub_composer(monkeypatch, compose)
    client = open_app(ASSISTANT_HEARTBEAT_SECONDS="0.1")
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    response, _ = _ask(client, headers, session_id, "营收是多少")

    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count(": ping\n\n") >= 2
    # 旧前端只认 data: 行；心跳不影响正文与结束信号。
    assert _deltas(response.text) == "慢回答。"
    assert _events(response.text)[-1] == {
        "type": "done",
        "done": True,
        "status": "completed",
    }


def test_second_concurrent_turn_from_the_same_user_is_rejected_with_429(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    started = threading.Event()
    release = threading.Event()

    def compose(_question: str) -> str:
        started.set()
        release.wait(timeout=10)
        return "第一条"

    _stub_composer(monkeypatch, compose)
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)
    outcome: dict[str, Any] = {}

    def first() -> None:
        outcome["first"], _ = _ask(client, headers, session_id, "第一问")

    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert started.wait(timeout=10)
        second, _ = _ask(client, headers, session_id, "第二问")
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "RATE_LIMITED"
        assert "上一条" in second.json()["error"]["message"]
    finally:
        release.set()
        worker.join(timeout=10)
    assert outcome["first"].status_code == 200
    # 被拒的那一问没有落库。
    assert len(_assistant_rows(client, headers, session_id)) == 1
    # 第一条结束后立刻可以再问。
    third, _ = _ask(client, headers, session_id, "第三问")
    assert third.status_code == 200


def test_daily_limit_rejects_new_turns_without_saving_them_but_replays_still_work(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    _stub_composer(monkeypatch, lambda question: f"回答：{question}")
    client = open_app(ASSISTANT_DAILY_LIMIT="2")
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    first, first_cid = _ask(client, headers, session_id, "一")
    second, _ = _ask(client, headers, session_id, "二")
    third, _ = _ask(client, headers, session_id, "三")

    assert first.status_code == 200 and second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMITED"
    assert "今天" in third.json()["error"]["message"]
    assert len(_assistant_rows(client, headers, session_id)) == 2
    replay, _ = _ask(client, headers, session_id, "一", first_cid)
    assert replay.status_code == 200
    assert replay.text == first.text


def test_per_minute_limit_rejects_the_extra_turn(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, username = make_user()
    _stub_composer(monkeypatch, lambda _question: "好")
    client = open_app(ASSISTANT_PER_MINUTE="2")
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    statuses = [_ask(client, headers, session_id, f"问{n}")[0].status_code for n in range(3)]

    assert statuses == [200, 200, 429]
    assert len(_assistant_rows(client, headers, session_id)) == 2


def test_posting_to_someone_elses_session_is_404_and_saves_nothing(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, owner = make_user()
    _, intruder = make_user()
    _stub_composer(monkeypatch, lambda _question: "不应被调用")
    client = open_app()
    owner_headers = _login(client, owner)
    session_id = _new_session(client, owner_headers)

    denied, _ = _ask(client, _login(client, intruder), session_id, "我能看到吗")

    assert denied.status_code == 404
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT count(*) FROM chat_messages WHERE session_id = :sid"),
            {"sid": session_id},
        ).scalar_one()
    assert rows == 0


def test_blank_question_is_rejected_before_anything_is_saved(
    open_app: Callable[..., TestClient],
    make_user: Callable[[], tuple[UUID, str]],
) -> None:
    _, username = make_user()
    client = open_app()
    headers = _login(client, username)
    session_id = _new_session(client, headers)

    blank, _ = _ask(client, headers, session_id, "   ")

    assert blank.status_code == 422
    assert _assistant_rows(client, headers, session_id) == []


def test_migration_0029_adds_the_columns_and_can_be_rolled_back(db_ready: None) -> None:
    config = _alembic_config()
    engine = create_db_engine(get_settings().database_url)
    columns = (
        "blocks",
        "sources",
        "steps",
        "as_of",
        "model",
        "grounding",
        "tool_trace",
        "finished_at",
    )

    def present() -> set[str]:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'chat_messages'"
                )
            ).scalars()
            return set(rows)

    def index_exists() -> bool:
        with engine.connect() as connection:
            return (
                connection.execute(
                    text(
                        "SELECT 1 FROM pg_indexes "
                        "WHERE indexname = 'ix_chat_messages_assistant_created'"
                    )
                ).first()
                is not None
            )

    try:
        assert set(columns) <= present() and index_exists()
        command.downgrade(config, "20261018_0028")
        assert not (set(columns) & present())
        assert not index_exists()
    finally:
        command.upgrade(config, "head")
        engine.dispose()
    check = create_db_engine(get_settings().database_url)
    try:
        with check.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM chat_messages WHERE blocks IS NOT NULL")
                ).scalar_one()
                == 0
            )  # 旧消息这些列都是 NULL
    finally:
        check.dispose()
