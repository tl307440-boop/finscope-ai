import pytest
from app.core.exceptions import AppError
from app.core.secret_box import SecretBox
from app.services.runtime_settings import managed_key, validate_value


def test_secret_box_round_trip_hides_plaintext() -> None:
    box = SecretBox("master-one")
    token = box.seal("tiingo-secret-1234")
    assert "tiingo-secret" not in token
    assert box.open(token) == "tiingo-secret-1234"


def test_secret_box_wrong_master_returns_none_instead_of_raising() -> None:
    token = SecretBox("master-one").seal("value")
    assert SecretBox("master-two").open(token) is None
    assert SecretBox("master-one").open("not-a-token") is None


def test_secret_box_rejects_empty_master() -> None:
    with pytest.raises(ValueError):
        SecretBox("")


def test_validate_value_rules() -> None:
    assert validate_value(managed_key("TIINGO_API_KEY"), "  abc123  ") == "abc123"
    assert validate_value(managed_key("ALLOW_REGISTRATION"), "FALSE") == "false"
    for key, value in (
        ("TIINGO_API_KEY", "   "),
        ("TIINGO_API_KEY", "a\nb"),
        ("TIINGO_API_KEY", "x" * 600),
        ("LLM_API_BASE_URL", "api.openai.com/v1"),
        ("SEC_USER_AGENT", "FinScopeAI no-contact"),
        ("ALLOW_REGISTRATION", "maybe"),
    ):
        with pytest.raises(AppError):
            validate_value(managed_key(key), value)


def test_unknown_key_is_not_found() -> None:
    with pytest.raises(AppError) as info:
        managed_key("DATABASE_URL")
    assert info.value.status_code == 404
