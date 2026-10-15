"""T00 只确认数据库可连接，不写入行情、财报或用户。"""

from typing import Protocol

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine
from app.services.heartbeat_rules import ProbeResult
from app.services.probes import DatabaseProbe


class ReadinessProbe(Protocol):
    def check(self) -> ProbeResult: ...


def run(probe: ReadinessProbe) -> int:
    result = probe.check()
    if result.status != "up":
        print("数据库不可用，未写入任何数据。")
        return 1
    print("T00 不写入演示行情或用户。数据库连接正常，业务种子数据留待后续任务。")
    return 0


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    try:
        return run(DatabaseProbe(engine))
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
