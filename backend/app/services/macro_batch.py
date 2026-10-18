"""一次取回多条宏观序列（美国 + 中国）在某观察日可见的观测。可见性与单序列接口同一套规则。"""

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Protocol

from app.catalog.macro_series import catalog_codes, seed_for
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint, select_visible
from app.schemas.macro import ObservationBatch, build_observations
from app.services.china_macro import CHINA_SERIES, series_spec

MAX_BATCH_CODES = 20


# 每条观测至少占多少个自然日（宁大勿小，保证下界之后一定够 limit 条）。
# 日度按交易日约 0.69 条/天取 1.6 天，周度 8 天，月度 35 天，季度及未知 100 天。
_DAYS_PER_OBSERVATION = {"Daily": 1.6, "Weekly": 8.0, "Monthly": 35.0}
_DEFAULT_DAYS_PER_OBSERVATION = 100.0
_LOOKBACK_MARGIN_DAYS = 30


class VintageSource(Protocol):
    def list_vintages_many(
        self,
        series_codes: Sequence[str],
        *,
        since: Mapping[str, date] | None = None,
    ) -> dict[str, list[VintagePoint]]: ...


def parse_codes(raw: str) -> list[str]:
    codes = list(dict.fromkeys(item.strip().upper() for item in raw.split(",") if item.strip()))
    if not codes or len(codes) > MAX_BATCH_CODES:
        raise AppError(
            code="VALIDATION_ERROR",
            message=f"codes 需要 1–{MAX_BATCH_CODES} 个序列代码",
            status_code=422,
        )
    known = set(catalog_codes()) | {code for code, _name, _unit in CHINA_SERIES}
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise AppError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
            details={"unknown_codes": unknown},
        )
    return codes


def frequency_of(series_code: str) -> str:
    """美国序列看 FRED / 东方财富目录，中国序列看中国目录；都没有时返回空串。"""

    seed = seed_for(series_code)
    if seed is not None:
        return seed.frequency
    china = series_spec(series_code)
    return china.frequency if china is not None else ""


def limit_for(series_code: str, settings: Settings | None = None) -> int:
    """按频率取最近 N 条，数值来自 MACRO_BATCH_LIMIT_*，够算同比和画多年走势。"""

    active = settings or get_settings()
    frequency = frequency_of(series_code)
    if frequency == "Daily":
        return active.macro_batch_limit_daily
    if frequency == "Weekly":
        return active.macro_batch_limit_weekly
    return active.macro_batch_limit_other


def read_floor(series_code: str, pit: date, settings: Settings | None = None) -> date:
    """读库的观察日下界：库里历史存得再长，每次也只读 limit 条所需的那一段。"""

    active = settings or get_settings()
    per_observation = _DAYS_PER_OBSERVATION.get(
        frequency_of(series_code), _DEFAULT_DAYS_PER_OBSERVATION
    )
    days = math.ceil(limit_for(series_code, active) * per_observation) + _LOOKBACK_MARGIN_DAYS
    try:
        return pit - timedelta(days=days)
    except OverflowError:
        return date.min


def build_observation_batch(
    source: VintageSource,
    codes: Sequence[str],
    pit: date,
    settings: Settings | None = None,
) -> ObservationBatch:
    active = settings or get_settings()
    rows_by_code = source.list_vintages_many(
        codes, since={code: read_floor(code, pit, active) for code in codes}
    )
    return ObservationBatch(
        pit_date=pit,
        items=[
            build_observations(
                series_code=code,
                pit=pit,
                rows=select_visible(rows_by_code.get(code, []), pit),
                limit=limit_for(code, active),
            )
            for code in codes
        ],
    )
