import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)([^@/\s]+)@")
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|api_key|authorization|jwt)(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)\S+")


def redact(value: str) -> str:
    """去掉日志中的口令、令牌和连接串密码。"""
    redacted = _URL_PASSWORD.sub(r"\1***@", value)
    redacted = _BEARER.sub(r"\1***", redacted)
    return _ASSIGNMENT.sub(r"\1\2***", redacted)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
