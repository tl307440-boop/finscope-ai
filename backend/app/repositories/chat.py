import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.chat import ChatMessage, ChatSession
from app.services.chat_reply import StoredTurn, accept_question, cancel_turn
from app.utils.time import utc_now


@dataclass(frozen=True)
class SessionRecord:
    id: UUID
    title: str
    created_at: datetime


@dataclass(frozen=True)
class MessageRecord:
    id: UUID
    role: str
    content: str
    client_message_id: UUID
    completion_status: str
    created_at: datetime


class ChatRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create_session(self, user_id: UUID, title: str) -> SessionRecord:
        now = utc_now()
        row = ChatSession(
            id=uuid.uuid4(),
            user_id=user_id,
            title=title.strip()[:80] or "新的问答",
            created_at=now,
            updated_at=now,
        )
        with Session(self.engine) as session, session.begin():
            session.add(row)
            session.flush()
            return SessionRecord(id=row.id, title=row.title, created_at=row.created_at)

    def list_sessions(self, user_id: UUID) -> list[SessionRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.created_at.desc())
            ).all()
            return [
                SessionRecord(id=row.id, title=row.title, created_at=row.created_at) for row in rows
            ]

    def list_messages(self, user_id: UUID, session_id: UUID) -> list[MessageRecord] | None:
        with Session(self.engine) as session:
            if not _owned(session, user_id, session_id):
                return None
            rows = session.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at, ChatMessage.role.desc())
            ).all()
            return [_message(row) for row in rows]

    def accept(
        self,
        user_id: UUID,
        session_id: UUID,
        client_message_id: UUID,
        question: str,
        compose: Callable[[str], str],
    ) -> StoredTurn | None:
        with Session(self.engine) as session, session.begin():
            if not _owned(session, user_id, session_id):
                return None
            current = _turn(session, session_id, client_message_id)
            turn = accept_question(
                current,
                client_message_id=client_message_id,
                question=question,
                compose=compose,
            )
            if turn.created:
                now = utc_now()
                session.add(
                    ChatMessage(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        role="user",
                        content=turn.question,
                        client_message_id=client_message_id,
                        completion_status="completed",
                        created_at=now,
                    )
                )
                session.add(
                    ChatMessage(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        role="assistant",
                        content=turn.answer,
                        client_message_id=client_message_id,
                        completion_status=turn.status,
                        created_at=now,
                    )
                )
            return turn

    def cancel(
        self,
        user_id: UUID,
        session_id: UUID,
        client_message_id: UUID,
    ) -> StoredTurn | None:
        with Session(self.engine) as session, session.begin():
            if not _owned(session, user_id, session_id):
                return None
            current = _turn(session, session_id, client_message_id)
            updated = cancel_turn(current)
            if updated is None or current is None:
                return updated
            if updated.status != current.status:
                assistant = session.scalars(
                    select(ChatMessage).where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.client_message_id == client_message_id,
                        ChatMessage.role == "assistant",
                    )
                ).one()
                assistant.completion_status = updated.status
            return updated


def _owned(session: Session, user_id: UUID, session_id: UUID) -> bool:
    row = session.get(ChatSession, session_id)
    return row is not None and row.user_id == user_id


def _turn(session: Session, session_id: UUID, client_message_id: UUID) -> StoredTurn | None:
    rows = session.scalars(
        select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.client_message_id == client_message_id,
        )
    ).all()
    if not rows:
        return None
    question = next((row.content for row in rows if row.role == "user"), "")
    answer = next((row for row in rows if row.role == "assistant"), None)
    if answer is None:
        return None
    return StoredTurn(
        client_message_id=client_message_id,
        question=question,
        answer=answer.content,
        status=answer.completion_status,
        created=False,
    )


def _message(row: ChatMessage) -> MessageRecord:
    return MessageRecord(
        id=row.id,
        role=row.role,
        content=row.content,
        client_message_id=row.client_message_id,
        completion_status=row.completion_status,
        created_at=row.created_at,
    )
