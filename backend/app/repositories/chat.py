import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.chat import ChatMessage, ChatSession
from app.services.chat_reply import StoredTurn, cancel_turn
from app.utils.time import utc_now

# 创建会话时的默认标题；第一问受理后才改成问题前 30 个字（规格 §3）。
DEFAULT_TITLES = frozenset({"新的问答", "研究问答"})
TITLE_CHARS = 30
DEFAULT_STALE_SECONDS = 300


class TurnExists(Exception):
    """同一会话里 client_message_id 已经有这一回合（并发重复提交时抛出）。"""


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
    # 只有带数据工具的回答才有；旧消息为 None，前端按纯文本显示。
    blocks: list[dict[str, Any]] | None = None
    sources: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None
    as_of: date | None = None
    grounding: str | None = None


@dataclass(frozen=True)
class TurnExtras:
    """回合定稿时随正文一起写入的内容（规格 §3 第 3 步）。"""

    blocks: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None
    as_of: date | None = None
    model: str | None = None
    grounding: str | None = None
    tool_trace: dict[str, Any] | None = None


@dataclass(frozen=True)
class HistoryTurn:
    """同一会话里一个已完成的旧回合：给模型的历史与「上文提到的实体」用。"""

    question: str
    answer: str
    trace: dict[str, Any] | None


@dataclass(frozen=True)
class TurnLookup:
    """会话归当前用户时才有；`turn` 为空表示这个 client_message_id 还没有回合。"""

    turn: StoredTurn | None


class ChatRepository:
    def __init__(self, engine: Engine, stale_seconds: int = DEFAULT_STALE_SECONDS) -> None:
        self.engine = engine
        self.stale_seconds = stale_seconds

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
            cutoff = utc_now() - timedelta(seconds=self.stale_seconds)
            return [_message(row, cutoff) for row in rows]

    def find_turn(
        self, user_id: UUID, session_id: UUID, client_message_id: UUID
    ) -> TurnLookup | None:
        """查这一回合。会话不归该用户返回 None。

        停在 streaming 超过阈值的回答是进程重启留下的残留，这里顺手落库为 failed。
        """

        with Session(self.engine) as session, session.begin():
            if not _owned(session, user_id, session_id):
                return None
            answer = _assistant_row(session, session_id, client_message_id)
            if answer is None:
                return TurnLookup(turn=None)
            if answer.completion_status == "streaming" and _is_stale(
                answer.created_at, self.stale_seconds
            ):
                answer.completion_status = "failed"
                answer.finished_at = utc_now()
            return TurnLookup(turn=_turn(session, session_id, client_message_id))

    def create_turn(
        self,
        user_id: UUID,
        session_id: UUID,
        client_message_id: UUID,
        question: str,
    ) -> bool:
        """第一段：一个短事务写入「提问 + streaming 状态的空回答」，提交后才去调模型。

        会话不归该用户返回 False；同一回合已存在抛 `TurnExists`。
        """

        now = utc_now()
        try:
            with Session(self.engine) as session, session.begin():
                chat = session.get(ChatSession, session_id)
                if chat is None or chat.user_id != user_id:
                    return False
                if chat.title.strip() in DEFAULT_TITLES:
                    chat.title = _title_from(question) or chat.title
                chat.updated_at = now
                session.add(
                    ChatMessage(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        role="user",
                        content=question,
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
                        content="",
                        client_message_id=client_message_id,
                        completion_status="streaming",
                        created_at=now,
                    )
                )
        except IntegrityError as exc:
            raise TurnExists from exc
        return True

    def finish_turn(
        self,
        session_id: UUID,
        client_message_id: UUID,
        *,
        status: str,
        content: str,
        extras: TurnExtras | None = None,
    ) -> str:
        """第二段：回答定稿。只有仍是 streaming 的回合才会被写入。

        用户在这期间取消了（或残留被判 failed），写入会被跳过，返回库里实际的状态，
        调用方据此决定给浏览器推什么。
        """

        values: dict[str, Any] = {
            "content": content,
            "completion_status": status,
            "finished_at": utc_now(),
        }
        if extras is not None:
            values.update(
                blocks=extras.blocks,
                steps=extras.steps,
                as_of=extras.as_of,
                model=extras.model,
                grounding=extras.grounding,
                tool_trace=extras.tool_trace,
            )
        with Session(self.engine) as session, session.begin():
            written = session.execute(
                update(ChatMessage)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.client_message_id == client_message_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.completion_status == "streaming",
                )
                .values(**values)
                .returning(ChatMessage.id)
            ).first()
            if written is not None:
                return status
            current = session.scalars(
                select(ChatMessage.completion_status).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.client_message_id == client_message_id,
                    ChatMessage.role == "assistant",
                )
            ).first()
            return current or "failed"

    def append_block(
        self, session_id: UUID, client_message_id: UUID, block: dict[str, Any]
    ) -> bool:
        """推送前先把块追加到回答行（一个短事务），中途断开也能从库里还原已有的块。

        只写仍是 streaming 的回合；已取消的回合保留取消前追加的块，之后的丢弃。
        """

        with Session(self.engine) as session, session.begin():
            written = session.execute(
                text(
                    "UPDATE chat_messages "
                    "SET blocks = COALESCE(blocks, '[]'::jsonb) || CAST(:block AS jsonb) "
                    "WHERE session_id = :session_id AND client_message_id = :client_message_id "
                    "AND role = 'assistant' AND completion_status = 'streaming' "
                    "RETURNING id"
                ),
                {
                    "block": json.dumps([block], ensure_ascii=False),
                    "session_id": session_id,
                    "client_message_id": client_message_id,
                },
            ).first()
        return written is not None

    def turn_status(self, session_id: UUID, client_message_id: UUID) -> str | None:
        """回答行当前的状态；编排器据此发现用户取消了。不存在返回 None。"""

        with Session(self.engine) as session:
            return session.scalars(
                select(ChatMessage.completion_status).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.client_message_id == client_message_id,
                    ChatMessage.role == "assistant",
                )
            ).first()

    def recent_turns(self, session_id: UUID, exclude: UUID, limit: int = 3) -> list[HistoryTurn]:
        """同一会话最近 `limit` 个已完成的回合，由旧到新。调用方已确认会话归属。"""

        with Session(self.engine) as session:
            answers = session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.completion_status == "completed",
                    ChatMessage.client_message_id != exclude,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            ).all()
            if not answers:
                return []
            questions = {
                row.client_message_id: row.content
                for row in session.scalars(
                    select(ChatMessage).where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "user",
                        ChatMessage.client_message_id.in_([a.client_message_id for a in answers]),
                    )
                )
            }
            return [
                HistoryTurn(questions[row.client_message_id], row.content, row.tool_trace)
                for row in reversed(answers)
                if row.client_message_id in questions
            ]

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
                assistant = _assistant_row(session, session_id, client_message_id)
                if assistant is not None:
                    assistant.completion_status = updated.status
                    assistant.finished_at = utc_now()
            return updated

    def count_assistant_turns_since(self, user_id: UUID, since: datetime) -> int:
        """该用户从 `since` 起的助手消息条数，每日额度据此判断（重启不清零）。"""

        with Session(self.engine) as session:
            total = session.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .where(
                    ChatSession.user_id == user_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.created_at >= since,
                )
            )
            return int(total or 0)


def _title_from(question: str) -> str:
    return " ".join(question.split())[:TITLE_CHARS]


def _is_stale(created_at: datetime, stale_seconds: int) -> bool:
    return utc_now() - created_at > timedelta(seconds=stale_seconds)


def _owned(session: Session, user_id: UUID, session_id: UUID) -> bool:
    row = session.get(ChatSession, session_id)
    return row is not None and row.user_id == user_id


def _assistant_row(
    session: Session, session_id: UUID, client_message_id: UUID
) -> ChatMessage | None:
    return session.scalars(
        select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.client_message_id == client_message_id,
            ChatMessage.role == "assistant",
        )
    ).first()


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
        blocks=answer.blocks,
        steps=answer.steps,
        as_of=answer.as_of,
        model=answer.model,
        grounding=answer.grounding,
    )


def _message(row: ChatMessage, stale_cutoff: datetime) -> MessageRecord:
    status = row.completion_status
    if row.role == "assistant" and status == "streaming" and row.created_at < stale_cutoff:
        status = "failed"
    return MessageRecord(
        id=row.id,
        role=row.role,
        content=row.content,
        client_message_id=row.client_message_id,
        completion_status=status,
        created_at=row.created_at,
        blocks=row.blocks,
        sources=row.sources,
        steps=row.steps,
        as_of=row.as_of,
        grounding=row.grounding,
    )
