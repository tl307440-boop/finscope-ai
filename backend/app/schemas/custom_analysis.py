from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

Status = Literal["ok", "stale", "unavailable"]
IndicatorCode = Literal["erp", "buffett", "median_pb"]


class PcrPoint(BaseModel):
    trade_date: date
    index_close: float | None = None
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    volume_pcr: float | None = None
    volume_pcr_ma10: float | None = None
    oi_pcr: float | None = None
    volume_pcr_percentile_5y: float | None = None
    sentiment_signal: str | None = None
    relative_signal: str | None = None
    quality_flag: str


class PcrBasketView(BaseModel):
    kind: Literal["etf", "index"]
    label: str
    members: list[str]
    note: str
    status: Status
    reason: str | None = None
    first_date: date | None = None
    last_date: date | None = None
    history_years: float | None = None
    incomplete_days: int = 0
    latest: PcrPoint | None = None
    points: list[PcrPoint] = []


class PcrView(BaseModel):
    group_code: str
    group_name: str
    index_code: str
    index_name: str
    baskets: list[PcrBasketView]
    rules: list[str]
    rules_version: str


class IndicatorPoint(BaseModel):
    trade_date: date
    value: float
    percentile_5y: float | None = None
    band_mean: float | None = None
    band_upper: float | None = None
    band_lower: float | None = None
    quality_flag: str
    extras: dict[str, float | int | str | None] = {}


class InputStatus(BaseModel):
    name: str
    source: str
    last_date: date | None = None
    rows: int = 0


class IndicatorView(BaseModel):
    code: IndicatorCode
    name: str
    unit: str
    definition: str
    status: Status
    reason: str | None = None
    zone: str | None = None
    percentile_note: str | None = None
    latest: IndicatorPoint | None = None
    points: list[IndicatorPoint] = []
    inputs: list[InputStatus] = []
    notes: list[str] = []


class ValuationView(BaseModel):
    indicators: list[IndicatorView]
    rules_version: str


class SourceRow(BaseModel):
    key: str
    label: str
    provider: str
    url: str
    frequency: str
    status: Status
    rows: int = 0
    first_date: date | None = None
    last_date: date | None = None
    review_rows: int = 0
    fetched_at: datetime | None = None
    note: str = ""


class QualityIssue(BaseModel):
    trade_date: date
    subject: str
    note: str


class SourcesView(BaseModel):
    sources: list[SourceRow]
    recent_issues: list[QualityIssue]
    rules_version: str
