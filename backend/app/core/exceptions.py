import logging
import traceback
from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import redact

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def request_id_from(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    return "missing-request-id"


def public_validation_errors(errors: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验错误只保留位置和类型，不回显用户输入。"""
    public: list[dict[str, Any]] = []
    for error in errors:
        public.append(
            {
                "loc": list(error.get("loc", [])),
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return public


def error_body(
    *,
    request_id: str,
    code: str,
    message: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details},
        "request_id": request_id,
    }


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            request_id=request_id_from(request),
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ),
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(
            request_id=request_id_from(request),
            code="VALIDATION_ERROR",
            message="请求参数不正确",
            details={"errors": public_validation_errors(list(exc.errors()))},
        ),
    )


_HTTP_CODES = {
    404: ("NOT_FOUND", "资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不被允许"),
}


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_CODES.get(exc.status_code, ("HTTP_ERROR", "请求无法完成"))
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(
            request_id=request_id_from(request),
            code=code,
            message=message,
            details={},
        ),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "unhandled error_type=%s detail=%s",
        type(exc).__name__,
        redact(traceback.format_exception_only(type(exc), exc)[-1].strip()),
    )
    return JSONResponse(
        status_code=500,
        content=error_body(
            request_id=request_id_from(request),
            code="INTERNAL_ERROR",
            message="服务器内部错误",
            details={},
        ),
    )
