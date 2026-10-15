from typing import Literal

CalendarStatus = Literal["open", "closed", "unknown"]


def calendar_status(is_open: bool | None) -> CalendarStatus:
    """没有日历记录就是未知，不用星期几代替交易所日历。"""
    if is_open is None:
        return "unknown"
    return "open" if is_open else "closed"
