"""调度器进程入口：``python -m app.workers.scheduler``。

只跑调度器，不跑任务本体——任务由 ``python -m app.workers.main`` 里的 worker 取走执行。
两个进程可以各自单独重启：调度器停了只是不再登记新任务，已经在 queued 里的任务照旧会跑完。

Ctrl+C（SIGINT）或 SIGTERM 会走 ``scheduler.shutdown(wait=True)``：等在执行的触发回调收尾，
然后按 ``stopped`` 写一条心跳，最后返回 0。
"""

import logging
import os
import signal
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import Engine

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine
from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.services.scheduler import (
    RegisteredJob,
    build_scheduler,
    catch_up_universe,
    register_jobs,
)
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

SCHEDULER_SERVICE_NAME = "scheduler"
# 主线程只负责等信号；这个上限防止在极端情况下永久挂住。
IDLE_JOIN_SECONDS = 1.0


class HeartbeatWriter(Protocol):
    def upsert(self, *, status: str, beat_at: datetime, pid: int) -> None: ...


def log_registered(jobs: list[RegisteredJob]) -> None:
    if not jobs:
        logger.warning("scheduler registered 0 jobs; check SCHEDULER_ENABLE_* switches")
        return
    for job in jobs:
        logger.info(
            "scheduler registered job_id=%s task=%s trigger=%s failure_policy=%s",
            job.job_id,
            job.task_name,
            job.trigger,
            job.failure_policy,
        )
    logger.info("scheduler registered %s jobs", len(jobs))


def run(
    *,
    settings: Settings | None = None,
    stop_event: threading.Event | None = None,
    clock: Callable[[], datetime] = utc_now,
    pid: int | None = None,
    scheduler: BackgroundScheduler | None = None,
    writer: HeartbeatWriter | None = None,
) -> int:
    """启动调度器并阻塞到收到停止信号；返回进程退出码。"""

    resolved = settings or get_settings()
    configure_logging(resolved.log_level)
    if not resolved.scheduler_enabled:
        logger.warning("scheduler disabled by SCHEDULER_ENABLED=false; exiting")
        return 0
    engine = create_db_engine(resolved.database_url)
    beats = writer or WorkerHeartbeatRepository(engine, SCHEDULER_SERVICE_NAME)
    stop = stop_event or threading.Event()
    process_id = os.getpid() if pid is None else pid
    runner = scheduler or build_scheduler(resolved)
    try:
        _beat(beats, "alive", clock, process_id)
        jobs = register_jobs(runner, resolved)
        log_registered(jobs)
        _catch_up(engine, resolved)
        # 后台调度器在自己的线程池里触发任务，主线程只等 stop_event。
        runner.start()
        while not stop.wait(IDLE_JOIN_SECONDS):
            _beat(beats, "alive", clock, process_id)
        _beat(beats, "stopped", clock, process_id)
        logger.info("scheduler stopped jobs=%s", len(runner.get_jobs()))
        return 0
    finally:
        if runner.running:
            # wait=True：等在执行的触发回调收尾，避免半途留下没写完的 queued 记录。
            runner.shutdown(wait=True)
        engine.dispose()


def _catch_up(engine: Engine, settings: Settings) -> None:
    try:
        queued = catch_up_universe(engine, settings)
        if queued:
            logger.info("scheduler queued universe catch-up jobs=%s", queued)
    except Exception:
        # 补登记失败不能拦住调度器本身；周日的定时触发仍会补上。
        logger.warning("scheduler universe catch-up failed error_type=unexpected")


def _beat(
    writer: HeartbeatWriter,
    status: str,
    clock: Callable[[], datetime],
    pid: int,
) -> None:
    try:
        writer.upsert(status=status, beat_at=clock(), pid=pid)
    except Exception:
        logger.warning("scheduler heartbeat write failed status=%s error_type=unexpected", status)


def main() -> int:
    settings = get_settings()
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("scheduler stop requested signal=%s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    return run(settings=settings, stop_event=stop_event)


if __name__ == "__main__":
    raise SystemExit(main())
