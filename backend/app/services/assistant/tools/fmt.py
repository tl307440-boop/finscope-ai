"""工具结果里数字的统一写法：Decimal 计算，字符串输出，不经过浮点。"""

from decimal import ROUND_HALF_UP, Decimal

from app.repositories.macro import decimal_text

YI = Decimal(100_000_000)
_CENT = Decimal("0.01")


def plain(value: Decimal | None) -> str | None:
    """去掉末尾多余的 0；没有值就是 None，不补 0。"""

    return None if value is None else decimal_text(value)


def pct(value: Decimal | None) -> str | None:
    """百分数保留两位，四舍五入。没有值就是 None，不补 0。"""

    if value is None:
        return None
    return decimal_text(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def yi(value: Decimal | None) -> str | None:
    """元 → 亿元，四舍五入到两位小数。"""

    if value is None:
        return None
    return decimal_text((value / YI).quantize(_CENT, rounding=ROUND_HALF_UP))


def count(value: int | None) -> str | None:
    return None if value is None else str(value)
