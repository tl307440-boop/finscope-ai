import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, Select, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.provider import DataProvider, SyncJob, SyncJobEvent
from app.services.sync_queue import LEASE_SECONDS, public_failure, should_requeue


@dataclass(frozen=True)
class JobSnapshot:
    id: UUID
    job_type: str
    provider_id: UUID
    scope: dict[str, object]
    dedupe_key: str
    status: str
    attempts: int
    max_attempts: int
    rows_written: int
    error_code: str | None
    error_summary: str | None
    progress_stage: str
    requested_at: datetime


@dataclass(frozen=True)
class ProviderHealth:
    status: str | None
    checked_at: datetime | None


class SyncJobRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

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
        try:
            with Session(self.engine) as session, session.begin():
                existing = _active(session, dedupe_key)
                if existing is not None:
                    return _snapshot(existing), False
                job = SyncJob(
                    id=uuid.uuid4(),
                    job_type=job_type,
                    provider_id=provider_id,
                    scope=scope,
                    dedupe_key=dedupe_key,
                    status="queued",
                    priority=0,
                    requested_by=requested_by,
                    requested_at=now,
                    started_at=None,
                    finished_at=None,
                    attempts=0,
                    max_attempts=max_attempts,
                    rows_written=0,
                    error_code=None,
                    error_summary=None,
                    progress={"stage": "queued"},
                    lease_expires_at=None,
                )
                session.add(job)
                _event(session, job, now, "info", "queued", "任务已排队")
                session.flush()
                return _snapshot(job), True
        except IntegrityError:
            with Session(self.engine) as session:
                existing = _active(session, dedupe_key)
                if existing is None:
                    raise
                return _snapshot(existing), False

    def claim(self, now: datetime) -> JobSnapshot | None:
        with Session(self.engine) as session, session.begin():
            job = session.scalars(_claim_statement(now)).first()
            if job is None:
                return None
            nxt = job.attempts + 1
            if nxt > job.max_attempts:
                job.status = "failed"
                job.finished_at = now
                job.lease_expires_at = None
                job.error_code = "TASK_FAILED"
                job.error_summary = "任务执行失败"
                _event(session, job, now, "error", "failed", "超过重试次数")
                _health(session, job.provider_id, "down", now)
                return None
            job.attempts = nxt
            job.status = "running"
            job.started_at = job.started_at or now
            job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            job.progress = {"stage": "running"}
            _event(session, job, now, "info", "running", "开始执行")
            session.flush()
            return _snapshot(job)

    def hold_claim(self, now: datetime) -> tuple[Session, JobSnapshot | None]:
        """测试用来把行锁留在未提交事务里。调用方负责关闭。"""

        session = Session(self.engine)
        session.begin()
        job = session.scalars(_claim_statement(now)).first()
        if job is None:
            return session, None
        job.status = "running"
        job.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        session.flush()
        return session, _snapshot(job)

    def finish_success(self, job_id: UUID, rows_written: int, now: datetime) -> None:
        with Session(self.engine) as session, session.begin():
            job = session.get(SyncJob, job_id)
            if job is None or job.status != "running":
                return
            job.status = "succeeded"
            job.finished_at = now
            job.lease_expires_at = None
            job.rows_written = rows_written
            job.error_code = None
            job.error_summary = None
            job.progress = {"stage": "succeeded", "rows_written": rows_written}
            _event(session, job, now, "info", "succeeded", "任务完成")
            _health(session, job.provider_id, "up", now)

    def finish_failure(self, job_id: UUID, exc: BaseException, now: datetime) -> None:
        code, message = public_failure(exc)
        with Session(self.engine) as session, session.begin():
            job = session.get(SyncJob, job_id)
            if job is None or job.status != "running":
                return
            if should_requeue(
                attempts=job.attempts, max_attempts=job.max_attempts, error_code=code
            ):
                job.status = "queued"
                job.lease_expires_at = None
                job.error_code = code
                job.error_summary = message
                job.progress = {"stage": "retry"}
                _event(session, job, now, "warning", "retry", "将重试")
                return
            job.status = "failed"
            job.finished_at = now
            job.lease_expires_at = None
            job.error_code = code
            job.error_summary = message
            job.progress = {"stage": "failed"}
            _event(session, job, now, "error", "failed", message)
            _health(session, job.provider_id, "down", now)

    def get(self, job_id: UUID) -> JobSnapshot | None:
        with Session(self.engine) as session:
            job = session.get(SyncJob, job_id)
            if job is None:
                return None
            return _snapshot(job)

    def list_recent(self, limit: int) -> list[JobSnapshot]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SyncJob).order_by(SyncJob.requested_at.desc()).limit(limit)
            ).all()
            return [_snapshot(row) for row in rows]

    def provider_health(self) -> dict[str, ProviderHealth]:
        with Session(self.engine) as session:
            rows = session.scalars(select(DataProvider)).all()
            return {
                row.code: ProviderHealth(
                    status=row.last_health_status, checked_at=row.last_health_at
                )
                for row in rows
            }


def pump_once(
    repository: SyncJobRepository,
    now: datetime,
    runner: Callable[[JobSnapshot], int],
) -> bool:
    job = repository.claim(now)
    if job is None:
        return False
    try:
        written = runner(job)
    except Exception as exc:
        repository.finish_failure(job.id, exc, now)
    else:
        repository.finish_success(job.id, written, now)
    return True


def _claim_statement(now: datetime) -> Select[SyncJob]:
    return (
        select(SyncJob)
        .where(
            or_(
                SyncJob.status == "queued",
                (SyncJob.status == "running") & (SyncJob.lease_expires_at < now),
            )
        )
        .order_by(SyncJob.priority.desc(), SyncJob.requested_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def _active(session: Session, dedupe_key: str) -> SyncJob | None:
    return session.scalars(
        select(SyncJob).where(
            SyncJob.dedupe_key == dedupe_key,
            SyncJob.status.in_(("queued", "running")),
        )
    ).first()


def _event(
    session: Session,
    job: SyncJob,
    now: datetime,
    level: str,
    stage: str,
    message: str,
) -> None:
    session.add(
        SyncJobEvent(
            id=uuid.uuid4(),
            job_id=job.id,
            event_time=now,
            level=level,
            stage=stage,
            message=message,
            meta={},
        )
    )


def _health(session: Session, provider_id: UUID, status: str, now: datetime) -> None:
    provider = session.get(DataProvider, provider_id)
    if provider is None:
        return
    provider.last_health_status = status
    provider.last_health_at = now


def _snapshot(job: SyncJob) -> JobSnapshot:
    stage = job.progress.get("stage")
    return JobSnapshot(
        id=job.id,
        job_type=job.job_type,
        provider_id=job.provider_id,
        scope=dict(job.scope),
        dedupe_key=job.dedupe_key,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        rows_written=job.rows_written,
        error_code=job.error_code,
        error_summary=job.error_summary,
        progress_stage=stage if isinstance(stage, str) else "queued",
        requested_at=job.requested_at,
    )
