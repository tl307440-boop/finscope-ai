from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DashboardSeriesSnapshot(Base):
    """总览驾驶舱上游序列的「上次成功抓取」快照（如 FRED 各序列）。

    只用来在服务重启 / 上游暂时不通时先给用户展示旧值，不参与任何口径计算；
    抓取成功后整体覆盖。
    """

    __tablename__ = "dashboard_series_snapshots"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
