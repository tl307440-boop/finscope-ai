import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NewsEvent(Base):
    __tablename__ = "news_events"
    __table_args__ = (UniqueConstraint("dedupe_hash", name="uq_news_events_dedupe"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(300), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_rights: Mapped[str] = mapped_column(String(120), nullable=False)
    quality_flag: Mapped[str] = mapped_column(String(32), nullable=False)
