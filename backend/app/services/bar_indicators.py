from collections.abc import Sequence
from decimal import Decimal

MA_WINDOWS = (5, 10, 20, 60)


def moving_average(closes: Sequence[Decimal], window: int) -> Decimal | None:
    if window < 1 or len(closes) < window:
        return None
    total = sum(closes[-window:], Decimal("0"))
    return total / Decimal(window)


def simple_return(previous: Decimal | None, close: Decimal) -> Decimal | None:
    if previous is None or previous == 0:
        return None
    return (close - previous) / previous


def series_without_gaps(dates: Sequence[str]) -> tuple[str, ...]:
    """只保留已有交易日，不把缺失日补成 0。"""

    return tuple(dates)
