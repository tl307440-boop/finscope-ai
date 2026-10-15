from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SyncJobBody(BaseModel):
    job_type: str = Field(pattern=r"^(akshare_daily|fred_series)$")
    scope: dict[str, str] = Field(min_length=1)


class SyncJobPublic(BaseModel):
    id: UUID
    job_type: str
    status: str
    dedupe_key: str
    attempts: int
    max_attempts: int
    rows_written: int
    error_code: str | None = None
    error_summary: str | None = None
    progress_stage: str
    requested_at: datetime


class EnqueueData(BaseModel):
    created: bool
    job: SyncJobPublic


class SyncJobList(BaseModel):
    jobs: list[SyncJobPublic]
