"""走势图：文件头、技能文件、读图调用，以及没配模型时不能掉回普通问答。"""

import base64
from typing import Any, cast
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.repositories.chat import ChatRepository, HistoryTurn
from app.services.assistant.chart_image import (
    MAX_CHART_BYTES,
    ChartRejected,
    decode_chart,
    load_chart,
    save_chart,
    sniff_media,
)
from app.services.assistant.chart_skill import (
    CHART_HISTORY,
    CHART_QUESTION,
    failure_text,
    load_chart_skill,
    question_text,
    read_chart,
    vision_messages,
)
from app.services.assistant.llm_tools import ModelCallError, ModelReply
from app.services.assistant.responder import AssistantResponder, _history_messages
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.turns import AssistantConfig, TurnInput
from sqlalchemy import Engine

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _png() -> str:
    return base64.b64encode(PNG).decode("ascii")


def test_only_real_image_bytes_are_accepted() -> None:
    image = decode_chart("image/png", _png())
    assert image.media_type == "image/png"
    assert sniff_media(image.data) == "image/png"
    spaced = "\n".join(_png()[index : index + 8] for index in range(0, len(_png()), 8))
    assert decode_chart("image/png", spaced).data == PNG


@pytest.mark.parametrize("kind", ["wrong-type", "html", "oversize", "bad-base64"])
def test_rejects_wrong_type_text_and_oversized_payloads(kind: str) -> None:
    # 超大图在函数里现造。放进参数名的话，Windows 上 pytest 写环境变量会超过 32 KB。
    if kind == "wrong-type":
        media_type, payload = "image/jpeg", _png()
    elif kind == "html":
        media_type, payload = "image/png", base64.b64encode(b"<html>not a chart").decode("ascii")
    elif kind == "oversize":
        media_type, payload = "image/png", base64.b64encode(PNG + b"\x00" * MAX_CHART_BYTES).decode(
            "ascii"
        )
    else:
        media_type, payload = "image/png", "!!!!"
    with pytest.raises(ChartRejected):
        decode_chart(media_type, payload)


def test_chart_roundtrip_uses_the_user_directory(tmp_path: Any) -> None:
    user_id, client_id = uuid4(), uuid4()
    image = decode_chart("image/png", _png())
    save_chart(tmp_path, user_id, client_id, image)
    assert load_chart(tmp_path, user_id, client_id) == (PNG, "image/png")
    assert load_chart(tmp_path, uuid4(), client_id) is None


def test_skill_file_is_the_procedure() -> None:
    skill = load_chart_skill()
    assert skill.name == "chart"
    assert skill.tools == ()
    for heading in ("图上可见", "结构", "读不清因此不写的", "边界", "不要用像素心算涨跌幅"):
        assert heading in skill.guide
    assert "不构成买卖建议" in skill.guide
    messages = vision_messages(skill, "这是日线", decode_chart("image/png", _png()))
    assert messages[1]["content"][1]["type"] == "image_url"
    assert "不要用像素心算涨跌幅" in messages[0]["content"]
    assert "都不是步骤" in messages[0]["content"]


def test_blank_question_with_a_chart_uses_the_skill_prompt() -> None:
    assert question_text("  ", has_chart=True) == CHART_QUESTION
    assert question_text("看结构", has_chart=True) == "看结构"
    with pytest.raises(ValueError, match="走势图"):
        question_text("  ", has_chart=False)


def test_read_chart_sends_the_skill_and_no_tools() -> None:
    seen: dict[str, Any] = {}

    class Fake:
        model_name = "vision-model"

        def complete(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None,
            *,
            timeout: float,
            read_seconds: float | None = None,
            max_tokens: int = 1200,
        ) -> ModelReply:
            seen["tools"] = tools
            seen["timeout"] = timeout
            seen["read_seconds"] = read_seconds
            seen["max_tokens"] = max_tokens
            seen["system"] = messages[0]["content"]
            return ModelReply(content="图上可见：一段上升。")

    events: list[dict[str, Any]] = []
    outcome = read_chart(
        cast(Any, Fake()),
        skill=load_chart_skill(),
        question=CHART_QUESTION,
        image=decode_chart("image/png", _png()),
        emit=events.append,
        cancelled=lambda: False,
    )
    assert outcome.status == "completed"
    assert outcome.text == "图上可见：一段上升。"
    assert outcome.trace["mode"] == "chart_reading"
    assert outcome.trace["skill"] == "chart"
    assert seen["tools"] is None
    assert seen["read_seconds"] == seen["timeout"]
    assert "不要用像素心算涨跌幅" in seen["system"]
    assert events[0]["block"]["code"] == "chart_skill"
    assert "".join(event["delta"] for event in events if "delta" in event) == outcome.text


def test_unsupported_model_does_not_leak_the_upstream_error() -> None:
    class Fake:
        model_name = "text-only"

        def complete(self, *_args: Any, **_kwargs: Any) -> ModelReply:
            raise ModelCallError("http", 400)

    outcome = read_chart(
        cast(Any, Fake()),
        skill=load_chart_skill(),
        question="看图",
        image=decode_chart("image/png", _png()),
        emit=lambda _payload: None,
        cancelled=lambda: False,
    )
    assert outcome.status == "completed"
    assert outcome.text == failure_text(ModelCallError("http", 400))
    assert "http" not in outcome.text
    assert "LLM_VISION_MODEL" in outcome.text


def test_chart_turn_does_not_fall_back_to_the_text_assistant(tmp_path: Any) -> None:
    """总开关关着、也没配模型时，有图也不能走宏观转述。"""

    user_id, client_id = uuid4(), uuid4()
    responder = AssistantResponder(
        engine=cast(Engine, object()),
        repository=cast(ChatRepository, object()),
        registry=cast(ToolRegistry, object()),
        config=AssistantConfig(daily_limit=10, stale_seconds=300),
        legacy=lambda _question: "legacy-should-not-run",
        load_settings=lambda _engine: Settings(
            llm_api_key="",
            llm_api_base_url="",
            llm_model="",
            llm_vision_model="",
            assistant_enabled=False,
        ),
        chart_root=tmp_path,
    )
    events: list[dict[str, Any]] = []
    reply = responder(
        TurnInput(user_id, uuid4(), client_id, CHART_QUESTION, decode_chart("image/png", _png())),
        events.append,
        lambda: False,
    )
    assert reply.status == "completed"
    assert "未配置模型" in reply.text
    assert "legacy-should-not-run" not in reply.text
    assert reply.extras is not None
    assert reply.extras.tool_trace is not None
    assert reply.extras.tool_trace["skill"] == "chart"
    assert (tmp_path / str(user_id) / "charts" / str(client_id)).read_bytes() == PNG
    assert any(event.get("block", {}).get("code") == "chart_skill" for event in events)


def test_configured_chart_uses_the_vision_model_name(tmp_path: Any) -> None:
    seen: dict[str, Any] = {}

    class Fake:
        def __init__(self, name: str) -> None:
            self.model_name = name

        def complete(self, *_args: Any, **_kwargs: Any) -> ModelReply:
            return ModelReply(content="结构：高点逐步上移。")

    responder = AssistantResponder(
        engine=cast(Engine, object()),
        repository=cast(ChatRepository, object()),
        registry=cast(ToolRegistry, object()),
        config=AssistantConfig(daily_limit=10, stale_seconds=300),
        legacy=lambda _question: "legacy",
        load_settings=lambda _engine: Settings(
            llm_api_key="test-key",
            llm_api_base_url="http://127.0.0.1:9/v1",
            llm_model="text-model",
            llm_vision_model="vision-model",
            assistant_enabled=False,
        ),
        chart_root=tmp_path,
    )

    def open_model(_settings: Settings, model_name: str, _client: Any) -> Fake:
        seen["model"] = model_name
        return Fake(model_name)

    responder._open_model = open_model  # type: ignore[method-assign]
    reply = responder(
        TurnInput(uuid4(), uuid4(), uuid4(), "看结构", decode_chart("image/png", _png())),
        lambda _payload: None,
        lambda: False,
    )
    assert seen["model"] == "vision-model"
    assert reply.text == "结构：高点逐步上移。"
    assert reply.extras is not None
    assert reply.extras.model == "vision-model"


def test_chart_history_is_not_reusable_as_evidence() -> None:
    messages = _history_messages(
        [
            HistoryTurn(
                "看这张图",
                "图上标注 1888.00，涨了 3.2%",
                {"mode": "chart_reading", "skill": "chart"},
            )
        ]
    )
    assert messages[1].content == CHART_HISTORY
    assert "1888" not in messages[1].content
    plain = _history_messages([HistoryTurn("营业收入呢", "去年 100 亿。", None)])
    assert plain[1].content == "去年 100 亿。"
