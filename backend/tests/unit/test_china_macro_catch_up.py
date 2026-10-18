"""启动补跑：自动序列里有一条数据都没有的，就补登记一次中国宏观抓取。"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.core.config import Settings
from app.repositories.macro_china import SeriesStoredSummary
from app.repositories.sync_jobs import JobSnapshot
from app.services.china_macro import china_series_catalog
from app.services.scheduler import (
    ScheduledTask,
    catch_up_china_macro,
    china_macro_missing_series,
)
from sqlalchemy import Engine

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)
PROVIDER = uuid4()


def _summaries(*, empty: set[str]) -> list[SeriesStoredSummary]:
    return [
        SeriesStoredSummary(
            series_code=spec.series_code,
            observation_count=0 if spec.series_code in empty else 12,
            latest=None,
            first_observation_date=None if spec.series_code in empty else date(2025, 1, 1),
            first_published_at=None,
        )
        for spec in china_series_catalog()
    ]


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def enqueue(
        self,
        *,
        job_type: str,
        provider_id: UUID,
        scope: dict[str, object],
        dedupe_key: str,
        requested_by: UUID | None,
        now: datetime,
        max_attempts: int = 3,
    ) -> tuple[JobSnapshot, bool]:
        self.calls.append((job_type, dedupe_key))
        job = JobSnapshot(
            id=uuid4(),
            job_type=job_type,
            provider_id=provider_id,
            scope=scope,
            dedupe_key=dedupe_key,
            status="queued",
            attempts=0,
            max_attempts=max_attempts,
            rows_written=0,
            error_code=None,
            error_summary=None,
            progress_stage="queued",
            requested_at=now,
        )
        return job, True


def _builder(_engine: Engine, _settings: Settings, now: datetime) -> list[ScheduledTask]:
    return [
        ScheduledTask(
            task_name="sync_china_macro",
            job_type="china_macro",
            dedupe_key=f"china_macro:{now.date().isoformat()}",
            scope={"display_symbol": "china-macro"},
            provider_id=PROVIDER,
            failure_policy="test",
        )
    ]


def _run(summaries: list[SeriesStoredSummary], settings: Settings) -> tuple[int, _FakeEnqueuer]:
    enqueuer = _FakeEnqueuer()
    created = catch_up_china_macro(
        None,  # type: ignore[arg-type]  # 摘要、builder、enqueuer 都已注入，不会碰引擎
        settings,
        summaries=summaries,
        builder=_builder,
        enqueuer=enqueuer,
    )
    return created, enqueuer


def test_missing_series_lists_only_empty_auto_series() -> None:
    missing = china_macro_missing_series(
        _summaries(empty={"M1_CN", "PMI_NEW_ORDERS_CN", "INDUS_PROFIT_CN"})
    )

    assert len(missing) == 3
    assert set(missing) == {"M1_CN", "PMI_NEW_ORDERS_CN", "INDUS_PROFIT_CN"}


def test_manual_only_series_never_trigger_a_catch_up() -> None:
    manual = {spec.series_code for spec in china_series_catalog() if spec.manual_only}
    assert manual == {"SOCIAL_FINANCING_STOCK_CN", "ENT_MLT_LOAN_CN", "GOV_BOND_NET_CN"}

    assert china_macro_missing_series(_summaries(empty=manual)) == []
    created, enqueuer = _run(_summaries(empty=manual), Settings())
    assert created == 0 and enqueuer.calls == []


def test_catch_up_enqueues_one_sync_when_an_auto_series_is_empty() -> None:
    created, enqueuer = _run(_summaries(empty={"M1_CN"}), Settings())

    assert created == 1
    assert enqueuer.calls == [("china_macro", enqueuer.calls[0][1])]
    assert enqueuer.calls[0][1].startswith("china_macro:")


def test_catch_up_does_nothing_when_every_auto_series_has_data() -> None:
    created, enqueuer = _run(_summaries(empty=set()), Settings())

    assert created == 0 and enqueuer.calls == []


def test_catch_up_respects_the_scheduler_switch() -> None:
    settings = Settings(scheduler_enable_china_macro=False)

    created, enqueuer = _run(_summaries(empty={"M1_CN", "GDP_CN"}), settings)

    assert created == 0 and enqueuer.calls == []
