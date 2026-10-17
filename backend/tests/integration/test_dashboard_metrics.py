from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.repositories.dashboard_metrics import DashboardMetricRepository
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.integration

# 2099 年的日期不会和真实计算结果冲突；测试结束按日期删除。
DAY_1 = date(2099, 1, 2)
DAY_2 = date(2099, 1, 5)
DAY_3 = date(2099, 1, 6)


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
    with db.begin() as connection:
        connection.execute(
            text("DELETE FROM dashboard_daily_metrics WHERE trade_date >= :d"), {"d": DAY_1}
        )
    db.dispose()


def test_record_upserts_and_previous_reads_latest_earlier_day(engine: Engine) -> None:
    repo = DashboardMetricRepository(engine)
    now = datetime(2099, 1, 6, 7, 0, tzinfo=UTC)
    repo.record(DAY_1, {"temperature": Decimal("40.1"), "hot_boards": Decimal("5")}, now)
    repo.record(DAY_2, {"temperature": Decimal("45.0")}, now)
    repo.record(DAY_2, {"temperature": Decimal("47.5")}, now)
    repo.record(DAY_3, {"temperature": Decimal("60")}, now)

    found = repo.previous(["temperature", "hot_boards", "liquidity"], DAY_3)

    assert found["temperature"].trade_date == DAY_2
    assert found["temperature"].value == Decimal("47.5")
    assert found["hot_boards"].trade_date == DAY_1
    assert "liquidity" not in found


def test_previous_is_empty_without_history(engine: Engine) -> None:
    repo = DashboardMetricRepository(engine)
    repo.record(DAY_1, {}, datetime(2099, 1, 2, tzinfo=UTC))

    assert repo.previous(["temperature"], DAY_1) == {}
