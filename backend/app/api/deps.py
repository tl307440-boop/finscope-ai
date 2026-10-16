from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy import Engine

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import PasswordService, TokenService
from app.repositories.accounts import AccountRepository, RefreshSessionRepository
from app.repositories.worker_heartbeat import WorkerHeartbeatRepository
from app.services.accounts import AccountService
from app.services.auth import AuthService
from app.services.auth_types import UserRecord
from app.services.health import HealthService
from app.services.login_limiter import LoginRateLimiter
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


def get_auth_service(request: Request) -> AuthService:
    settings = get_settings()
    engine = cast(Engine, request.app.state.engine)
    limiter = cast(LoginRateLimiter, request.app.state.login_limiter)
    return AuthService(
        users=AccountRepository(engine),
        sessions=RefreshSessionRepository(engine),
        passwords=PasswordService(settings.app_env),
        tokens=TokenService(settings.jwt_secret, settings.access_token_minutes),
        limiter=limiter,
        refresh_token_days=settings.refresh_token_days,
    )


def get_account_service(request: Request) -> AccountService:
    settings = get_settings()
    engine = cast(Engine, request.app.state.engine)
    passwords = PasswordService(settings.app_env)
    sessions = RefreshSessionRepository(engine)
    return AccountService(
        users=AccountRepository(engine),
        sessions=sessions,
        passwords=passwords,
        auth=get_auth_service(request),
        register_limiter=cast(LoginRateLimiter, request.app.state.register_limiter),
        password_limiter=cast(LoginRateLimiter, request.app.state.password_limiter),
    )


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def device_label_from(request: Request) -> str | None:
    raw = request.headers.get("x-device-label")
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned or any(char in cleaned for char in "\r\n"):
        return None
    return cleaned[:80]


def get_current_user(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserRecord:
    token = bearer_token(request)
    if token is None:
        raise AppError(code="UNAUTHENTICATED", message="未登录或登录已失效", status_code=401)
    return service.user_from_access_token(token)


def require_admin(user: Annotated[UserRecord, Depends(get_current_user)]) -> UserRecord:
    if user.role != "admin":
        raise AppError(code="FORBIDDEN", message="需要管理员权限", status_code=403)
    return user
