"""注册、改密码与首个管理员：登录之外的账号流程。"""

import logging
import re
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppError
from app.core.security import PasswordService
from app.repositories.accounts import AccountRepository, RefreshSessionRepository
from app.services.auth import AuthService, LoginResult
from app.services.auth_types import UserRecord
from app.services.login_limiter import LoginRateLimiter
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
COMMON_PASSWORDS = frozenset(
    {
        "12345678",
        "123456789",
        "1234567890",
        "password",
        "password1",
        "qwerty123",
        "11111111",
        "00000000",
        "abc12345",
        "admin123",
        "iloveyou1",
    }
)


def _invalid(message: str) -> AppError:
    return AppError(code="VALIDATION_ERROR", message=message, status_code=422)


def password_problem(username: str, password: str) -> str | None:
    """返回不满足的原因；满足策略返回 None。"""

    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密码至少 {MIN_PASSWORD_LENGTH} 位"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"密码最多 {MAX_PASSWORD_LENGTH} 位"
    if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        return "密码需要同时包含字母和数字"
    if password.lower() == username.lower() or password.lower() in COMMON_PASSWORDS:
        return "密码太容易被猜到，请换一个"
    return None


def clean_identity(username: str, email: str) -> tuple[str, str]:
    name = username.strip().lower()
    mail = email.strip().lower()
    if not USERNAME_PATTERN.fullmatch(name):
        raise _invalid("账号只能用 3-30 位字母、数字、下划线、点或短横线")
    if len(mail) > 254 or not EMAIL_PATTERN.fullmatch(mail):
        raise _invalid("邮箱格式不正确")
    return name, mail


class AccountService:
    def __init__(
        self,
        *,
        users: AccountRepository,
        sessions: RefreshSessionRepository,
        passwords: PasswordService,
        auth: AuthService,
        register_limiter: LoginRateLimiter,
        password_limiter: LoginRateLimiter,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.passwords = passwords
        self.auth = auth
        self.register_limiter = register_limiter
        self.password_limiter = password_limiter
        self.clock = clock or utc_now

    def register(
        self,
        *,
        username: str,
        email: str,
        password: str,
        client_key: str,
        device_label: str | None,
    ) -> LoginResult:
        now = self.clock()
        # 每次尝试都计数（不论成败），防止脚本批量注册。
        self.register_limiter.check(client_key, now)
        self.register_limiter.record_failure(client_key, now)
        name, mail = clean_identity(username, email)
        problem = password_problem(name, password)
        if problem is not None:
            raise _invalid(problem)
        conflict = AppError(
            code="ACCOUNT_EXISTS", message="这个账号或邮箱已被使用", status_code=409
        )
        if self.users.get_by_username(name) is not None:
            raise conflict
        try:
            # 注册出来的永远是研究员；管理员只能由部署方用环境变量创建或在库里提升。
            self.users.create_user(
                username=name,
                email=mail,
                password_hash=self.passwords.hash(password),
                role="researcher",
                now=now,
            )
        except IntegrityError as exc:
            raise conflict from exc
        logger.info("account registered")
        return self.auth.login(username=name, password=password, device_label=device_label)

    def change_password(
        self,
        *,
        user: UserRecord,
        current_password: str,
        new_password: str,
        device_label: str | None,
    ) -> LoginResult:
        now = self.clock()
        key = f"pw:{user.id}"
        self.password_limiter.check(key, now)
        if not self.passwords.verify(user.password_hash, current_password):
            self.password_limiter.record_failure(key, now)
            raise AppError(code="ACCOUNT_INVALID", message="当前密码不正确", status_code=400)
        if new_password == current_password:
            raise _invalid("新密码不能和当前密码相同")
        problem = password_problem(user.username, new_password)
        if problem is not None:
            raise _invalid(problem)
        self.password_limiter.clear(key)
        self.users.set_password(user.id, self.passwords.hash(new_password), now)
        # 所有设备上旧的登录凭证一律作废，再给当前设备签发新的。
        self.sessions.revoke_all(user.id, now)
        logger.info("password changed")
        return self.auth.login(
            username=user.username, password=new_password, device_label=device_label
        )


def bootstrap_admin(
    users: AccountRepository,
    passwords: PasswordService,
    *,
    username: str,
    email: str,
    password: str,
) -> str:
    """按环境变量创建首个管理员。已存在同名账号时不改密码；返回结果代号，不记录密码。"""

    if not username.strip() or not email.strip() or not password:
        return "skipped"
    try:
        name, mail = clean_identity(username, email)
    except AppError:
        logger.warning("admin bootstrap skipped: ADMIN_USERNAME / ADMIN_EMAIL invalid")
        return "invalid"
    problem = password_problem(name, password)
    if problem is not None:
        logger.warning("admin bootstrap skipped: ADMIN_PASSWORD rejected (%s)", problem)
        return "weak"
    if users.get_by_username(name) is not None:
        return "exists"
    try:
        users.create_user(
            username=name,
            email=mail,
            password_hash=passwords.hash(password),
            role="admin",
        )
    except IntegrityError:
        return "exists"
    logger.info("admin account created from environment username=%s", name)
    return "created"
