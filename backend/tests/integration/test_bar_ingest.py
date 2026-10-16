from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.models.market import Instrument
from app.db.models.provider import DataProvider
from app.db.session import create_db_engine
from app.main import create_app
from app.providers.errors import ProviderError
from app.providers.quality import CheckedBar
from app.repositories.accounts import AccountRepository
from app.repositories.market_bars import MarketBarRepository
from app.services.ingest import ingest_bars
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _sample(close: str, *, adjustment: str = "none", high: str = "11") -> CheckedBar:
    return CheckedBar(
        trade_date=date(2024, 1, 2),
        open=Decimal("10"),
        high=Decimal(high),
        low=Decimal("9"),
        close=Decimal(close),
        volume=Decimal("100"),
        amount=Decimal("1000"),
        adjustment=adjustment,  # type: ignore[arg-type]
        currency="CNY",
        unit="price",
        source_record_key="db-1",
        quality_flag="ok",
    )


@pytest.mark.integration
def test_failed_ingest_and_bad_revision_do_not_replace_good_bar() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    provider_id = uuid4()
    instrument_id = uuid4()
    repository = MarketBarRepository(engine)
    try:
        with Session(engine) as session:
            moment = datetime(2024, 1, 1, tzinfo=UTC)
            session.add(
                DataProvider(
                    id=provider_id,
                    code=f"probe-{provider_id.hex[:8]}",
                    name="probe",
                    enabled=True,
                    config_public={},
                    last_health_at=None,
                    last_health_status=None,
                )
            )
            session.add(
                Instrument(
                    id=instrument_id,
                    market="CN",
                    exchange="SSE",
                    symbol=f"T{provider_id.hex[:6]}",
                    display_symbol=f"T{provider_id.hex[:6]}.SH",
                    name_cn="探测样例",
                    name_en="probe",
                    instrument_type="STOCK",
                    currency="CNY",
                    industry_id=None,
                    is_active=True,
                    list_date=None,
                    delist_date=None,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            session.commit()
        assert (
            repository.upsert(
                [_sample("10.5")],
                instrument_id=instrument_id,
                provider_id=provider_id,
            )
            == 1
        )
        failed = ingest_bars(
            ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504),
            repository,
            instrument_id=instrument_id,
            provider_id=provider_id,
        )
        assert failed.rows_written == 0
        stored = repository.get_close(
            instrument_id=instrument_id,
            provider_id=provider_id,
            trade_date=date(2024, 1, 2),
            adjustment="none",
        )
        assert stored is not None
        assert Decimal(stored[0]) == Decimal("10.5")
        assert stored[1] == "ok"
        review = _sample("10.2").model_copy(update={"quality_flag": "review"})
        assert (
            repository.upsert(
                [review],
                instrument_id=instrument_id,
                provider_id=provider_id,
            )
            == 0
        )
        stored = repository.get_close(
            instrument_id=instrument_id,
            provider_id=provider_id,
            trade_date=date(2024, 1, 2),
            adjustment="none",
        )
        assert stored is not None
        assert Decimal(stored[0]) == Decimal("10.5")
        assert (
            repository.upsert(
                [_sample("10.8")],
                instrument_id=instrument_id,
                provider_id=provider_id,
            )
            == 1
        )
        stored = repository.get_close(
            instrument_id=instrument_id,
            provider_id=provider_id,
            trade_date=date(2024, 1, 2),
            adjustment="none",
        )
        assert stored is not None
        assert Decimal(stored[0]) == Decimal("10.8")
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM market_bars WHERE instrument_id = :instrument_id"),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text("DELETE FROM instruments WHERE id = :instrument_id"),
                {"instrument_id": instrument_id},
            )
            # 探测用数据源也要清掉：漏了这一句，每跑一次集成就往 data_providers
            # 里落一条 probe-xxxxxxxx，跑几十次之后数据中心页会列出一堆僵尸源。
            connection.execute(
                text("DELETE FROM data_providers WHERE id = :provider_id"),
                {"provider_id": provider_id},
            )
        engine.dispose()


@pytest.mark.integration
def test_daily_limit_keeps_the_most_recent_bars() -> None:
    """limit 是「最近的 N 条」，不是「最早的 N 条」。

    反了的话，公司研究页拿 limit=60 就会把两三年前那一段的收盘价当成「最新收盘」。
    """
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    engine = create_db_engine(get_settings().database_url)
    provider_id = uuid4()
    instrument_id = uuid4()
    repository = MarketBarRepository(engine)
    dates = [date(2024, 1, day) for day in (2, 3, 4, 5, 8)]
    try:
        with Session(engine) as session:
            moment = datetime(2024, 1, 1, tzinfo=UTC)
            session.add(
                DataProvider(
                    id=provider_id,
                    code=f"probe-{provider_id.hex[:8]}",
                    name="probe",
                    enabled=True,
                    config_public={},
                    last_health_at=None,
                    last_health_status=None,
                )
            )
            session.add(
                Instrument(
                    id=instrument_id,
                    market="CN",
                    exchange="SSE",
                    symbol=f"L{provider_id.hex[:6]}",
                    display_symbol=f"L{provider_id.hex[:6]}.SH",
                    name_cn="探测样例",
                    name_en="probe",
                    instrument_type="STOCK",
                    currency="CNY",
                    industry_id=None,
                    is_active=True,
                    list_date=None,
                    delist_date=None,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            session.commit()
        bars = [
            _sample(str(10 + index)).model_copy(update={"trade_date": trade_date})
            for index, trade_date in enumerate(dates)
        ]
        written = repository.upsert(bars, instrument_id=instrument_id, provider_id=provider_id)
        assert written == len(bars)
        rows, truncated = repository.list_daily(
            instrument_id=instrument_id,
            provider_id=provider_id,
            adjustment="none",
            start=None,
            end=None,
            pit=date(2025, 1, 1),
            limit=2,
        )
        assert truncated is True
        assert [row.trade_date for row in rows] == [dates[-2], dates[-1]]
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM market_bars WHERE instrument_id = :instrument_id"),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text("DELETE FROM instruments WHERE id = :instrument_id"),
                {"instrument_id": instrument_id},
            )
            connection.execute(
                text("DELETE FROM data_providers WHERE id = :provider_id"),
                {"provider_id": provider_id},
            )
        engine.dispose()


@pytest.mark.integration
def test_admin_probe_reports_timeout_without_writing_rows() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t03-{uuid4().hex[:10]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="admin",
    )
    engine.dispose()
    try:
        with TestClient(create_app()) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            response = client.post(
                "/api/v1/admin/provider-probes",
                headers={"Authorization": f"Bearer {token}"},
                json={"scenario": "timeout"},
            )
        assert response.status_code == 504
        body = response.json()
        assert body["error"]["code"] == "UPSTREAM_TIMEOUT"
        assert body["error"]["details"]["rows_written"] == 0
        assert body["error"]["details"]["preserved_existing"] is True
        assert "correct-password" not in response.text
    finally:
        cleanup = create_db_engine(get_settings().database_url)
        with cleanup.begin() as connection:
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
        cleanup.dispose()
