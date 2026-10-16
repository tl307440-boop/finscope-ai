"""问答可用的宏观资料。只读已入库观测，不让模型写查询。"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine

from app.catalog.macro_series import fred_series_catalog
from app.providers.vintage import pit_date, select_visible
from app.repositories.macro import MacroRepository


def macro_chat_evidence(
    engine: Engine,
    codes: tuple[str, ...],
    as_of: datetime,
) -> tuple[str, ...]:
    if not codes:
        return ()
    pit = pit_date(as_of)
    names = {seed.series_code: seed.name_cn for seed in fred_series_catalog()}
    repository = MacroRepository(engine)
    lines: list[str] = []
    for code in codes:
        points = repository.list_vintages(code, start=pit - timedelta(days=400), end=pit)
        if not points:
            continue
        for point in select_visible(points, pit)[-6:]:
            lines.append(
                f"{code} {names.get(code, code)} 观察日 {point.observation_date.isoformat()} "
                f"数值 {_plain(point.value)} 单位 {point.unit} "
                f"发布日 {point.realtime_start.isoformat()} 来源 FRED"
            )
    return tuple(lines)


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
