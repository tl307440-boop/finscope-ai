import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MacroRegimeSnapshot(Base):
    """某个观察日的宏观状态判断快照。

    同一 (as_of, model_version) 重算只更新这一行；换了模型版本就是新行，旧版本的结论不会被覆盖。
    """

    __tablename__ = "macro_regime_snapshots"
    __table_args__ = (
        UniqueConstraint("as_of", "model_version", name="uq_macro_regime_snapshots_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    regime_code: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
