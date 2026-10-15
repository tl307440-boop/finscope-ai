from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from app.core.exceptions import AppError
from app.schemas.common import ComponentCheck, LiveData, ReadyData
from app.services.heartbeat_rules import ProbeResult
from app.utils.time import utc_now


class DatabaseChecker(Protocol):
    def check(self) -> ProbeResult: ...


class WorkerChecker(Protocol):
    def check(self, *, now: datetime, ttl_seconds: int) -> ProbeResult: ...


class HealthService:
    def __init__(
        self,
        *,
        database: DatabaseChecker,
        worker: WorkerChecker,
        ttl_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.worker = worker
        self.ttl_seconds = ttl_seconds
        self.clock = clock or utc_now

    def live(self) -> LiveData:
        return LiveData(status="alive", service="api")

    def ready(self) -> ReadyData:
        now = self.clock()
        database = self.database.check()
        worker = self.worker.check(now=now, ttl_seconds=self.ttl_seconds)
        checks = {"database": _component(database), "worker": _component(worker)}
        if database.status != "up" or worker.status != "up":
            raise AppError(
                code="SERVICE_UNAVAILABLE",
                message="服务尚未就绪",
                status_code=503,
                details={
                    "checks": {
                        name: item.model_dump(mode="json", exclude_none=True)
                        for name, item in checks.items()
                    }
                },
            )
        return ReadyData(status="ready", checks=checks)


def _component(result: ProbeResult) -> ComponentCheck:
    status: Literal["up", "down"] = "up" if result.status == "up" else "down"
    return ComponentCheck(
        status=status,
        reason=result.reason,
        latency_ms=result.latency_ms,
        last_seen_at=result.last_seen_at,
    )
