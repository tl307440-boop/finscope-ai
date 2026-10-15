import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

NO_MODEL_REPLY = "未配置模型，不能根据这个问题编造财务数字。请查看已保存的研究记录。"


@dataclass(frozen=True)
class StoredTurn:
    client_message_id: UUID
    question: str
    answer: str
    status: str
    created: bool


def accept_question(
    existing: StoredTurn | None,
    *,
    client_message_id: UUID,
    question: str,
    compose: Callable[[str], str],
) -> StoredTurn:
    if existing is not None:
        return StoredTurn(
            client_message_id=existing.client_message_id,
            question=existing.question,
            answer=existing.answer,
            status=existing.status,
            created=False,
        )
    if not question.strip():
        raise ValueError("请输入问题")
    return StoredTurn(
        client_message_id=client_message_id,
        question=question.strip(),
        answer=compose(question.strip()),
        status="completed",
        created=True,
    )


def cancel_turn(existing: StoredTurn | None) -> StoredTurn | None:
    if existing is None or existing.status == "completed":
        return existing
    return StoredTurn(
        client_message_id=existing.client_message_id,
        question=existing.question,
        answer=existing.answer,
        status="cancelled",
        created=False,
    )


def rules_reply(question: str) -> str:
    del question
    return NO_MODEL_REPLY


def event_stream(text: str, size: int = 24) -> list[str]:
    parts = [text[index : index + size] for index in range(0, len(text), size)] or [""]
    events = ["data: " + json.dumps({"delta": part}, ensure_ascii=False) + "\n\n" for part in parts]
    events.append("data: " + json.dumps({"done": True}) + "\n\n")
    return events
