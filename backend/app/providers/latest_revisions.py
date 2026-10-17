"""只给最新值的来源 → 版本行。纯函数，无副作用，可离线测试。

来源本身没有修订历史，所以：
- 第一次见到某观测期：从发布日开始的开放版本；
- 之后同值：不写；
- 值变了：旧版本在抓取日前一天截止，新版本从抓取日开始；
  抓取日不晚于旧版本起点时直接改写旧版本。
首次同步之前的历史只能是来源的最新修订值。
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from app.providers.eastmoney_us_macro import LatestValue
from app.providers.fred import FredVintage
from app.providers.vintage import VintagePoint

OPEN_ENDED = date(9999, 12, 31)


def plan_latest_revisions(
    existing: Sequence[VintagePoint],
    fresh: Sequence[LatestValue],
    *,
    fetched_on: date,
) -> tuple[FredVintage, ...]:
    current: dict[date, VintagePoint] = {}
    for row in existing:
        if row.realtime_end != OPEN_ENDED:
            continue
        held = current.get(row.observation_date)
        if held is None or row.realtime_start > held.realtime_start:
            current[row.observation_date] = row
    planned: list[FredVintage] = []
    for point in fresh:
        held = current.get(point.observation_date)
        if held is None:
            planned.append(_open(point.observation_date, point.value, point.published_on))
            continue
        if held.value == point.value:
            continue
        if fetched_on <= held.realtime_start:
            planned.append(_open(point.observation_date, point.value, held.realtime_start))
            continue
        planned.append(
            FredVintage(
                observation_date=held.observation_date,
                value=held.value,
                realtime_start=held.realtime_start,
                realtime_end=fetched_on - timedelta(days=1),
            )
        )
        planned.append(_open(point.observation_date, point.value, fetched_on))
    return tuple(planned)


def _open(observed: date, value: Decimal, start: date) -> FredVintage:
    return FredVintage(
        observation_date=observed,
        value=value,
        realtime_start=start,
        realtime_end=OPEN_ENDED,
    )
