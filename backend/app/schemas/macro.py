from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.catalog.macro_series import MacroSeriesSeed
from app.providers.vintage import VintagePoint
from app.repositories.macro import decimal_text
from app.services.macro_rules import MacroAssessment, RuleScore

Availability = Literal["unavailable", "configured"]
CATALOG_NOTICE = "这里只列出序列定义。没有同步到的观测值不会用演示数字填补。"
EMPTY_OBSERVATIONS = "该日期还看不到已公布的数值"


class MacroSeriesItem(BaseModel):
    series_code: str
    name_cn: str
    name_en: str
    category: str
    frequency: str
    base_unit: str
    seasonal_adjustment: str


class MacroCatalog(BaseModel):
    provider_code: Literal["fred"]
    availability: Availability
    notice: str
    series: list[MacroSeriesItem]


class ObservationItem(BaseModel):
    observation_date: date
    value: str
    unit: str
    realtime_start: date
    realtime_end: date
    revision_no: int
    time_precision: Literal["date"]


class ObservationPage(BaseModel):
    series_code: str
    pit_date: date
    time_precision: Literal["date"]
    observations: list[ObservationItem]
    empty_message: str | None = None
    truncated: bool


class FredSyncResult(BaseModel):
    series_code: str
    rows_written: int
    preserved_existing: bool


class FredSyncRequest(BaseModel):
    series_code: str = Field(pattern=r"^[A-Z0-9]+$", min_length=1, max_length=32)
    observation_start: date
    observation_end: date


def build_catalog(seeds: list[MacroSeriesSeed], *, availability: Availability) -> MacroCatalog:
    ordered = sorted(seeds, key=lambda item: item.series_code)
    return MacroCatalog(
        provider_code="fred",
        availability=availability,
        notice=CATALOG_NOTICE,
        series=[
            MacroSeriesItem(
                series_code=item.series_code,
                name_cn=item.name_cn,
                name_en=item.name_en,
                category=item.category,
                frequency=item.frequency,
                base_unit=item.base_unit,
                seasonal_adjustment=item.seasonal_adjustment,
            )
            for item in ordered
        ],
    )


def build_observations(
    *,
    series_code: str,
    pit: date,
    rows: list[VintagePoint],
    limit: int,
) -> ObservationPage:
    visible = rows[:limit]
    return ObservationPage(
        series_code=series_code,
        pit_date=pit,
        time_precision="date",
        observations=[
            ObservationItem(
                observation_date=row.observation_date,
                value=decimal_text(row.value),
                unit=row.unit,
                realtime_start=row.realtime_start,
                realtime_end=row.realtime_end,
                revision_no=row.revision_no,
                time_precision="date",
            )
            for row in visible
        ],
        empty_message=EMPTY_OBSERVATIONS if not rows else None,
        truncated=len(rows) > limit,
    )


class RuleScoreModel(BaseModel):
    code: str
    value: int | None = None
    reason: str | None = None
    evidence: list[str]


class AssessmentData(BaseModel):
    pit_date: date
    regime_code: str
    growth: RuleScoreModel
    inflation: RuleScoreModel
    liquidity: RuleScoreModel
    notice: str
    version: str


def build_assessment(result: MacroAssessment, pit: date) -> AssessmentData:
    return AssessmentData(
        pit_date=pit,
        regime_code=result.regime_code,
        growth=_score_model(result.growth),
        inflation=_score_model(result.inflation),
        liquidity=_score_model(result.liquidity),
        notice=result.notice,
        version=result.version,
    )


def _score_model(score: RuleScore) -> RuleScoreModel:
    return RuleScoreModel(
        code=score.code,
        value=score.value,
        reason=score.reason,
        evidence=list(score.evidence),
    )
