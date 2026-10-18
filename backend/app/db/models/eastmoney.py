import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EastmoneyBinding(Base):
    """用户绑定的东方财富账号：扫码得到的登录 cookie（加密）与最近一次同步结果。"""

    __tablename__ = "eastmoney_bindings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    em_uid: Mapped[str] = mapped_column(String(32), nullable=False)
    em_alias: Mapped[str] = mapped_column(String(64), nullable=False)
    cookies_enc: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    bound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_message: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_sync_groups: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
