"""自定义数据分析仓库：真实 PostgreSQL 上的 upsert 幂等、质量闸门过滤与读取。"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.providers.exchange_options import OptionStat
from app.repositories.custom_analysis import CustomAnalysisRepository, SeriesPointIn
from app.services.custom_analysis_rules import QualityResult
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.integration

# 2099 年的日期与 T99xx 代码不会和真实入库数据冲突；测试结束按代码删除。
DAY1 = date(2099, 1, 4)
DAY2 = date(2099, 1, 5)
CODE = "T9901"
SERIES = "TEST_SERIES_9901"
FETCHED = datetime(2099, 1, 5, 11, 0, tzinfo=UTC)


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
            text("DELETE FROM option_daily_stats WHERE underlying_code = :c"), {"c": CODE}
        )
        connection.execute(
            text("DELETE FROM custom_series_points WHERE series_code = :s"), {"s": SERIES}
        )
    db.dispose()


def _stat(day: date, put: int = 400, call: int = 600) -> OptionStat:
    return OptionStat(
        trade_date=day,
        exchange="SSE",
        underlying_code=CODE,
        underlying_name="测试标的",
        call_volume=call,
        put_volume=put,
        call_oi=800,
        put_oi=500,
        reported_ratio=Decimal("66.67"),
        reported_ratio_kind="volume",
        reported_total_volume=call + put,
        source="test",
    )


def test_option_upsert_is_idempotent_and_latest_write_wins(engine: Engine) -> None:
    repository = CustomAnalysisRepository(engine)
    ok = QualityResult("ok")

    assert repository.upsert_option_stats([(_stat(DAY1), ok), (_stat(DAY2), ok)], FETCHED) == 2
    assert repository.upsert_option_stats([(_stat(DAY2, put=450), ok)], FETCHED) == 1

    rows = repository.option_rows([("SSE", CODE)])
    assert [(row.trade_date, row.put_volume, row.call_volume) for row in rows] == [
        (DAY1, 400, 600),
        (DAY2, 450, 600),
    ]
    assert {DAY1, DAY2} <= repository.option_days("SSE")
    assert [row.trade_date for row in repository.option_rows([("SSE", CODE)], start=DAY2)] == [DAY2]


def test_rejected_option_rows_are_never_stored(engine: Engine) -> None:
    repository = CustomAnalysisRepository(engine)

    written = repository.upsert_option_stats(
        [
            (_stat(DAY1), QualityResult("rejected", "成交量为负")),
            (_stat(DAY2), QualityResult("review", "与交易所比值不一致")),
        ],
        FETCHED,
    )

    assert written == 1
    rows = repository.option_rows([("SSE", CODE)])
    assert [(row.trade_date, row.quality_flag) for row in rows] == [(DAY2, "review")]
    notes = [item for item in repository.option_review_notes(limit=500) if item[2] == CODE]
    assert notes == [(DAY2, "SSE", CODE, "与交易所比值不一致")]


def test_option_rows_with_no_members_return_nothing(engine: Engine) -> None:
    assert CustomAnalysisRepository(engine).option_rows([]) == []


def test_series_upsert_replaces_value_meta_and_flag(engine: Engine) -> None:
    repository = CustomAnalysisRepository(engine)
    repository.upsert_points(
        SERIES, [SeriesPointIn(DAY1, Decimal("1.5"), "test", "ok", {"unit": "x"})], FETCHED
    )
    repository.upsert_points(
        SERIES,
        [
            SeriesPointIn(DAY1, Decimal("2.5"), "test", "review", {"note": "单日变化过大"}),
            SeriesPointIn(DAY2, Decimal("3.5"), "test", "ok"),
        ],
        FETCHED,
    )

    points = repository.series(SERIES)
    assert [(p.trade_date, p.value, p.quality_flag) for p in points] == [
        (DAY1, Decimal("2.5000"), "review"),
        (DAY2, Decimal("3.5000"), "ok"),
    ]
    assert points[0].meta == {"note": "单日变化过大"}
    assert points[1].meta is None
    assert repository.series_dates(SERIES) == {DAY1, DAY2}
    assert [p.trade_date for p in repository.series(SERIES, start=DAY2)] == [DAY2]


def test_series_summary_counts_rows_and_review_rows(engine: Engine) -> None:
    repository = CustomAnalysisRepository(engine)
    repository.upsert_points(
        SERIES,
        [
            SeriesPointIn(DAY1, Decimal("1"), "test", "ok"),
            SeriesPointIn(DAY2, Decimal("2"), "test", "review", {"note": "待审"}),
        ],
        FETCHED,
    )

    summary = repository.series_summaries()[SERIES]
    assert (summary.rows, summary.review_rows) == (2, 1)
    assert (summary.first_date, summary.last_date) == (DAY1, DAY2)
    assert (DAY2, SERIES, "待审") in repository.series_review_notes(limit=500)


def test_empty_series_reads_are_empty_not_errors(engine: Engine) -> None:
    repository = CustomAnalysisRepository(engine)
    assert repository.upsert_points(SERIES, [], FETCHED) == 0
    assert repository.series(SERIES) == []
    assert repository.series_dates(SERIES) == set()
    assert SERIES not in repository.series_summaries()
