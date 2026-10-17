"""一次取回多条美国宏观序列在某观察日可见的观测。可见性与单序列接口同一套规则。"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.catalog.macro_series import catalog_codes, seed_for
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint, select_visible
from app.schemas.macro import ObservationBatch, build_observations

MAX_BATCH_CODES = 20
DEFAULT_LIMIT = 120
_LIMIT_BY_FREQUENCY = {"Daily": 1000, "Weekly": 260}


class VintageSource(Protocol):
    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]: ...


def parse_codes(raw: str) -> list[str]:
    codes = list(dict.fromkeys(item.strip().upper() for item in raw.split(",") if item.strip()))
    if not codes or len(codes) > MAX_BATCH_CODES:
        raise AppError(
            code="VALIDATION_ERROR",
            message=f"codes 需要 1–{MAX_BATCH_CODES} 个序列代码",
            status_code=422,
        )
    known = catalog_codes()
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise AppError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
            details={"unknown_codes": unknown},
        )
    return codes


def limit_for(series_code: str) -> int:
    """日度约四年交易日，周度约五年，月度 / 季度十年：够算同比和画 1–5 年走势。"""

    seed = seed_for(series_code)
    if seed is None:
        return DEFAULT_LIMIT
    return _LIMIT_BY_FREQUENCY.get(seed.frequency, DEFAULT_LIMIT)


def build_observation_batch(
    source: VintageSource, codes: Sequence[str], pit: date
) -> ObservationBatch:
    rows_by_code = source.list_vintages_many(codes)
    return ObservationBatch(
        pit_date=pit,
        items=[
            build_observations(
                series_code=code,
                pit=pit,
                rows=select_visible(rows_by_code.get(code, []), pit),
                limit=limit_for(code),
            )
            for code in codes
        ],
    )
