import logging
import os
import signal
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine
from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class HeartbeatWriter(Protocol):
    def upsert(self, *, status: str, beat_at: datetime, pid: int) -> None: ...


def serve(
    writer: HeartbeatWriter,
    stop_event: threading.Event,
    interval_seconds: float,
    clock: Callable[[], datetime],
    pid: int,
) -> None:
    while not stop_event.is_set():
        try:
            writer.upsert(status="alive", beat_at=clock(), pid=pid)
        except Exception:
            logger.warning("heartbeat write failed error_type=unexpected")
        if stop_event.wait(interval_seconds):
            break
    try:
        writer.upsert(status="stopped", beat_at=clock(), pid=pid)
    except Exception:
        logger.warning("heartbeat stop write failed error_type=unexpected")


def serve_with_jobs(
    writer: HeartbeatWriter,
    pump: Callable[[datetime], None],
    stop_event: threading.Event,
    interval_seconds: float,
    clock: Callable[[], datetime],
    pid: int,
) -> None:
    while not stop_event.is_set():
        try:
            writer.upsert(status="alive", beat_at=clock(), pid=pid)
        except Exception:
            logger.warning("heartbeat write failed error_type=unexpected")
        try:
            pump(clock())
        except Exception:
            logger.warning("job pump failed error_type=unexpected")
        if stop_event.wait(interval_seconds):
            break
    try:
        writer.upsert(status="stopped", beat_at=clock(), pid=pid)
    except Exception:
        logger.warning("heartbeat stop write failed error_type=unexpected")


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    writer = WorkerHeartbeatRepository(engine, settings.worker_service_name)
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("worker stop requested signal=%s", signum)
        stop_event.set()

    def pump(now: datetime) -> None:
        from pathlib import Path

        from app.repositories.sync_jobs import JobSnapshot, SyncJobRepository, pump_once
        from app.services.document_index import index_next_queued
        from app.workers.jobs import execute_job

        def run(job: JobSnapshot) -> int:
            return execute_job(engine, job)

        pump_once(SyncJobRepository(engine), now, run)
        index_next_queued(engine, Path("data/uploads"))

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        serve_with_jobs(
            writer,
            pump,
            stop_event,
            settings.worker_heartbeat_interval_seconds,
            utc_now,
            os.getpid(),
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
