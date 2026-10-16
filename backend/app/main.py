import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.v1.router import router as api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AppError,
    app_error_handler,
    http_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.repositories.accounts import AccountRepository
from app.services.accounts import bootstrap_admin
from app.services.login_limiter import LoginRateLimiter

ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]


def _bootstrap_admin(engine: Engine, settings: Settings) -> None:
    """环境变量里给了 ADMIN_* 就在启动时创建首个管理员；数据库没就绪时只告警，不阻止启动。"""

    try:
        bootstrap_admin(
            AccountRepository(engine),
            PasswordService(settings.app_env),
            username=settings.admin_username,
            email=settings.admin_email,
            password=settings.admin_password,
        )
    except Exception:
        logging.getLogger(__name__).warning("admin bootstrap skipped: database not ready")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings.database_url)
        app.state.engine = engine
        app.state.login_limiter = LoginRateLimiter(
            max_failures=settings.login_max_failures,
            window_seconds=settings.login_failure_window_seconds,
        )
        app.state.register_limiter = LoginRateLimiter(
            max_failures=settings.register_max_per_hour,
            window_seconds=3600,
        )
        app.state.password_limiter = LoginRateLimiter(
            max_failures=settings.login_max_failures,
            window_seconds=settings.login_failure_window_seconds,
        )
        _bootstrap_admin(engine, settings)
        yield
        engine.dispose()

    app = FastAPI(
        title="FinScope AI",
        version="0.1.0",
        description="金融投研辅助系统 API。不提供交易执行。",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(AppError, cast(ExceptionHandler, app_error_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_error_handler),
    )
    app.add_exception_handler(
        StarletteHTTPException,
        cast(ExceptionHandler, http_error_handler),
    )
    app.add_exception_handler(Exception, cast(ExceptionHandler, unhandled_error_handler))
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
