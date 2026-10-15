from decimal import Decimal

from app.services.bar_indicators import moving_average, series_without_gaps, simple_return


def test_ma_is_rounded_so_the_serialized_text_has_no_tail() -> None:
    """60 日均线除以 60 是循环小数；对外文本不能出现 1295.7418333333333333333 这种尾巴。"""

    closes = [Decimal("1295.7418333333333333333")] * 60
    average = moving_average(closes, 60)
    assert average == Decimal("1295.7418")
    assert format(average, "f") == "1295.7418"


def test_return_is_rounded_to_six_places() -> None:
    ratio = simple_return(Decimal("3"), Decimal("7"))
    assert ratio == Decimal("1.333333")
    assert format(ratio, "f") == "1.333333"


def test_ma20_is_null_until_twenty_closes_exist() -> None:
    closes = [Decimal(str(index)) for index in range(1, 20)]
    assert moving_average(closes, 20) is None
    closes.append(Decimal("20"))
    assert moving_average(closes, 20) == Decimal("10.5")


def test_ma_uses_only_the_latest_window() -> None:
    closes = [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("10")]
    assert moving_average(closes, 5) == Decimal("4")


def test_first_return_is_null_and_later_return_uses_previous_close() -> None:
    assert simple_return(None, Decimal("10")) is None
    assert simple_return(Decimal("0"), Decimal("10")) is None
    assert simple_return(Decimal("10"), Decimal("11")) == Decimal("0.1")


def test_missing_day_is_not_filled() -> None:
    assert series_without_gaps(("2024-01-05", "2024-01-08")) == ("2024-01-05", "2024-01-08")
