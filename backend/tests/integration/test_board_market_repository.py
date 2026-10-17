from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.providers import eastmoney_boards as em
from app.repositories.board_market import BoardMarketRepository
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.integration

# 2099 年的日期与 BK9xxx 代码不会和真实入库数据冲突；测试结束按代码删除。
DAY = date(2099, 1, 2)
CODE = "BK9901"
SECID = f"90.{CODE}"


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
            text("DELETE FROM board_daily_snapshots WHERE board_code = :c"), {"c": CODE}
        )
        connection.execute(text("DELETE FROM board_daily_bars WHERE secid = :s"), {"s": SECID})
    db.dispose()


def _quote(change: str | None) -> em.BoardQuote:
    return em.BoardQuote(
        code=CODE,
        name="测试板块",
        last=Decimal("1000.5"),
        change_pct=None if change is None else Decimal(change),
        change_5d_pct=None,
        change_10d_pct=None,
        change_60d_pct=None,
        change_ytd_pct=None,
        amount=None,
        turnover_pct=None,
        pe_ttm=None,
        market_cap=Decimal("123456789012"),
        main_net_inflow=Decimal("-1000"),
        main_net_inflow_5d=None,
        advancers=3,
        decliners=None,
        unchanged=None,
        leader_code=None,
        leader_name=None,
        leader_change_pct=None,
    )


def _bar(day: date, close: str) -> em.BoardBar:
    value = Decimal(close)
    return em.BoardBar(day, value, value, value, value, Decimal(10), Decimal(20))


def test_snapshot_upsert_is_idempotent_and_keeps_nulls(engine: Engine) -> None:
    repository = BoardMarketRepository(engine)
    node = em.BoardNode(code=CODE, name="测试板块", kind="industry", level=2)
    fetched = datetime(2099, 1, 2, 8, tzinfo=UTC)
    repository.upsert_snapshots(DAY, [(node, _quote("1.25"))], fetched)
    repository.upsert_snapshots(DAY, [(node, _quote("-0.5"))], fetched)
    rows = repository.snapshots(DAY, "industry", 2)
    mine = [row for row in rows if row.node.code == CODE]
    assert len(mine) == 1
    assert mine[0].quote.change_pct == Decimal("-0.5")
    assert mine[0].quote.decliners is None and mine[0].quote.pe_ttm is None
    assert repository.latest_snapshot_date(DAY) == DAY
    stored = repository.latest_snapshot(CODE)
    assert stored is not None and stored.trade_date == DAY


def test_bars_upsert_overwrites_same_day_and_reads_range(engine: Engine) -> None:
    repository = BoardMarketRepository(engine)
    fetched = datetime(2099, 1, 3, 8, tzinfo=UTC)
    repository.upsert_bars(SECID, [_bar(date(2099, 1, 1), "10"), _bar(DAY, "11")], fetched)
    repository.upsert_bars(SECID, [_bar(DAY, "12")], fetched)
    assert repository.last_bar_dates([SECID]) == {SECID: DAY}
    stored = repository.bars(SECID, date(2098, 12, 1))
    assert [bar.close for bar in stored.bars] == [Decimal("10"), Decimal("12")]
    assert repository.bars(SECID, date(2099, 1, 2), until=date(2099, 1, 2)).bars[0].close == 12
    assert repository.bars("90.BK9999", date(2098, 1, 1)).bars == ()
