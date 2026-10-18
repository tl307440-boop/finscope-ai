import asyncio
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.providers import china_macro_sources, nbs_macro
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

    async def fake_nbs(client: object, request: nbs_macro.NbsRequest, today: date):
        if request.frequency == "quarterly":
            raise ProviderError(code="UPSTREAM_ERROR", message="被限流", status_code=302)
        return tuple(_row(item.series_code, "1.5") for item in request.indicators)

    monkeypatch.setattr(china_macro_sources, "fetch_eastmoney", fake_eastmoney)
    monkeypatch.setattr(china_macro_sources, "fetch_social_financing", fake_social)
    monkeypatch.setattr(nbs_macro, "fetch_nbs", fake_nbs)

    eastmoney_ok = {spec.series_code for spec in china_macro_sources.EASTMONEY_SPECS} - {"PPI_CN"}
    nbs_ok = {
        item.series_code
        for request in nbs_macro.NBS_REQUESTS
        if request.frequency != "quarterly"
        for item in request.indicators
    }
    first_pass = len(eastmoney_ok) + 1 + len(nbs_ok)  # + 社融；核心 CPI 两段只算一个序列

    with pytest.raises(ProviderError) as info:
        asyncio.run(sync_china_macro(engine, today=OBSERVED))
    assert info.value.code == "UPSTREAM_PARTIAL"
    assert "PPI_CN" in info.value.message
    assert "GDP_CN" in info.value.message
    assert _count(engine) == first_pass  # 失败的两个源不影响其余序列入库

    async def healthy_eastmoney(client: object, spec: china_macro_sources.EastmoneySpec):
        return (_row(spec.series_code, "1.5"),)

    async def healthy_nbs(client: object, request: nbs_macro.NbsRequest, today: date):
        return tuple(_row(item.series_code, "1.5") for item in request.indicators)

    monkeypatch.setattr(china_macro_sources, "fetch_eastmoney", healthy_eastmoney)
    monkeypatch.setattr(nbs_macro, "fetch_nbs", healthy_nbs)
    assert asyncio.run(sync_china_macro(engine, today=OBSERVED)) == 2  # 只补上 PPI 与 GDP
    assert _count(engine) == first_pass + 2
    assert asyncio.run(sync_china_macro(engine, today=OBSERVED)) == 0  # 重放不重复写
    assert _count(engine) == first_pass + 2


def test_published_at_never_precedes_observation(engine: Engine) -> None:
    del engine
    for code in china_macro_sources.PUBLISH_RULES:
        assert china_macro_sources.published_upper_bound(code, OBSERVED) > OBSERVED
