"""指数化基期月还没入库时，调度器补抓前缀，不靠每日 45 天窗口回头补。"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.catalog.macro_series import fred_codes
from app.core.config import Settings
from app.repositories.sync_jobs import JobSnapshot
from app.services.scheduler import (
    ScheduledTask,
    catch_up_fred_history,
    fred_history_backfill_scopes,
    history_backfill_window,
)
from app.services.sync_queue import dedupe_key_for
from sqlalchemy import Engine

TODAY = date(2026, 10, 19)
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
FRED = fred_codes()
PROVIDER = uuid4()


def test_gap_before_the_base_month_is_filled_up_to_the_day_before_earliest() -> None:
    assert history_backfill_window(date(2020, 10, 1), TODAY) == (
        date(2020, 1, 1),
        date(2020, 9, 30),
    )


def test_base_month_already_stored_needs_no_backfill() -> None:
    assert history_backfill_window(date(2020, 1, 1), TODAY) is None
    assert history_backfill_window(date(2020, 1, 31), TODAY) is None
    assert history_backfill_window(date(1947, 1, 1), TODAY) is None


def test_empty_series_waits_for_the_daily_window() -> None:
    assert history_backfill_window(None, TODAY) is None


def test_scopes_skip_non_fred_series_and_a_window_that_already_succeeded() -> None:
    earliest = {"CPIAUCSL": date(2020, 10, 1), "ISM_MFG": date(2020, 10, 1)}
    draft = fred_history_backfill_scopes(
        ["CPIAUCSL", "ISM_MFG"],
        earliest,
        TODAY,
        fred_only=FRED,
    )
    assert [item["series_code"] for item in draft] == ["CPIAUCSL"]
    done = frozenset({dedupe_key_for("fred_series", draft[0])})
    assert (
        fred_history_backfill_scopes(
            ["CPIAUCSL", "ISM_MFG"],
            earliest,
            TODAY,
            fred_only=FRED,
            already_succeeded=done,
        )
        == []
    )


class _FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[str] = []

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
        del provider_id, scope, requested_by, now, max_attempts
        self.calls.append(job_type)
        job = JobSnapshot(
            id=uuid4(),
            job_type=job_type,
            provider_id=PROVIDER,
            scope={},
            dedupe_key=dedupe_key,
            status="queued",
            attempts=0,
            max_attempts=3,
            rows_written=0,
            error_code=None,
            error_summary=None,
            progress_stage="queued",
            requested_at=NOW,
        )
        return job, True


def _builder(_engine: Engine, _settings: Settings, _now: datetime) -> list[ScheduledTask]:
    return [
        ScheduledTask(
            task_name="sync_fred_series",
            job_type="fred_series",
            dedupe_key="fred:CPIAUCSL:2020-01-01:2020-09-30",
            scope={"series_code": "CPIAUCSL"},
            provider_id=PROVIDER,
            failure_policy="test",
        )
    ]


def test_startup_catch_up_enqueues_the_backfill() -> None:
    enqueuer = _FakeEnqueuer()
    created = catch_up_fred_history(
        None,  # type: ignore[arg-type]
        Settings(),
        builder=_builder,
        enqueuer=enqueuer,
    )
    assert created == 1
    assert enqueuer.calls == ["fred_series"]


def test_startup_catch_up_skips_when_fred_sync_is_disabled() -> None:
    enqueuer = _FakeEnqueuer()
    created = catch_up_fred_history(
        None,  # type: ignore[arg-type]
        Settings(scheduler_enable_fred=False),
        builder=_builder,
        enqueuer=enqueuer,
    )
    assert created == 0
    assert enqueuer.calls == []
