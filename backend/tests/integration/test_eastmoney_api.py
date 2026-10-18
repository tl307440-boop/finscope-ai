"""东财绑定 API：需要登录；二维码 → 轮询 → 同步 → 解绑；响应里不出现任何登录凭证。"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from app.api.v1.eastmoney import get_service
from app.core.config import get_settings
from app.core.secret_box import SecretBox
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.eastmoney_account import EastmoneyLoginExpired, FavorGroup, QrPoll
from app.repositories.accounts import AccountRepository
from app.services import eastmoney_binding as binding_module
from app.services.eastmoney_binding import EastmoneyBindingService
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.integration

COOKIES = {"ct": "CT-SECRET", "ut": "UT-SECRET"}
PNG = b"\x89PNG-fake-image"


class _Login:
    png = PNG
    expired = False

    async def close(self) -> None:
        return None


class _Account:
    def __init__(self) -> None:
        self.state = "waiting"
        self.error: Exception | None = None

    async def start_qr(self) -> _Login:
        return _Login()

    async def poll_qr(self, login: object) -> QrPoll:
        if self.state == "success":
            return QrPoll(state="success", uid="42", alias="老王", cookies=COOKIES)
        return QrPoll(state=self.state)

    async def fetch_groups(self, cookies: Mapping[str, str]) -> list[FavorGroup]:
        if self.error is not None:
            raise self.error
        return [FavorGroup("11", "东财自选-api", ("90.BK0473", "171.US10Y"))]


class _Insights:
    async def resolve_names(self, secids: Sequence[str]) -> dict[str, str]:
        return {secid: f"名称{secid}" for secid in secids}


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        for statement in (
            "DELETE FROM watchlist_items WHERE group_id IN (SELECT id FROM watchlist_groups "
            "WHERE user_id IN (SELECT id FROM users WHERE username = :username))",
            "DELETE FROM watchlist_groups WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM eastmoney_bindings WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM refresh_sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM user_preferences WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM users WHERE username = :username",
        ):
            connection.execute(text(statement), {"username": username})
    engine.dispose()


def test_bind_sync_and_unbind_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    monkeypatch.setattr(binding_module, "_MIN_QR_INTERVAL_SECONDS", 0)
    username = f"em-api-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    account = _Account()
    service = EastmoneyBindingService(
        engine=engine,
        account=cast(Any, account),
        insights=cast(Any, _Insights()),
        box=SecretBox("test-master-key"),
    )
    application = create_app()
    application.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(application) as client:
            assert client.get("/api/v1/eastmoney").status_code == 401
            assert client.post("/api/v1/eastmoney/qr").status_code == 401

            token = client.post(
                "/api/v1/auth/login", json={"username": username, "password": "correct-password"}
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            assert client.get("/api/v1/eastmoney", headers=headers).json()["data"] == {
                "bound": False
            }
            assert client.post("/api/v1/eastmoney/sync", headers=headers).status_code == 404
            assert client.delete("/api/v1/eastmoney", headers=headers).status_code == 404

            qr = client.post("/api/v1/eastmoney/qr", headers=headers).json()["data"]
            assert base64.b64decode(qr["qr_png_base64"]) == PNG and qr["expires_in"] > 0

            poll_url = f"/api/v1/eastmoney/qr/{qr['login_id']}"
            waiting = client.get(poll_url, headers=headers).json()["data"]
            assert waiting == {"state": "waiting"}

            account.state = "success"
            done = client.get(poll_url, headers=headers)
            assert done.status_code == 200, done.text
            body = done.json()["data"]
            assert body["state"] == "success" and body["alias"] == "老王"
            assert body["sync"]["groups"] == 1 and body["sync"]["added"] == 2
            # 任何响应都不能带出登录凭证。
            assert "SECRET" not in done.text

            status = client.get("/api/v1/eastmoney", headers=headers).json()["data"]
            assert status["bound"] is True and status["status"] == "active"
            assert status["alias"] == "老王" and status["groups"] == 1 and status["items"] == 2
            assert "SECRET" not in str(status) and "cookies" not in status

            # 同步来的分组出现在自选列表里，板块、国债收益率按外部标的保存。
            groups = client.get("/api/v1/watchlists", headers=headers).json()["data"]["groups"]
            assert [g["name"] for g in groups] == ["东财自选-api"]
            assert {i["display_symbol"]: i["kind"] for i in groups[0]["items"]} == {
                "BK0473": "board",
                "US10Y": "bond_yield",
            }

            # 刚同步过，再点「立即同步」只是返回上次结果，不再请求东财。
            assert (
                client.post("/api/v1/eastmoney/sync", headers=headers).json()["data"]["cached"]
                is True
            )

            # 东财登录失效：409，状态变成 expired，提示重新扫码。
            account.error = EastmoneyLoginExpired(
                code="EASTMONEY_LOGIN_EXPIRED", message="东财登录已失效，请重新扫码"
            )
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE eastmoney_bindings SET last_sync_at = now() - interval '5 min'")
                )
            failed = client.post("/api/v1/eastmoney/sync", headers=headers)
            assert failed.status_code == 409
            assert failed.json()["error"]["code"] == "EASTMONEY_LOGIN_EXPIRED"
            expired = client.get("/api/v1/eastmoney", headers=headers).json()["data"]
            assert expired["status"] == "expired"

            removed = client.delete("/api/v1/eastmoney", headers=headers)
            assert removed.status_code == 200
            assert client.get("/api/v1/eastmoney", headers=headers).json()["data"] == {
                "bound": False
            }
            # 解绑后已同步的自选还在。
            kept = client.get("/api/v1/watchlists", headers=headers).json()["data"]["groups"]
            assert len(kept) == 1 and len(kept[0]["items"]) == 2

    finally:
        engine.dispose()
        _cleanup(username)
