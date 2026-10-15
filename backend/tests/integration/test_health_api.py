import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from app.api.deps import get_health_service
from app.core.config import reset_settings_cache
from app.core.exceptions import AppError, unhandled_error_handler, validation_error_handler
from app.main import create_app
from app.schemas.common import ComponentCheck, LiveData, ReadyData
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


class ReadyHealth:
    def live(self) -> LiveData:
        return LiveData(status="alive", service="api")

    def ready(self) -> ReadyData:
        return ReadyData(
            status="ready",
            checks={
                "database": ComponentCheck(status="up", latency_ms=2),
                "worker": ComponentCheck(
                    status="up",
                    last_seen_at=datetime(2026, 10, 15, tzinfo=UTC),
                ),
            },
        )


class UnavailableHealth(ReadyHealth):
    def ready(self) -> ReadyData:
        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="服务尚未就绪",
            status_code=503,
            details={
                "checks": {
                    "database": {"status": "up", "latency_ms": 2},
                    "worker": {"status": "down", "reason": "heartbeat_missing"},
                }
            },
        )


@contextmanager
def client_for(service: object) -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[get_health_service] = lambda: service
    with TestClient(application) as client:
        yield client


def _request(request_id: str = "req-12345678") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/health/live",
        "raw_path": b"/api/v1/health/live",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "state": {"request_id": request_id},
    }
    return Request(scope)


def test_live_returns_request_id_and_live_mode() -> None:
    with client_for(ReadyHealth()) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": "req-12345678"})
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"status": "alive", "service": "api"}
    assert body["meta"]["request_id"] == "req-12345678"
    assert body["meta"]["source"] == "internal"
    assert body["meta"]["stale"] is False
    assert body["meta"]["data_mode"] == "live"
    assert "notice" not in body["meta"]
    assert response.headers["X-Request-ID"] == "req-12345678"
    assert "change-me" not in response.text
    assert "+00:00" in body["meta"]["as_of"] or body["meta"]["as_of"].endswith("Z")


def test_ready_success_hides_process_id() -> None:
    with client_for(ReadyHealth()) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ready"
    assert body["data"]["checks"]["database"]["status"] == "up"
    assert body["data"]["checks"]["worker"]["status"] == "up"
    assert "pid" not in response.text


def test_ready_failure_is_structured_and_keeps_request_id() -> None:
    with client_for(UnavailableHealth()) as client:
        response = client.get(
            "/api/v1/health/ready",
            headers={"X-Request-ID": "bad id\ninjected"},
        )
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert body["error"]["message"] == "服务尚未就绪"
    assert body["error"]["details"]["checks"]["worker"]["reason"] == "heartbeat_missing"
    assert "injected" not in response.headers["X-Request-ID"]
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "change-me" not in response.text


def test_missing_route_is_structured_not_found() -> None:
    with client_for(ReadyHealth()) as client:
        response = client.get("/api/v1/not-a-resource")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "资源不存在",
        "details": {},
    }


def test_openapi_and_docs_are_available() -> None:
    with client_for(ReadyHealth()) as client:
        docs = client.get("/docs")
        schema = client.get("/openapi.json")
    assert docs.status_code == 200
    assert "swagger" in docs.text.lower() or "openapi" in docs.text.lower()
    paths = schema.json()["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health/ready" in paths


def test_cors_allows_configured_origin_only() -> None:
    with client_for(ReadyHealth()) as client:
        allowed = client.get(
            "/api/v1/health/live",
            headers={"Origin": "http://localhost:5173"},
        )
        rejected = client.get(
            "/api/v1/health/live",
            headers={"Origin": "http://evil.example"},
        )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert rejected.headers.get("access-control-allow-origin") != "http://evil.example"


def test_demo_mode_is_labeled() -> None:
    from app.core.config import get_settings

    original = get_settings().data_mode
    try:
        import os

        os.environ["DATA_MODE"] = "demo"
        reset_settings_cache()
        with client_for(ReadyHealth()) as client:
            body = client.get("/api/v1/health/live").json()
        assert body["meta"]["data_mode"] == "demo"
        assert "演示数据" in body["meta"]["notice"]
        assert "非实时" in body["meta"]["notice"]
    finally:
        import os

        os.environ["DATA_MODE"] = original
        reset_settings_cache()


def test_validation_error_does_not_echo_input() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "string_type",
                "loc": ("body", "password"),
                "msg": "Input should be a string",
                "input": "super-secret",
            }
        ]
    )
    response = asyncio.run(validation_error_handler(_request(), exc))
    rendered = bytes(response.body).decode()
    assert response.status_code == 422
    assert "super-secret" not in rendered
    assert "VALIDATION_ERROR" in rendered
    assert "req-12345678" in rendered


def test_internal_error_hides_exception_text() -> None:
    application = create_app()

    @application.get("/api/v1/_boom")
    def _boom() -> dict[str, str]:
        raise RuntimeError("password=super-secret")

    messages: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("app.core.exceptions")
    handler = ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.ERROR)
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/_boom")
    finally:
        logger.removeHandler(handler)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "super-secret" not in response.text
    assert messages
    assert "super-secret" not in " ".join(messages)


def test_unhandled_handler_redacts_log_line() -> None:
    messages: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("app.core.exceptions")
    handler = ListHandler()
    logger.addHandler(handler)
    try:
        asyncio.run(unhandled_error_handler(_request(), RuntimeError("token=super-secret")))
    finally:
        logger.removeHandler(handler)
    assert "super-secret" not in " ".join(messages)
