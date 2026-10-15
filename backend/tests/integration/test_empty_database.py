import os
from pathlib import Path

import pytest
from app.core.config import get_settings, reset_settings_cache
from app.db.session import create_db_engine
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]
EMPTY_DATABASE = "finscope_empty_t02"
EXPECTED_TABLES = {
    "users",
    "refresh_sessions",
    "user_preferences",
    "worker_heartbeats",
    "data_providers",
    "sync_jobs",
    "sync_job_events",
    "instruments",
    "trading_calendars",
    "market_bars",
    "macro_series",
    "macro_observations",
}


@pytest.mark.integration
def test_empty_database_upgrades_in_one_command() -> None:
    base_url = get_settings().database_url
    probe = create_db_engine(base_url)
    try:
        with probe.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        probe.dispose()
        pytest.skip("PostgreSQL 不可用")
    probe.dispose()

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    empty_url = make_url(base_url).set(database=EMPTY_DATABASE)
    rendered_url = empty_url.render_as_string(hide_password=False)
    previous = os.environ.get("DATABASE_URL")
    try:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{EMPTY_DATABASE}" WITH (FORCE)'))
            connection.execute(text(f'CREATE DATABASE "{EMPTY_DATABASE}"'))
        os.environ["DATABASE_URL"] = rendered_url
        reset_settings_cache()
        from alembic import command
        from alembic.config import Config

        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        migrated = create_db_engine(rendered_url)
        try:
            with migrated.connect() as connection:
                names = set(
                    connection.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    ).scalars()
                )
                close_type = connection.execute(
                    text(
                        """
                        SELECT numeric_precision, numeric_scale
                        FROM information_schema.columns
                        WHERE table_name = 'market_bars' AND column_name = 'close'
                        """
                    )
                ).one()
        finally:
            migrated.dispose()
        assert names >= EXPECTED_TABLES
        assert close_type.numeric_precision == 20
        assert close_type.numeric_scale == 6
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        reset_settings_cache()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{EMPTY_DATABASE}" WITH (FORCE)'))
        admin.dispose()
