"""问答会话列表：重命名、删除、按最近活动排序、只能动自己的会话。假仓库，不连库。"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_current_user
from app.api.v1 import assistant as assistant_api
from app.main import create_app
from app.repositories.chat import SessionBusy, SessionRecord
from app.services.assistant.turns import AssistantConfig
from app.services.auth_types import UserRecord
from fastapi.testclient import TestClient

ME = uuid4()
OTHER = uuid4()
T0 = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)


class FakeChats:
    """内存里的会话表：{session_id: (owner, record, busy)}。"""

    rows: dict[UUID, tuple[UUID, SessionRecord, bool]] = {}

    def __init__(self, engine: object, stale_seconds: int = 300) -> None:
        del engine, stale_seconds

    def list_sessions(self, user_id: UUID) -> list[SessionRecord]:
        mine = [record for owner, record, _ in self.rows.values() if owner == user_id]
        return sorted(mine, key=lambda item: item.updated_at or item.created_at, reverse=True)

    def rename_session(self, user_id: UUID, session_id: UUID, title: str) -> SessionRecord | None:
        cleaned = " ".join(title.split())
        if not cleaned:
            raise ValueError("标题不能为空")
        found = self.rows.get(session_id)
        if found is None or found[0] != user_id:
            return None
        owner, record, busy = found
        renamed = SessionRecord(record.id, cleaned, record.created_at, record.updated_at)
        self.rows[session_id] = (owner, renamed, busy)
        return renamed

    def delete_session(self, user_id: UUID, session_id: UUID) -> bool:
        found = self.rows.get(session_id)
        if found is None or found[0] != user_id:
            return False
        if found[2]:
            raise SessionBusy
        del self.rows[session_id]
        return True


def _add(owner: UUID, title: str, minutes: int, *, busy: bool = False) -> UUID:
    session_id = uuid4()
    record = SessionRecord(session_id, title, T0, T0 + timedelta(minutes=minutes))
    FakeChats.rows[session_id] = (owner, record, busy)
    return session_id


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    FakeChats.rows = {}
    monkeypatch.setattr(assistant_api, "ChatRepository", FakeChats)
    application = create_app()
    application.state.engine = None
    application.state.assistant_config = AssistantConfig(daily_limit=50, stale_seconds=300)
    application.dependency_overrides[get_current_user] = lambda: UserRecord(
        id=ME,
        username="me",
        email="me@example.com",
        password_hash="x",
        role="researcher",
        is_active=True,
        disabled_at=None,
    )
    # 不进入 lifespan：这些测试不需要数据库连接。
    yield TestClient(application)


def test_list_is_newest_activity_first_and_carries_updated_at(client: TestClient) -> None:
    older = _add(ME, "旧问答", 1)
    newer = _add(ME, "中国半导体还有救吗", 30)
    _add(OTHER, "别人的问答", 60)
    body = client.get("/api/v1/assistant/sessions").json()
    sessions = body["data"]["sessions"]
    assert [item["id"] for item in sessions] == [str(newer), str(older)]
    assert sessions[0]["updated_at"].startswith("2026-10-21T01:30")
    assert body["meta"]["request_id"]


def test_rename_trims_and_returns_the_session(client: TestClient) -> None:
    session_id = _add(ME, "研究问答", 1)
    response = client.patch(
        f"/api/v1/assistant/sessions/{session_id}", json={"title": "  半导体   追问 "}
    )
    assert response.status_code == 200
    assert response.json()["data"]["title"] == "半导体 追问"


@pytest.mark.parametrize(("title", "status"), [("", 422), ("   ", 422), ("长" * 81, 422)])
def test_rename_rejects_empty_or_too_long_titles(
    client: TestClient, title: str, status: int
) -> None:
    session_id = _add(ME, "研究问答", 1)
    response = client.patch(f"/api/v1/assistant/sessions/{session_id}", json={"title": title})
    assert response.status_code == status
    assert FakeChats.rows[session_id][1].title == "研究问答"


def test_cannot_rename_or_delete_someone_elses_session(client: TestClient) -> None:
    theirs = _add(OTHER, "别人的问答", 1)
    renamed = client.patch(f"/api/v1/assistant/sessions/{theirs}", json={"title": "改掉"})
    deleted = client.delete(f"/api/v1/assistant/sessions/{theirs}")
    assert (renamed.status_code, deleted.status_code) == (404, 404)
    assert theirs in FakeChats.rows


def test_delete_removes_the_session(client: TestClient) -> None:
    session_id = _add(ME, "研究问答", 1)
    response = client.delete(f"/api/v1/assistant/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["data"] == {"id": str(session_id), "deleted": True}
    assert session_id not in FakeChats.rows
    assert client.delete(f"/api/v1/assistant/sessions/{session_id}").status_code == 404


def test_delete_is_refused_while_an_answer_is_streaming(client: TestClient) -> None:
    session_id = _add(ME, "研究问答", 1, busy=True)
    response = client.delete(f"/api/v1/assistant/sessions/{session_id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SESSION_BUSY"
    assert session_id in FakeChats.rows
