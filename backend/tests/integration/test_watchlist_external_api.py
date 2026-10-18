"""自选 API：混合导入 A 股与非 A 股标的，列表 / 看板 / 移出都能处理外部标的。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.errors import ProviderError
from app.repositories.accounts import AccountRepository
from app.services.industry_market import IndustryMarketService
from app.services.market_cache import TtlCache
from app.services.market_sentiment import MarketSentimentService
from app.services.watchlist_insights import WatchlistInsightsService, get_insights_service
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 8, tzinfo=UTC)

# 东财 ulist.np：A 股、板块、国债收益率、ETF 共用一个接口；故意不返回 AAPL 与 920438。
QUOTES = [
    {"f12": "600519", "f13": 1, "f14": "贵州茅台", "f2": "1500", "f3": "2", "f20": "3000"},
    {"f12": "BK0473", "f13": 90, "f14": "证券Ⅱ", "f2": "134562.68", "f3": "-1.23"},
    {"f12": "US10Y", "f13": 171, "f14": "美国10年期国债收益率", "f2": "5.0811", "f3": "0.25"},
    {"f12": "513120", "f13": 1, "f14": "港股创新药ETF广发", "f2": "1.226", "f3": "0.16"},
]


class FakeGateway:
    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        if "ulist.np" in url:
            return json.dumps({"data": {"diff": QUOTES}}).encode()
        raise ProviderError(code="UPSTREAM_ERROR", message="不提供", status_code=503)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        raise ProviderError(code="UPSTREAM_ERROR", message="不提供", status_code=503)


def _service() -> WatchlistInsightsService:
    gateway = FakeGateway()
    return WatchlistInsightsService(
        gateway=gateway,
        industry=IndustryMarketService(gateway=gateway, cache=TtlCache()),
        sentiment=MarketSentimentService(gateway=gateway, cache=TtlCache(), now=lambda: NOW),
        cache=TtlCache(),
        now=lambda: NOW,
    )


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
            "DELETE FROM watchlist_groups WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM refresh_sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM user_preferences WHERE user_id IN "
            "(SELECT id FROM users WHERE username = :username)",
            "DELETE FROM users WHERE username = :username",
        ):
            connection.execute(text(statement), {"username": username})
    engine.dispose()


def test_mixed_import_lists_and_removes_external_targets() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"wl-ext-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()
    application = create_app()
    application.dependency_overrides[get_insights_service] = _service
    try:
        with TestClient(application) as client:
            token = client.post(
                "/api/v1/auth/login", json={"username": username, "password": "correct-password"}
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            group_id = client.post(
                "/api/v1/watchlists", headers=headers, json={"name": "混合"}
            ).json()["data"]["id"]

            text_body = "600519 105.AAPL 90.BK0473 171.US10Y 1.513120 0.920438 abc"
            url = f"/api/v1/watchlists/{group_id}/items/batch"
            imported = client.post(url, headers=headers, json={"text": text_body})
            assert imported.status_code == 200, imported.text
            result = imported.json()["data"]
            assert result["added"] == [
                "600519.SH",
                "AAPL",
                "BK0473",
                "US10Y",
                "513120.SH",
                "920438.BJ",
            ]
            # AAPL 是本系统已收录的美股，直接挂靠证券，不算外部标的。
            assert result["external"] == ["BK0473", "US10Y", "513120.SH", "920438.BJ"]
            assert result["unnamed"] == ["920438.BJ"]
            assert result["invalid"] == ["abc"]

            again = client.post(url, headers=headers, json={"text": text_body}).json()["data"]
            assert again["added"] == [] and len(again["existing"]) == 6

            groups = client.get("/api/v1/watchlists", headers=headers).json()["data"]["groups"]
            items = {item["display_symbol"]: item for item in groups[0]["items"]}
            assert set(items) == {"600519.SH", "AAPL", "BK0473", "US10Y", "513120.SH", "920438.BJ"}
            # 响应省略值为 None 的字段，所以外部标的没有 instrument_id 这个键。
            assert "instrument_id" not in items["BK0473"] and items["BK0473"]["item_id"]
            assert items["BK0473"]["kind"] == "board" and items["BK0473"]["kind_label"] == "板块"
            assert items["BK0473"]["name_cn"] == "证券Ⅱ"
            assert items["US10Y"]["kind"] == "bond_yield"
            assert items["513120.SH"]["kind"] == "fund" and items["920438.BJ"]["kind"] == "cn_stock"
            assert items["AAPL"]["instrument_id"] and items["AAPL"]["kind"] == "us_stock"

            insights = client.get(f"/api/v1/watchlists/{group_id}/insights", headers=headers)
            assert insights.status_code == 200, insights.text
            body = insights.json()["data"]
            assert [stock["code"] for stock in body["stocks"]] == ["600519"]
            assert body["summary"]["count"] == 1
            others = {row["display_symbol"]: row for row in body["others"]}
            assert set(others) == {"AAPL", "BK0473", "US10Y", "513120.SH", "920438.BJ"}
            assert others["BK0473"]["last"] == "134562.68"
            assert others["BK0473"]["change_pct"] == "-1.23"
            assert others["513120.SH"]["name"] == "港股创新药ETF广发"
            assert "last" not in others["AAPL"] and others["AAPL"]["name"] == "苹果"
            assert body["others_section"]["status"] == "stale"
            assert "AAPL" in body["others_section"]["reason"]

            removed = client.delete(
                f"/api/v1/watchlists/{group_id}/items/{items['BK0473']['item_id']}",
                headers=headers,
            )
            assert removed.status_code == 200
            after = client.get("/api/v1/watchlists", headers=headers).json()["data"]["groups"][0]
            assert "BK0473" not in {item["display_symbol"] for item in after["items"]}
            assert len(after["items"]) == 5
    finally:
        _cleanup(username)
