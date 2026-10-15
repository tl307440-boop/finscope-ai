from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class VintagePoint:
    observation_date: date
    value: Decimal
    unit: str
    realtime_start: date
    realtime_end: date
    revision_no: int


def pit_date(as_of: date | datetime) -> date:
    """日期参数直接作为 FRED 日历日。时间戳先换到纽约，再取日历日。"""

    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            raise ValueError("as_of 必须带时区")
        return as_of.astimezone(NEW_YORK).date()
    return as_of


def vintage_visible(*, realtime_start: date, realtime_end: date, pit: date) -> bool:
    """只知道发布日期，不知道时分。发布日当天不视为已经可见。"""

    return realtime_start < pit <= realtime_end


def published_at_from_vintage(realtime_start: date) -> datetime:
    midnight = datetime(
        realtime_start.year,
        realtime_start.month,
        realtime_start.day,
        tzinfo=NEW_YORK,
    )
    return midnight + timedelta(days=1)


def select_visible(rows: Sequence[VintagePoint], pit: date) -> list[VintagePoint]:
    chosen: dict[date, VintagePoint] = {}
    for row in rows:
        if not vintage_visible(
            realtime_start=row.realtime_start,
            realtime_end=row.realtime_end,
            pit=pit,
        ):
            continue
        current = chosen.get(row.observation_date)
        if current is None or row.realtime_start > current.realtime_start:
            chosen[row.observation_date] = row
    return [chosen[key] for key in sorted(chosen)]
