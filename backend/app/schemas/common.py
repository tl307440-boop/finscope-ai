from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.core.config import DataMode

DEMO_NOTICE = "演示数据/非实时。当前响应不是真实行情或财报。"


class ResponseMeta(BaseModel):
    request_id: str
    as_of: datetime
    source: str
    stale: bool
    data_mode: DataMode
    notice: str | None = None


class LiveData(BaseModel):
    status: Literal["alive"]
    service: Literal["api"]


class ComponentCheck(BaseModel):
    status: Literal["up", "down"]
    reason: str | None = None
    latency_ms: int | None = None
    last_seen_at: datetime | None = None


class ReadyData(BaseModel):
    status: Literal["ready"]
    checks: dict[str, ComponentCheck]
