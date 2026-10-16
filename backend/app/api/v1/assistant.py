from collections.abc import Callable, Iterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.chat import ChatRepository, MessageRecord, SessionRecord
from app.services.assistant_context import macro_chat_evidence
from app.services.auth_types import UserRecord
from app.services.chat_model import complete_chat
from app.services.chat_reply import (
    NO_OBSERVATION_REPLY,
    answer_with_evidence,
    event_stream,
    match_macro_codes,
    rules_reply,
)
from app.services.llm_gateway import model_availability
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

router = APIRouter(prefix="/assistant", tags=["问答"])


class SessionCreate(BaseModel):
    title: str = Field(default="新的问答", max_length=80)


class SessionItem(BaseModel):
    id: UUID
    title: str


class SessionList(BaseModel):
    sessions: list[SessionItem]


class MessageCreate(BaseModel):
    client_message_id: UUID
    content: str = Field(min_length=1, max_length=2000)


class MessageItem(BaseModel):
    id: UUID
    role: str
    content: str
    client_message_id: UUID
    completion_status: str


class MessageList(BaseModel):
    messages: list[MessageItem]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


@router.post("/sessions", summary="新建问答")
def create_session(
    body: SessionCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    created = ChatRepository(_engine(request)).create_session(user.id, body.title)
    return JSONResponse(
        status_code=200,
        content=success_response(request, SessionItem(id=created.id, title=created.title)),
    )


@router.get("/sessions", summary="我的问答")
def list_sessions(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = ChatRepository(_engine(request)).list_sessions(user.id)
    return JSONResponse(status_code=200, content=success_response(request, _sessions(rows)))


@router.get("/sessions/{session_id}/messages", summary="问答历史")
def list_messages(
    session_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = ChatRepository(_engine(request)).list_messages(user.id, session_id)
    if rows is None:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _messages(rows)))


@router.post("/sessions/{session_id}/messages", summary="发送问题并流式返回已保存的回答")
def create_message(
    session_id: UUID,
    body: MessageCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> StreamingResponse:
    try:
        turn = ChatRepository(_engine(request)).accept(
            user.id,
            session_id,
            body.client_message_id,
            body.content,
            _composer(_engine(request)),
        )
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    if turn is None:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404)
    return StreamingResponse(_events(turn.answer), media_type="text/event-stream")


@router.post(
    "/sessions/{session_id}/messages/{client_message_id}/cancel",
    summary="取消尚未完成的回答",
)
def cancel_message(
    session_id: UUID,
    client_message_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    turn = ChatRepository(_engine(request)).cancel(user.id, session_id, client_message_id)
    if turn is None:
        raise AppError(code="NOT_FOUND", message="找不到这条提问", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            MessageItem(
                id=client_message_id,
                role="assistant",
                content=turn.answer,
                client_message_id=client_message_id,
                completion_status=turn.status,
            ),
        ),
    )


def _composer(engine: Engine) -> Callable[[str], str]:
    """没配模型时保持固定说明。配了也只许转述白名单宏观序列，调用发生在入库之前。"""

    def compose(question: str) -> str:
        settings = effective_settings(engine)
        available, _message = model_availability(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base_url,
            model=settings.llm_model,
        )
        if not available:
            return rules_reply(question)
        codes = match_macro_codes(question)
        evidence = macro_chat_evidence(engine, codes, utc_now())
        if codes and not evidence:
            return NO_OBSERVATION_REPLY
        return answer_with_evidence(
            question,
            evidence,
            lambda prompt: complete_chat(settings, prompt),
        )

    return compose


def _events(text: str) -> Iterator[str]:
    yield from event_stream(text)


def _sessions(rows: list[SessionRecord]) -> SessionList:
    return SessionList(sessions=[SessionItem(id=row.id, title=row.title) for row in rows])


def _messages(rows: list[MessageRecord]) -> MessageList:
    return MessageList(
        messages=[
            MessageItem(
                id=row.id,
                role=row.role,
                content=row.content,
                client_message_id=row.client_message_id,
                completion_status=row.completion_status,
            )
            for row in rows
        ]
    )
