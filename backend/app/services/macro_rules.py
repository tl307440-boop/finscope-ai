from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RULE_VERSION = "macro-rules-v1"
RULE_NOTICE = "这是规则计算，不是经济定论。不同频率的水平值没有相加。"


@dataclass(frozen=True)
class MacroPoint:
    series_code: str
    observation_date: date
    value: Decimal
    realtime_start: date


@dataclass(frozen=True)
class RuleScore:
    code: str
    value: int | None
    reason: str | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class MacroAssessment:
    regime_code: str
    growth: RuleScore
    inflation: RuleScore
    liquidity: RuleScore
    notice: str
    version: str


def assess_macro(points: tuple[MacroPoint, ...] | list[MacroPoint]) -> MacroAssessment:
    growth = _score("growth", points, "GDP", tighter_when_rising=False)
    inflation = _score("inflation", points, "CPIAUCSL", tighter_when_rising=False)
    liquidity = _score("liquidity", points, "FEDFUNDS", tighter_when_rising=True)
    return MacroAssessment(
        regime_code=_regime(growth.value, inflation.value, liquidity.value),
        growth=growth,
        inflation=inflation,
        liquidity=liquidity,
        notice=RULE_NOTICE,
        version=RULE_VERSION,
    )


def _score(
    code: str,
    points: tuple[MacroPoint, ...] | list[MacroPoint],
    series_code: str,
    *,
    tighter_when_rising: bool,
) -> RuleScore:
    series = sorted(
        (point for point in points if point.series_code == series_code),
        key=lambda point: point.observation_date,
    )
    if len(series) < 2:
        return RuleScore(
            code=code,
            value=None,
            reason="insufficient_data",
            evidence=tuple(_evidence(point) for point in series),
        )
    previous = series[-2]
    latest = series[-1]
    if latest.value > previous.value:
        value = 30 if tighter_when_rising else 70
    elif latest.value < previous.value:
        value = 70 if tighter_when_rising else 30
    else:
        value = 50
    return RuleScore(
        code=code,
        value=value,
        reason=None,
        evidence=(_evidence(previous), _evidence(latest)),
    )


def _evidence(point: MacroPoint) -> str:
    return (
        f"{point.series_code} {point.observation_date.isoformat()} "
        f"{_plain(point.value)} 公布日 {point.realtime_start.isoformat()}"
    )


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _regime(growth: int | None, inflation: int | None, liquidity: int | None) -> str:
    if growth is None or inflation is None or liquidity is None:
        return "insufficient_data"
    growth_side = "up" if growth >= 50 else "down"
    inflation_side = "up" if inflation >= 50 else "down"
    liquidity_side = "easy" if liquidity >= 50 else "tight"
    return f"{growth_side}-{inflation_side}-{liquidity_side}"
