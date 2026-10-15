import logging
import re
import time
import uuid
from collections.abc import MutableMapping

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import redact

_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
logger = logging.getLogger("finscope.access")


def normalize_request_id(raw: str | None) -> str:
    if raw and _REQUEST_ID.fullmatch(raw):
        return raw
    return str(uuid.uuid4())


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, b"x-request-id")
        request_id = normalize_request_id(incoming)
        state = scope.setdefault("state", {})
        if isinstance(state, MutableMapping):
            state["request_id"] = request_id

        started = time.perf_counter()
        status_code = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            path = redact(scope.get("path", ""))
            query = scope.get("query_string", b"")
            if query:
                path = f"{path}?{redact(query.decode('latin-1', errors='ignore'))}"
            logger.info(
                "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
                scope.get("method", ""),
                path,
                status_code,
                duration_ms,
                request_id,
            )


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            decoded = value.decode("latin-1")
            return decoded if isinstance(decoded, str) else None
    return None
