import json
from datetime import date
from uuid import uuid4

import pytest
from app.catalog.reference_data import reference_instruments
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.akshare_bars import parse_klines
from app.providers.errors import ProviderError
from app.providers.quality import RawBar
from app.repositories.accounts import AccountRepository
from app.repositories.instruments import InstrumentRepository
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

PAYLOAD = json.dumps(
    {
        "data": {
            "klines": [
                "1999-01-04,1.10,1.20,1.30,1.00,10,100.00,0,0,0,0",
                "1999-01-04,9.10,9.20,9.30,9.00,11,101.00,0,0,0,0",
                "1999-01-05,2.10,2.20,2.30,2.00,12,102.00,0,0,0,0",
            ]
        }
    }
).encode()
SYMBOLS = ("600519.SH", "601318.SH", "000858.SZ")


def _bars_for(display_symbol: str) -> tuple[RawBar, ...]:
    """把固定报文解析成日线，供测试注入的抓取函数返回。

    注入点在 app.state.akshare_fetch，签名与真实 fetch_bars 一致：拿到的是
    已解析的原始日线，不再是自己去解析报文。
    """
    return parse_klines(PAYLOAD, display_symbol=display_symbol.upper(), adjustment="none")


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        InstrumentRepository(engine).upsert_reference(reference_instruments())
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM market_bars
                WHERE trade_date IN (DATE '1999-01-04', DATE '1999-01-05')
                """
            )
        )
        connection.execute(
            text(
                """
                DELETE FROM refresh_sessions
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text(
                """
                DELETE FROM user_preferences
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text("DELETE FROM users WHERE username = :username"),
            {"username": username},
        )
    engine.dispose()


def _token(client: TestClient, username: str) -> str:
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="admin",
    )
    engine.dispose()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200
    token = response.json()["data"]["access_token"]
    assert isinstance(token, str)
    return token


@pytest.mark.integration
def test_three_shares_can_be_queried_without_network() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t06-{uuid4().hex[:10]}"
    calls: list[str] = []

    async def fetch(
        display_symbol: str,
        start: date,
        end: date,
        adjustment: str,
    ) -> tuple[RawBar, ...]:
        del start, end, adjustment
        calls.append(display_symbol)
        return _bars_for(display_symbol)

    try:
        _cleanup(username)
        application = create_app()
        with TestClient(application) as client:
            application.state.akshare_fetch = fetch
            token = _token(client, username)
            for symbol in SYMBOLS:
                synced = client.post(
                    "/api/v1/admin/akshare-syncs",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "display_symbol": symbol,
                        "observation_start": "1999-01-04",
                        "observation_end": "1999-01-05",
                        "adjustment": "none",
                    },
                )
                assert synced.status_code == 200, synced.text
                body = synced.json()["data"]
                assert body["rows_written"] == 2
                assert body["duplicates_skipped"] == 1
                again = client.post(
                    "/api/v1/admin/akshare-syncs",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "display_symbol": symbol,
                        "observation_start": "1999-01-04",
                        "observation_end": "1999-01-05",
                        "adjustment": "none",
                    },
                )
                assert again.status_code == 200
            for symbol in SYMBOLS:
                early = _closes(client, symbol, "1999-01-03")
                first_day = _closes(client, symbol, "1999-01-04")
                both = _closes(client, symbol, "1999-01-05")
                assert early == []
                assert first_day == ["1.2"]
                assert both == ["1.2", "2.2"]
        assert calls == [
            "600519.SH",
            "600519.SH",
            "601318.SH",
            "601318.SH",
            "000858.SZ",
            "000858.SZ",
        ]
    finally:
        _cleanup(username)


@pytest.mark.integration
def test_upstream_failure_keeps_existing_bars() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t06-{uuid4().hex[:10]}"
    mode = {"fail": False}

    async def fetch(
        display_symbol: str,
        start: date,
        end: date,
        adjustment: str,
    ) -> tuple[RawBar, ...]:
        del display_symbol, start, end, adjustment
        if mode["fail"]:
            raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
        return _bars_for("600519.SH")

    try:
        _cleanup(username)
        application = create_app()
        with TestClient(application) as client:
            application.state.akshare_fetch = fetch
            token = _token(client, username)
            first = client.post(
                "/api/v1/admin/akshare-syncs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "display_symbol": "600519.SH",
                    "observation_start": "1999-01-04",
                    "observation_end": "1999-01-05",
                },
            )
            assert first.status_code == 200
            mode["fail"] = True
            failed = client.post(
                "/api/v1/admin/akshare-syncs",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "display_symbol": "600519.SH",
                    "observation_start": "1999-01-04",
                    "observation_end": "1999-01-05",
                },
            )
            assert failed.status_code == 504
            assert failed.json()["error"]["details"]["rows_written"] == 0
            assert failed.json()["error"]["details"]["preserved_existing"] is True
            assert _closes(client, "600519.SH", "1999-01-05") == ["1.2", "2.2"]
    finally:
        _cleanup(username)


def _closes(client: TestClient, symbol: str, as_of: str) -> list[str]:
    found = client.get("/api/v1/instruments/search", params={"q": symbol})
    assert found.status_code == 200
    results = found.json()["data"]["results"]
    assert len(results) == 1
    instrument_id = results[0]["id"]
    response = client.get(
        f"/api/v1/market/bars/{instrument_id}",
        params={"as_of": as_of, "start": "1999-01-04", "end": "1999-01-05", "adjustment": "none"},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["source"] == "akshare"
    assert body["display_symbol"] == symbol
    if not body["bars"]:
        assert "不会用演示价格填补" in body["empty_message"]
    return [item["close"] for item in body["bars"]]
