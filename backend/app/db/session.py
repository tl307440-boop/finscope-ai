from sqlalchemy import Engine, create_engine, make_url


def create_db_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 3},
    )


def database_target(database_url: str) -> str:
    """日志可用的目标描述，不含用户名和口令。"""
    try:
        url = make_url(database_url)
    except Exception:
        return "unparsed-database"
    host = url.host or "unknown-host"
    database = url.database or "unknown-db"
    return f"{host}/{database}"
