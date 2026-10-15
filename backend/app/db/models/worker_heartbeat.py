from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkerHeartbeat(Base):
    """Worker 存活标记。业务表留到 T02。"""

    __tablename__ = "worker_heartbeats"

    service_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
