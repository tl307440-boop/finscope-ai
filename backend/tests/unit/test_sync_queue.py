from app.providers.errors import ProviderError
from app.services.sync_queue import dedupe_key_for, public_failure, should_requeue


def test_same_window_has_one_dedupe_key() -> None:
    scope = {
        "display_symbol": "600519.SH",
        "adjustment": "none",
        "observation_start": "1999-01-04",
        "observation_end": "1999-01-05",
    }
    assert dedupe_key_for("akshare_daily", scope) == (
        "akshare:600519.SH:none:1999-01-04:1999-01-05"
    )


def test_timeout_retries_until_the_attempt_limit() -> None:
    assert should_requeue(attempts=1, max_attempts=3, error_code="UPSTREAM_TIMEOUT") is True
    assert should_requeue(attempts=3, max_attempts=3, error_code="UPSTREAM_TIMEOUT") is False


def test_missing_key_is_not_retried() -> None:
    assert should_requeue(attempts=1, max_attempts=3, error_code="DATA_UNAVAILABLE") is False


def test_failure_text_drops_connection_secrets() -> None:
    code, message = public_failure(RuntimeError("postgresql://finscope:super-secret@db/finscope"))
    assert code == "TASK_FAILED"
    assert message == "任务执行失败"
    assert "super-secret" not in message
    provider = public_failure(
        ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
    )
    assert provider == ("UPSTREAM_TIMEOUT", "上游超时")
