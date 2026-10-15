from pydantic import BaseModel

from app.services.market_temperature import TemperatureResult


class FactorItem(BaseModel):
    code: str
    label: str
    weight: str
    score: str | None = None
    raw: str
    note: str


class TemperatureData(BaseModel):
    status: str
    label: str
    score: str | None = None
    coverage: str
    notice: str
    factors: list[FactorItem]


class SectorItem(BaseModel):
    name: str
    status: str
    label: str
    score: str | None = None
    coverage: str
    notice: str


class SectorList(BaseModel):
    sectors: list[SectorItem]


def build_temperature(result: TemperatureResult) -> TemperatureData:
    return TemperatureData(
        status=result.status,
        label=result.label,
        score=None if result.score is None else str(result.score),
        coverage=result.coverage,
        notice=result.notice,
        factors=[
            FactorItem(
                code=item.code,
                label=item.label,
                weight=str(item.weight),
                score=None if item.score is None else str(item.score),
                raw=item.raw,
                note=item.note,
            )
            for item in result.factors
        ],
    )
