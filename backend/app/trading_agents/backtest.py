from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class WindowReturn:
    actual: Decimal
    entry_date: date
    exit_date: date


def window_return(
    closes: list[tuple[date, Decimal]],
    as_of: date,
    holding_days: int,
) -> WindowReturn | None:
    """用已经入库的收盘价计算区间收益。没有入场后的收盘价时返回空。"""
    if holding_days < 1:
        raise ValueError("持有天数必须为正")
    ordered = sorted(closes, key=lambda item: item[0])
    entry_rows = [item for item in ordered if item[0] <= as_of and item[1] > 0]
    if not entry_rows:
        return None
    entry_date, entry_price = entry_rows[-1]
    deadline = entry_date + timedelta(days=holding_days)
    later = [item for item in ordered if entry_date < item[0] <= deadline and item[1] > 0]
    if not later:
        return None
    exit_date, exit_price = later[-1]
    return WindowReturn((exit_price / entry_price) - Decimal(1), entry_date, exit_date)
