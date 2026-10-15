from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

MA_WINDOWS = (5, 10, 20, 60)

# 均线和收益率必须落在这两个刻度上再往外给：
# Decimal 的除法精度是 28 位，60 日均线除以 60 会得到 1295.7418333333333333333
# 这种尾巴，前端照实渲染就会把小数位撑爆。四舍五入到下面这两档。
MA_SCALE = Decimal("0.0001")
RETURN_SCALE = Decimal("0.000001")


def moving_average(closes: Sequence[Decimal], window: int) -> Decimal | None:
    if window < 1 or len(closes) < window:
        return None
    total = sum(closes[-window:], Decimal("0"))
    return (total / Decimal(window)).quantize(MA_SCALE, rounding=ROUND_HALF_UP)


def simple_return(previous: Decimal | None, close: Decimal) -> Decimal | None:
    if previous is None or previous == 0:
        return None
    ratio = (close - previous) / previous
    return ratio.quantize(RETURN_SCALE, rounding=ROUND_HALF_UP)


def series_without_gaps(dates: Sequence[str]) -> tuple[str, ...]:
    """只保留已有交易日，不把缺失日补成 0。"""

    return tuple(dates)
