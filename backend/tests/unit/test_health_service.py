import json
from datetime import UTC, datetime

import pytest
from app.core.exceptions import AppError
from app.services.health import HealthService
from app.services.heartbeat_rules import ProbeResult


class ExplodingProbe:
    def check(self, *_args: object, **_kwargs: object) -> ProbeResult:
        raise AssertionError("存活检查不应访问数据库或 Worker")


class StaticDatabase:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result

    def check(self) -> ProbeResult:
        return self.result


class StaticWorker:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result

    def check(self, *, now: datetime, ttl_seconds: int) -> ProbeResult:
        assert ttl_seconds == 30
        assert now == datetime(2026, 10, 15, tzinfo=UTC)
        return self.result


def test_live_does_not_touch_dependencies() -> None:
    service = HealthService(database=ExplodingProbe(), worker=ExplodingProbe(), ttl_seconds=30)
    assert service.live().status == "alive"
    assert service.live().service == "api"


def test_ready_succeeds_only_when_both_dependencies_are_up() -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    service = HealthService(
        database=StaticDatabase(ProbeResult(status="up", latency_ms=4)),
        worker=StaticWorker(ProbeResult(status="up", last_seen_at=now)),
        ttl_seconds=30,
        clock=lambda: now,
    )
    ready = service.ready()
    assert ready.status == "ready"
    assert ready.checks["database"].latency_ms == 4
    assert ready.checks["worker"].status == "up"


def test_ready_reports_both_checks_without_pid_or_secrets() -> None:
    now = datetime(2026, 10, 15, tzinfo=UTC)
    service = HealthService(
        database=StaticDatabase(ProbeResult(status="down", reason="database_unavailable")),
        worker=StaticWorker(ProbeResult(status="down", reason="heartbeat_missing")),
        ttl_seconds=30,
        clock=lambda: now,
    )
    with pytest.raises(AppError) as caught:
        service.ready()
    error = caught.value
    assert error.status_code == 503
    assert error.code == "SERVICE_UNAVAILABLE"
    payload = json.dumps(error.details)
    assert "heartbeat_missing" in payload
    assert "database_unavailable" in payload
    assert "pid" not in payload
    assert "change-me" not in payload
    assert "super-secret" not in payload
