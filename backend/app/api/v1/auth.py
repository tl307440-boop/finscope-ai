from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine

from app.api.deps import (
    device_label_from,
    get_account_service,
    get_auth_service,
    get_current_user,
)
from app.api.responses import success_response
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import REFRESH_COOKIE, refresh_cookie_secure
from app.schemas.auth import (
    AuthOptions,
    ChangePasswordRequest,
    LoginData,
    LoginRequest,
    MeData,
    PreferencePublic,
    PreferenceUpdate,
    RegisterRequest,
    UserPublic,
)
from app.services.accounts import AccountService
from app.services.auth import AuthService, LoginResult
from app.services.auth_types import PreferenceRecord, UserRecord
from app.services.runtime_settings import effective_settings

router = APIRouter(prefix="/auth", tags=["认证"])
me_router = APIRouter(tags=["认证"])


def _public_user(user: UserRecord) -> UserPublic:
    if user.role not in {"researcher", "admin"}:
        raise AppError(code="INTERNAL_ERROR", message="服务器内部错误", status_code=500)
    role = cast(Literal["researcher", "admin"], user.role)
    return UserPublic(id=user.id, username=user.username, email=user.email, role=role)


def _public_preferences(preferences: PreferenceRecord) -> PreferencePublic:
    return PreferencePublic(
        theme=cast(Literal["dark", "light"], preferences.theme),
        rise_color_scheme=cast(Literal["cn", "us"], preferences.rise_color_scheme),
        timezone=preferences.timezone,
        default_market=cast(Literal["CN", "US"], preferences.default_market),
        default_lookback=preferences.default_lookback,
    )


def _login_body(request: Request, result: LoginResult, status_code: int = 200) -> JSONResponse:
    payload = LoginData(access_token=result.access_token, user=_public_user(result.user))
    response = JSONResponse(status_code=status_code, content=success_response(request, payload))
    _attach_refresh_cookie(response, result.refresh_token, get_settings())
    return response


def _attach_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=refresh_cookie_secure(settings.app_env),
        path="/api/v1/auth",
        max_age=settings.refresh_token_days * 24 * 60 * 60,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path="/api/v1/auth",
        httponly=True,
        samesite="lax",
        secure=refresh_cookie_secure(settings.app_env),
    )


@router.post("/login", summary="登录")
def login(
    body: LoginRequest,
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    result = service.login(
        username=body.username,
        password=body.password,
        device_label=device_label_from(request),
    )
    return _login_body(request, result)


def _registration_open(request: Request) -> bool:
    return effective_settings(cast(Engine, request.app.state.engine)).allow_registration


@router.get("/options", summary="登录页需要的公开选项（是否开放注册）")
def auth_options(request: Request) -> JSONResponse:
    payload = AuthOptions(registration_open=_registration_open(request))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.post("/register", summary="注册研究员账号并自动登录")
def register(
    body: RegisterRequest,
    request: Request,
    service: Annotated[AccountService, Depends(get_account_service)],
) -> JSONResponse:
    if not _registration_open(request):
        raise AppError(
            code="REGISTRATION_CLOSED",
            message="当前不开放注册，请联系管理员创建账号",
            status_code=403,
        )
    client = request.client.host if request.client else "unknown"
    result = service.register(
        username=body.username,
        email=body.email,
        password=body.password,
        client_key=f"reg:{client}",
        device_label=device_label_from(request),
    )
    return _login_body(request, result, status_code=201)


@router.post("/refresh", summary="刷新访问令牌")
def refresh(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    result = service.refresh(request.cookies.get(REFRESH_COOKIE), device_label_from(request))
    return _login_body(request, result)


@router.post("/logout", summary="退出")
def logout(
    request: Request,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> Response:
    service.logout(request.cookies.get(REFRESH_COOKIE))
    response = Response(status_code=204)
    _clear_refresh_cookie(response, get_settings())
    return response


@me_router.get("/me", summary="当前用户")
def read_me(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    preferences = service.preferences_for(user.id)
    payload = MeData(user=_public_user(user), preferences=_public_preferences(preferences))
    return JSONResponse(status_code=200, content=success_response(request, payload))


@me_router.post("/me/password", summary="修改密码（其它设备的登录会失效）")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[AccountService, Depends(get_account_service)],
) -> JSONResponse:
    result = service.change_password(
        user=user,
        current_password=body.current_password,
        new_password=body.new_password,
        device_label=device_label_from(request),
    )
    return _login_body(request, result)


@me_router.patch("/me/preferences", summary="更新个人偏好")
def update_preferences(
    body: PreferenceUpdate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> JSONResponse:
    updated = service.update_preferences(user.id, body.changes())
    payload = MeData(user=_public_user(user), preferences=_public_preferences(updated))
    return JSONResponse(status_code=200, content=success_response(request, payload))
