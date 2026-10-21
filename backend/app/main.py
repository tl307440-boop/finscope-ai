import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
from app.services.assistant.limits import AssistantLimiter
from app.services.assistant.loop_bridge import bind_main_loop
from app.services.assistant.orchestrator import TurnRunner
from app.services.assistant.tool_cache import ToolCache
from app.services.assistant.tools import default_registry
from app.services.assistant.turns import AssistantConfig
from app.services.cockpit import keep_dashboard_warm
from app.services.corpus_vectors import QueryEmbedder
from app.services.eastmoney_binding import build_service as build_eastmoney_service
from app.services.login_limiter import LoginRateLimiter
from app.services.market_cache import close_gateways

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
        app.state.passwords = PasswordService(settings.app_env)
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
        # 助手：回合在后台线程池里跑，限流状态放进程内（每日额度查库）。
        # 工具是同步的，行情服务是异步的：登记主事件循环，让工具把协程放回这里执行。
        bind_main_loop(asyncio.get_running_loop())
        assistant_runner = TurnRunner(
            workers=settings.assistant_workers,
            heartbeat_seconds=settings.assistant_heartbeat_seconds,
        )
        app.state.assistant_runner = assistant_runner
        app.state.assistant_limiter = AssistantLimiter(
            per_minute=settings.assistant_per_minute,
            workers=settings.assistant_workers,
        )
        app.state.assistant_config = AssistantConfig(
            daily_limit=settings.assistant_daily_limit,
            stale_seconds=settings.assistant_stale_seconds,
            max_steps=settings.assistant_max_steps,
            turn_seconds=settings.assistant_turn_seconds,
            fast_path=settings.assistant_fast_path,
        )
        # 数据工具共用一份结果缓存（工具 + 参数 + 时点，5 分钟）。只缓存公开行情与宏观数据，
        # 自选、语料检索和计算不进缓存，所以不会把一个用户的数据给到另一个用户。
        app.state.assistant_registry = default_registry(
            ToolCache(),
            corpus_min_rank=settings.corpus_min_rank if settings.corpus_enabled else None,
            corpus_min_similarity=settings.corpus_min_similarity,
            corpus_embed_query=QueryEmbedder(),
        )
        _bootstrap_admin(engine, settings)
        # 总览的上游（FRED / 东财 / 外盘 / 指数 / 板块 / 公告）冷启动要几十次跨境请求：
        # 后台预热并定期续命，用户打开总览时直接命中缓存。
        warmer = (
            asyncio.create_task(keep_dashboard_warm(engine)) if settings.app_env != "test" else None
        )
        # 已绑定东方财富的用户：后台定时把东财自选同步进来。
        eastmoney = build_eastmoney_service(engine, settings)
        app.state.eastmoney_service = eastmoney
        syncer = (
            asyncio.create_task(eastmoney.run_forever(settings.eastmoney_sync_interval_seconds))
            if settings.app_env != "test" and settings.eastmoney_sync_enabled
            else None
        )
        yield
        for task in (warmer, syncer):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await close_gateways()
        assistant_runner.shutdown()
        bind_main_loop(None)
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
    # 最外层压缩：总览、宏观这类 JSON 动辄上百 KB，不压缩时慢网络下传输就是首屏的大头。
    # Starlette 默认不压缩 text/event-stream，AI 助手的流式响应不受影响。
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
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
