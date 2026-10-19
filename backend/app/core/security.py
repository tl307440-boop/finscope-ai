import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from jwt.exceptions import InvalidTokenError

from app.core.exceptions import AppError

REFRESH_COOKIE = "finscope_refresh"
_ACCESS_TYPE = "access"


class PasswordService:
    def __init__(self, app_env: str) -> None:
        if app_env == "test":
            self.hasher = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        else:
            # 库默认是 64MiB、并行 4。注册一次会新建好几个 PasswordService，每个初始化都要先算一遍
            # 假哈希，小内存机器上会把进程打满，浏览器表现为注册请求被掐断。
            # 这里用 OWASP 的交互式参数：19MiB、2 次迭代、单线程。已有密码的校验仍按哈希串里记下的参数走。
            self.hasher = PasswordHasher(time_cost=2, memory_cost=19_456, parallelism=1)
        self._dummy_hash: str | None = None

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def _dummy(self) -> str:
        if self._dummy_hash is None:
            self._dummy_hash = self.hasher.hash("not-a-real-password")
        return self._dummy_hash

    def verify(self, password_hash: str | None, password: str) -> bool:
        candidate = password_hash or self._dummy()
        try:
            matched = bool(self.hasher.verify(candidate, password))
        except VerifyMismatchError:
            matched = False
        except VerificationError:
            matched = False
        if password_hash is None:
            return False
        return matched


class TokenService:
    def __init__(self, secret: str, access_token_minutes: int) -> None:
        self.secret = secret
        self.access_token_minutes = access_token_minutes

    def issue_access(self, *, user_id: UUID, role: str, now: datetime) -> str:
        expires = now.astimezone(UTC) + timedelta(minutes=self.access_token_minutes)
        payload = {
            "sub": str(user_id),
            "role": role,
            "typ": _ACCESS_TYPE,
            "exp": int(expires.timestamp()),
        }
        encoded = jwt.encode(payload, self.secret, algorithm="HS256")
        if isinstance(encoded, bytes):
            return encoded.decode("ascii")
        return encoded

    def read_access_subject(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                options={"require": ["exp", "sub"]},
            )
        except InvalidTokenError as exc:
            raise _unauthenticated() from exc
        if payload.get("typ") != _ACCESS_TYPE:
            raise _unauthenticated()
        try:
            return UUID(str(payload["sub"]))
        except ValueError as exc:
            raise _unauthenticated() from exc


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_cookie_secure(app_env: str, override: bool | None = None) -> bool:
    """刷新 cookie 是否带 Secure。

    默认只有 production 才开；``override`` 来自 ``COOKIE_SECURE``，用于「生产环境但暂时只有 HTTP」
    （例如还没有域名与证书、直接用公网 IP 访问）。纯 HTTP 下浏览器会丢弃 Secure cookie，
    此时必须显式设为 false；上了 HTTPS 之后应删掉该覆盖。
    """

    if override is not None:
        return override
    return app_env == "production"


def _unauthenticated() -> AppError:
    return AppError(
        code="UNAUTHENTICATED",
        message="未登录或登录已失效",
        status_code=401,
    )
