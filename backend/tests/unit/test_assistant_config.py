"""助手配置：默认关闭、范围校验、与过期时间的关系；运行时开关登记在集成设置里。"""

import pytest
from app.core.config import Settings
from app.services.runtime_settings import MANAGED
from pydantic import ValidationError


def test_the_assistant_is_off_by_default_with_safe_budgets() -> None:
    settings = Settings()
    assert settings.assistant_enabled is False
    assert settings.llm_vision_model == ""
    assert settings.assistant_max_steps == 4
    assert settings.assistant_fast_path is True
    assert settings.assistant_turn_seconds == 150.0
    # 回合上限留出了探测与落库的余量，运行中的回合不会被当成残留。
    assert settings.assistant_turn_seconds + 30 <= settings.assistant_stale_seconds


@pytest.mark.parametrize("steps", [0, 9, -1])
def test_max_steps_must_be_between_one_and_eight(steps: int) -> None:
    with pytest.raises(ValidationError, match="ASSISTANT_MAX_STEPS"):
        Settings(assistant_max_steps=steps)


@pytest.mark.parametrize("seconds", [0, -5])
def test_turn_seconds_must_be_positive(seconds: float) -> None:
    with pytest.raises(ValidationError, match="ASSISTANT_TURN_SECONDS"):
        Settings(assistant_turn_seconds=seconds)


def test_a_running_turn_must_not_outlive_the_stale_cutoff() -> None:
    with pytest.raises(ValidationError, match="ASSISTANT_STALE_SECONDS"):
        Settings(assistant_turn_seconds=280, assistant_stale_seconds=300)
    ok = Settings(assistant_turn_seconds=270, assistant_stale_seconds=300)
    assert ok.assistant_turn_seconds == 270


def test_the_switch_is_a_managed_runtime_setting() -> None:
    entry = next(item for item in MANAGED if item.key == "ASSISTANT_ENABLED")
    assert (entry.field, entry.kind, entry.secret) == ("assistant_enabled", "bool", False)
    vision = next(item for item in MANAGED if item.key == "LLM_VISION_MODEL")
    assert (vision.field, vision.kind, vision.secret) == ("llm_vision_model", "text", False)
