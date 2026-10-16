from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

ItemState = Literal["fresh", "warn", "stale", "missing", "unavailable"]


class FreshnessItem(BaseModel):
    group: str
    key: str
    label: str
    state: ItemState
    detail: str
    latest_data_date: date | None = None
    last_synced_at: datetime | None = None


class FreshnessReport(BaseModel):
    generated_at: datetime
    scheduler_running: bool
    items: list[FreshnessItem]
