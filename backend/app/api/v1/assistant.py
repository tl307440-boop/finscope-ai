from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user
from app.api.responses import success_response
from app.core.exceptions import AppError
from app.repositories.chat import ChatRepository, MessageRecord, SessionBusy, SessionRecord
from app.services.assistant.chart_image import ChartRejected, chart_exists, decode_chart, load_chart
from app.services.assistant.chart_skill import question_text
from app.services.assistant.limits import AssistantLimiter
from app.services.assistant.orchestrator import TurnRunner
from app.services.assistant.responder import AssistantResponder
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.turns import (
    AssistantConfig,
    RateLimited,
    Responder,
    SessionNotFound,
    TurnInProgress,
    TurnService,
)
from app.services.assistant_context import macro_chat_evidence
from app.services.auth_types import UserRecord
from app.services.chat_model import complete_chat
from app.services.chat_reply import (
    NO_OBSERVATION_REPLY,
    answer_with_evidence,
    match_macro_codes,
    rules_reply,
)
from app.services.llm_gateway import model_availability
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

router = APIRouter(prefix="/assistant", tags=["问答"])


class SessionCreate(BaseModel):
    title: str = Field(default="新的问答", max_length=80)


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class SessionItem(BaseModel):
    id: UUID
    title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SessionDeleted(BaseModel):
    id: UUID
    deleted: bool = True


class SessionList(BaseModel):
    sessions: list[SessionItem]


class ChartUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
    data_base64: str = Field(min_length=16, max_length=8_000_000)


class MessageCreate(BaseModel):
    client_message_id: UUID
    content: str = Field(default="", max_length=2000)
    chart: ChartUpload | None = None


class MessageItem(BaseModel):
    id: UUID
    role: str
    content: str
    client_message_id: UUID
    completion_status: str
    # 带数据工具的回答才有；旧消息为 null，按纯文本显示。
    blocks: list[dict[str, Any]] | None = None
    sources: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None
    as_of: date | None = None
    grounding: str | None = None
    has_chart: bool = False


class MessageList(BaseModel):
    messages: list[MessageItem]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _repository(request: Request) -> ChatRepository:
    config = cast(AssistantConfig, request.app.state.assistant_config)
    return ChatRepository(_engine(request), stale_seconds=config.stale_seconds)


def _responder(request: Request) -> Responder:
    state = request.app.state
    engine = _engine(request)
    return AssistantResponder(
        engine=engine,
        repository=_repository(request),
        registry=cast(ToolRegistry, state.assistant_registry),
        config=cast(AssistantConfig, state.assistant_config),
        legacy=_composer(engine),
        chart_root=_upload_root(request),
    )


def _upload_root(request: Request) -> Path:
    override = getattr(request.app.state, "upload_root", None)
    if isinstance(override, Path):
        return override
    return Path("data/uploads")


def _turns(request: Request) -> TurnService:
    state = request.app.state
    config = cast(AssistantConfig, state.assistant_config)
    return TurnService(
        _repository(request),
        cast(TurnRunner, state.assistant_runner),
        cast(AssistantLimiter, state.assistant_limiter),
        daily_limit=config.daily_limit,
    )


@router.post("/sessions", summary="新建问答")
def create_session(
    body: SessionCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    created = _repository(request).create_session(user.id, body.title)
    return JSONResponse(status_code=200, content=success_response(request, _session(created)))


@router.get("/sessions", summary="我的问答")
def list_sessions(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = _repository(request).list_sessions(user.id)
    return JSONResponse(status_code=200, content=success_response(request, _sessions(rows)))


@router.patch("/sessions/{session_id}", summary="重命名问答")
def rename_session(
    session_id: UUID,
    body: SessionRename,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    try:
        renamed = _repository(request).rename_session(user.id, session_id, body.title)
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    if renamed is None:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404)
    return JSONResponse(status_code=200, content=success_response(request, _session(renamed)))


@router.delete("/sessions/{session_id}", summary="删除问答（连同其中的消息）")
def delete_session(
    session_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    try:
        deleted = _repository(request).delete_session(user.id, session_id)
    except SessionBusy as exc:
        raise AppError(
            code="SESSION_BUSY",
            message="这个问答里还有回答正在生成，请等它结束或取消后再删除",
            status_code=409,
        ) from exc
    if not deleted:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404)
    return JSONResponse(
        status_code=200, content=success_response(request, SessionDeleted(id=session_id))
    )


@router.get("/sessions/{session_id}/messages", summary="问答历史")
def list_messages(
    session_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    rows = _repository(request).list_messages(user.id, session_id)
    if rows is None:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404)
    return JSONResponse(
        status_code=200,
        content=success_response(request, _messages(rows, user.id, _upload_root(request))),
    )


@router.post("/sessions/{session_id}/messages", summary="发送问题并流式返回已保存的回答")
def create_message(
    session_id: UUID,
    body: MessageCreate,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> StreamingResponse:
    try:
        chart = (
            None
            if body.chart is None
            else decode_chart(body.chart.media_type, body.chart.data_base64)
        )
        text = question_text(body.content, has_chart=chart is not None)
    except ChartRejected as exc:
        raise AppError(code="VALIDATION_ERROR", message=exc.message, status_code=422) from exc
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    try:
        events = _turns(request).start(
            user.id,
            session_id,
            body.client_message_id,
            text,
            _responder(request),
            chart,
        )
    except ValueError as exc:
        raise AppError(code="VALIDATION_ERROR", message=str(exc), status_code=422) from exc
    except SessionNotFound as exc:
        raise AppError(code="NOT_FOUND", message="找不到这个问答", status_code=404) from exc
    except TurnInProgress as exc:
        raise AppError(
            code="TURN_IN_PROGRESS",
            message="这条提问的回答还在生成，请稍后刷新查看",
            status_code=409,
        ) from exc
    except RateLimited as exc:
        raise AppError(code="RATE_LIMITED", message=exc.message, status_code=429) from exc
    # 关掉代理缓冲：nginx 看到 X-Accel-Buffering: no 就不会把事件攒成一批再发。
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/charts/{client_message_id}", summary="读取我上传的走势图")
def get_chart(
    client_message_id: UUID,
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> Response:
    if not _repository(request).owns_client_message(user.id, client_message_id):
        raise AppError(code="NOT_FOUND", message="找不到这张走势图", status_code=404)
    loaded = load_chart(_upload_root(request), user.id, client_message_id)
    if loaded is None:
        raise AppError(code="NOT_FOUND", message="找不到这张走势图", status_code=404)
    data, media_type = loaded
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


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
    turn = _repository(request).cancel(user.id, session_id, client_message_id)
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
    """没配模型时保持固定说明。配了也只许转述白名单宏观序列。

    回答在后台线程里生成，已不在数据库事务内；提问与 streaming 的空回答先提交。
    """

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


def _session(row: SessionRecord) -> SessionItem:
    return SessionItem(
        id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
    )


def _sessions(rows: list[SessionRecord]) -> SessionList:
    return SessionList(sessions=[_session(row) for row in rows])


def _messages(rows: list[MessageRecord], user_id: UUID, root: Path) -> MessageList:
    return MessageList(
        messages=[
            MessageItem(
                id=row.id,
                role=row.role,
                content=row.content,
                client_message_id=row.client_message_id,
                completion_status=row.completion_status,
                blocks=row.blocks,
                sources=row.sources,
                steps=row.steps,
                as_of=row.as_of,
                grounding=row.grounding,
                has_chart=row.role == "user" and chart_exists(root, user_id, row.client_message_id),
            )
            for row in rows
        ]
    )
