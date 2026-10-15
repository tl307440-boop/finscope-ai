"""确认数据库可连接，并在提供环境变量时创建管理员。不写入行情。"""

import os
from typing import Protocol

from sqlalchemy import Engine

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.repositories.accounts import AccountRepository
from app.services.heartbeat_rules import ProbeResult
from app.services.probes import DatabaseProbe


class ReadinessProbe(Protocol):
    def check(self) -> ProbeResult: ...


class UserWriter(Protocol):
    def get_by_username(self, username: str) -> object | None: ...

    def create_user(
        self,
        *,
        username: str,
        email: str,
        password_hash: str,
        role: str,
    ) -> object: ...


def ensure_admin(
    users: UserWriter,
    passwords: PasswordService,
    *,
    username: str,
    email: str,
    password: str,
) -> str:
    if not username or not email or not password:
        return "skipped"
    if len(password) < 8:
        return "weak"
    if users.get_by_username(username) is not None:
        return "exists"
    users.create_user(
        username=username,
        email=email,
        password_hash=passwords.hash(password),
        role="admin",
    )
    return "created"


def run(probe: ReadinessProbe, admin_setup: str | None = None) -> int:
    result = probe.check()
    if result.status != "up":
        print("数据库不可用，未写入任何数据。")
        return 1
    if admin_setup == "created":
        print("已创建管理员账号，不写入行情。")
    elif admin_setup == "exists":
        print("管理员账号已存在，未修改密码，不写入行情。")
    elif admin_setup == "weak":
        print("管理员密码至少 8 位，未创建账号，不写入行情。")
    else:
        print("数据库连接正常，不写入行情或用户。")
    return 0


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    try:
        outcome = _admin_from_env(engine, PasswordService(settings.app_env))
        return run(DatabaseProbe(engine), outcome)
    finally:
        engine.dispose()


def _admin_from_env(engine: Engine, passwords: PasswordService) -> str:
    username = os.environ.get("ADMIN_USERNAME", "").strip()
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username and not email and not password:
        return "skipped"
    return ensure_admin(
        AccountRepository(engine),
        passwords,
        username=username,
        email=email,
        password=password,
    )


if __name__ == "__main__":
    raise SystemExit(main())
