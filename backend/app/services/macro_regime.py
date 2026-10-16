"""宏观状态判断：按观察日取当时已公布的数据打分，并把结论存成可回看的快照。"""

from datetime import date

from sqlalchemy import Engine

from app.providers.vintage import VintagePoint, select_visible
from app.repositories.macro import MacroRepository
from app.repositories.macro_regime import MacroRegimeRepository, RegimeSnapshot
from app.schemas.macro import build_assessment
from app.services.macro_rules import MacroAssessment, MacroPoint, assess_macro
from app.utils.time import utc_now

ASSESSED_SERIES = ("GDP", "CPIAUCSL", "FEDFUNDS")


def points_from(series_code: str, rows: list[VintagePoint]) -> list[MacroPoint]:
    return [
        MacroPoint(
            series_code=series_code,
            observation_date=row.observation_date,
            value=row.value,
            realtime_start=row.realtime_start,
        )
        for row in rows
    ]


def assess_as_of(repository: MacroRepository, pit: date) -> MacroAssessment:
    """只用 realtime_start <= pit 的版本；库里没有的序列直接缺席，结论会标 insufficient_data。"""

    points: list[MacroPoint] = []
    for series_code in ASSESSED_SERIES:
        rows = repository.list_vintages(series_code, start=None, end=None)
        if rows is None:
            continue
        points.extend(points_from(series_code, select_visible(rows, pit)))
    return assess_macro(points)


def compute_macro_regime(engine: Engine, *, as_of: date, model_version: str) -> int:
    """算一个观察日并落快照；同一 (as_of, model_version) 重算只更新这一行。"""

    result = assess_as_of(MacroRepository(engine), as_of)
    payload = build_assessment(result, as_of).model_dump(mode="json")
    MacroRegimeRepository(engine).upsert(
        RegimeSnapshot(
            as_of=as_of,
            model_version=model_version,
            rule_version=result.version,
            regime_code=result.regime_code,
            payload=payload,
            computed_at=utc_now(),
        )
    )
    return 1
