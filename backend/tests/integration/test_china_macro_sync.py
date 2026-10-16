import asyncio
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.providers import china_macro_sources
from app.providers.errors import ProviderError
from app.services.china_macro import CsvObservation
from app.services.data_sync import sync_china_macro
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

MARK = "pytest-fixture"
# 用 2099 年的观察月，和真实抓到的数据永远不会冲突；测试结束按来源标记删除。
OBSERVED = date(2099, 1, 1)


@pytest.fixture
def engine() -> Iterator[Engine]:
    db = create_db_engine(get_settings().database_url)
    try:
        with db.connect() as connection:
            connection.execute(text("SELECT 1"))
        from pathlib import Path

        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")
    except SQLAlchemyError:
        pytest.skip("PostgreSQL 不可用")
    yield db
    with db.begin() as connection:
        connection.execute(
            text("DELETE FROM macro_observations WHERE source_record_key LIKE :p"),
            {"p": f"%|{MARK}|%"},
        )
    db.dispose()


def _row(code: str, value: str) -> CsvObservation:
    return CsvObservation(
        series_code=code,
        observation_date=OBSERVED,
        value=Decimal(value),
        unit="Percent",
        source=MARK,
        published_at=china_macro_sources.published_upper_bound(code, OBSERVED),
    )


def _count(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM macro_observations WHERE source_record_key LIKE :p"),
                {"p": f"%|{MARK}|%"},
            ).scalar_one()
        )


def test_sync_is_idempotent_and_keeps_good_series_when_one_source_fails(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_eastmoney(client: object, spec: china_macro_sources.EastmoneySpec):
        if spec.series_code == "PPI_CN":
            raise ProviderError(code="UPSTREAM_TIMEOUT", message="超时", status_code=504)
        return (_row(spec.series_code, "1.5"),)

    async def fake_social(client: object):
        return (_row("SOCIAL_FINANCING", "1234"),)

    monkeypatch.setattr(china_macro_sources, "fetch_eastmoney", fake_eastmoney)
    monkeypatch.setattr(china_macro_sources, "fetch_social_financing", fake_social)

    with pytest.raises(ProviderError) as info:
        asyncio.run(sync_china_macro(engine))
    assert info.value.code == "UPSTREAM_PARTIAL"
    assert "PPI_CN" in info.value.message
    assert _count(engine) == 5  # 其余 4 个东方财富序列 + 社融已经写入

    async def healthy(client: object, spec: china_macro_sources.EastmoneySpec):
        return (_row(spec.series_code, "1.5"),)

    monkeypatch.setattr(china_macro_sources, "fetch_eastmoney", healthy)
    assert asyncio.run(sync_china_macro(engine)) == 1  # 只补上失败的那一个
    assert _count(engine) == 6
    assert asyncio.run(sync_china_macro(engine)) == 0  # 重放不重复写
    assert _count(engine) == 6


def test_published_at_never_precedes_observation(engine: Engine) -> None:
    del engine
    for code in china_macro_sources.PUBLISH_RULES:
        assert china_macro_sources.published_upper_bound(code, OBSERVED) > OBSERVED
