import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.db.models.account import User
from app.db.models.market import Instrument, MarketBar
from app.db.models.provider import DataProvider
from app.db.session import create_db_engine
from app.repositories.watchlists import WatchlistRepository
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

# 699xxx / 399xxx 不是真实 A 股代码，不会和真实入库数据冲突。
CODES = (("SSE", "699901"), ("SZSE", "399902"))
MOMENT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    db = create_db_engine(get_settings().database_url)
    try:
        with db.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    except SQLAlchemyError:
        pytest.skip("PostgreSQL 不可用")
    yield db
    probe_users = "SELECT id FROM users WHERE username LIKE 'wl-import-%'"
    probe_instruments = "SELECT id FROM instruments WHERE symbol IN ('699901','399902')"
    with db.begin() as connection:
        for statement in (
            "DELETE FROM watchlist_items WHERE group_id IN "
            f"(SELECT id FROM watchlist_groups WHERE user_id IN ({probe_users}))",
            f"DELETE FROM watchlist_groups WHERE user_id IN ({probe_users})",
            f"DELETE FROM market_bars WHERE instrument_id IN ({probe_instruments})",
            "DELETE FROM instruments WHERE symbol IN ('699901','399902')",
            "DELETE FROM users WHERE username LIKE 'wl-import-%'",
            "DELETE FROM data_providers WHERE code LIKE 'wl-import-%'",
        ):
            connection.execute(text(statement))
    db.dispose()


def _user(session: Session) -> uuid.UUID:
    user_id = uuid.uuid4()
    session.add(
        User(
            id=user_id,
            username=f"wl-import-{user_id.hex[:8]}",
            email=f"{user_id.hex[:8]}@example.com",
            password_hash="x",
            role="researcher",
            is_active=True,
            created_at=MOMENT,
            updated_at=MOMENT,
        )
    )
    return user_id


def _seed(engine: Engine) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    ids: dict[str, uuid.UUID] = {}
    with Session(engine) as session:
        owner, stranger = _user(session), _user(session)
        session.flush()
        for exchange, symbol in CODES:
            ids[symbol] = uuid.uuid4()
            session.add(
                Instrument(
                    id=ids[symbol],
                    market="CN",
                    exchange=exchange,
                    symbol=symbol,
                    display_symbol=f"{symbol}.{'SH' if exchange == 'SSE' else 'SZ'}",
                    name_cn=f"样例{symbol}",
                    name_en="probe",
                    instrument_type="STOCK",
                    currency="CNY",
                    industry_id=None,
                    is_active=True,
                    list_date=None,
                    delist_date=None,
                    created_at=MOMENT,
                    updated_at=MOMENT,
                )
            )
        session.commit()
    return owner, stranger, ids


def test_batch_import_reports_added_existing_missing_and_respects_owner(engine: Engine) -> None:
    owner, stranger, _ = _seed(engine)
    repository = WatchlistRepository(engine)
    group = repository.create_group(owner, "导入测试")
    assert group is not None

    outcome = repository.add_codes(owner, group.id, [CODES[0], ("SSE", "699999")])
    assert outcome is not None
    assert outcome.added == ("699901.SH",) and outcome.missing == ("699999",)

    again = repository.add_codes(owner, group.id, list(CODES))
    assert again is not None
    assert again.existing == ("699901.SH",) and again.added == ("399902.SZ",)

    assert repository.add_codes(stranger, group.id, list(CODES)) is None
    assert repository.get_group(stranger, group.id) is None
    loaded = repository.get_group(owner, group.id)
    assert loaded is not None
    assert {(item.exchange, item.symbol) for item in loaded.items} == set(CODES)


def test_recent_closes_reads_unadjusted_daily_bars(engine: Engine) -> None:
    _, _, ids = _seed(engine)
    provider_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            DataProvider(
                id=provider_id,
                code=f"wl-import-{provider_id.hex[:8]}",
                name="probe",
                enabled=True,
                config_public={},
                last_health_at=None,
                last_health_status=None,
            )
        )
        session.flush()
        for day, close, adjustment in (
            (date(2026, 1, 5), "10", "none"),
            (date(2026, 1, 6), "11", "none"),
            (date(2026, 1, 6), "99", "qfq"),
        ):
            value = Decimal(close)
            session.add(
                MarketBar(
                    id=uuid.uuid4(),
                    instrument_id=ids["699901"],
                    trade_date=day,
                    timeframe="1d",
                    adjustment=adjustment,
                    open=value,
                    high=value,
                    low=value,
                    close=value,
                    volume=Decimal(1),
                    amount=None,
                    turnover_rate=None,
                    provider_id=provider_id,
                    fetched_at=MOMENT,
                    quality_flag="ok",
                )
            )
        session.commit()
    closes = WatchlistRepository(engine).recent_closes(list(ids.values()), date(2026, 1, 1))
    assert [row.close for row in closes[ids["699901"]]] == [Decimal("10"), Decimal("11")]
    assert ids["399902"] not in closes
