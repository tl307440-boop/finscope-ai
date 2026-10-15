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
            self.hasher = PasswordHasher()
        self.dummy_hash = self.hasher.hash("not-a-real-password")

    def hash(self, password: str) -> str:
        return self.hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        candidate = password_hash or self.dummy_hash
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


def refresh_cookie_secure(app_env: str) -> bool:
    return app_env == "production"


def _unauthenticated() -> AppError:
    return AppError(
        code="UNAUTHENTICATED",
        message="未登录或登录已失效",
        status_code=401,
    )
