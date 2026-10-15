import logging

import pytest
from app.core.config import PLACEHOLDER_JWT_SECRET, Settings
from app.core.logging import JsonFormatter, redact
from app.db.session import database_target
from pydantic import ValidationError


def test_redact_hides_url_password_bearer_and_assignments() -> None:
    raw = " ".join(
        [
            "postgresql://finscope:super-secret@db:5432/finscope",
            "Authorization: Bearer eyJsuper-secret",
            "token=abc.def",
            'password="plain-secret"',
        ]
    )
    cleaned = redact(raw)
    assert "super-secret" not in cleaned
    assert "abc.def" not in cleaned
    assert "plain-secret" not in cleaned
    assert "eyJsuper-secret" not in cleaned
    assert "***" in cleaned


def test_redact_keeps_ordinary_text() -> None:
    text = "request_id=req-12345678 status=ready"
    assert redact(text) == text


def test_json_formatter_redacts_message() -> None:
    record = logging.LogRecord(
        name="finscope",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="password=super-secret",
        args=(),
        exc_info=None,
    )
    rendered = JsonFormatter().format(record)
    assert "super-secret" not in rendered
    assert "password=***" in rendered


def test_database_target_omits_credentials() -> None:
    target = database_target("postgresql+psycopg://finscope:super-secret@db:5432/finscope")
    assert target == "db/finscope"
    assert "super-secret" not in target


def test_database_target_unparsed() -> None:
    assert database_target("not a url") == "unparsed-database"


def test_production_rejects_placeholder_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret=PLACEHOLDER_JWT_SECRET,
            database_url="postgresql+psycopg://finscope:real-password@db:5432/finscope",
        )


def test_production_rejects_example_database_password() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            jwt_secret="x" * 32,
            database_url="postgresql+psycopg://finscope:change-me@db:5432/finscope",
        )


def test_development_allows_example_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_env="development",
        jwt_secret=PLACEHOLDER_JWT_SECRET,
        database_url="postgresql+psycopg://finscope:change-me@db:5432/finscope",
    )
    assert settings.data_mode == "live"


def test_rejects_unofficial_data_mode() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_mode="mock")


def test_cors_origins_are_split_and_trimmed() -> None:
    settings = Settings(
        _env_file=None,
        frontend_origin="http://localhost:5173, http://localhost:8080",
    )
    assert settings.cors_origins == ["http://localhost:5173", "http://localhost:8080"]


def test_heartbeat_ttl_must_exceed_interval() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            worker_heartbeat_interval_seconds=30,
            worker_heartbeat_ttl_seconds=30,
        )
