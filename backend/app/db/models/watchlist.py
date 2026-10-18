import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatchlistGroup(Base):
    __tablename__ = "watchlist_groups"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_groups_owner_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WatchlistItem(Base):
    """自选里的一行：指向本系统证券，或按东财 secid 保存的外部标的（二选一）。"""

    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("group_id", "instrument_id", name="uq_watchlist_items_group_instrument"),
        CheckConstraint(
            "num_nonnulls(instrument_id, ext_secid) = 1", name="ck_watchlist_items_target"
        ),
        Index(
            "uq_watchlist_items_group_secid",
            "group_id",
            "ext_secid",
            unique=True,
            postgresql_where=text("ext_secid IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("watchlist_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id"),
        nullable=True,
    )
    # 东财 secid，例如 90.BK0473（板块）、105.NVDA（美股）、171.US10Y（国债收益率）。
    ext_secid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ext_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
