from datetime import date, datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_pit(as_of: date | datetime) -> date:
    """日期参数就是交易日历日。时间戳先换到上海，再取日历日。"""

    if isinstance(as_of, datetime):
        if as_of.tzinfo is None:
            raise ValueError("as_of 必须带时区")
        return as_of.astimezone(SHANGHAI).date()
    return as_of


def bar_visible(trade_date: date, pit: date) -> bool:
    return trade_date <= pit
