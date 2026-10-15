import logging
import time
from datetime import datetime

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.services.heartbeat_rules import ProbeResult, evaluate_heartbeat

logger = logging.getLogger(__name__)


class DatabaseProbe:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def check(self) -> ProbeResult:
        started = time.perf_counter()
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.warning("database readiness check failed error_type=SQLAlchemyError")
            return ProbeResult(
                status="down",
                reason="database_unavailable",
                latency_ms=_latency_ms(started),
            )
        return ProbeResult(status="up", latency_ms=_latency_ms(started))


class WorkerProbe:
    def __init__(self, repository: WorkerHeartbeatRepository) -> None:
        self.repository = repository

    def check(self, *, now: datetime, ttl_seconds: int) -> ProbeResult:
        try:
            snapshot = self.repository.fetch()
        except SQLAlchemyError:
            logger.warning("worker heartbeat read failed error_type=SQLAlchemyError")
            return ProbeResult(status="down", reason="heartbeat_unreadable")
        return evaluate_heartbeat(snapshot, now=now, ttl_seconds=ttl_seconds)


def _latency_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
