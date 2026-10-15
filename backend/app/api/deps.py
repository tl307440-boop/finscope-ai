from typing import cast

from fastapi import Request
from sqlalchemy import Engine

from app.core.config import get_settings
from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.services.health import HealthService
from app.services.probes import DatabaseProbe, WorkerProbe


def get_health_service(request: Request) -> HealthService:
    settings = get_settings()
    engine = cast(Engine, request.app.state.engine)
    repository = WorkerHeartbeatRepository(engine, settings.worker_service_name)
    return HealthService(
        database=DatabaseProbe(engine),
        worker=WorkerProbe(repository),
        ttl_seconds=settings.worker_heartbeat_ttl_seconds,
    )
