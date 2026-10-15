from datetime import date, timedelta
from decimal import Decimal

from app.services.market_temperature import PriceBar, market_temperature, sector_strength


def _bars(
    start: Decimal, count: int, step: Decimal = Decimal("1"), amount: Decimal = Decimal("10")
) -> list[PriceBar]:
    day = date(2024, 1, 2)
    rows: list[PriceBar] = []
    price = start
    for _ in range(count):
        rows.append(PriceBar(day, price, amount))
        price += step
        day += timedelta(days=1)
    return rows


def test_temperature_is_unknown_when_the_sample_is_short() -> None:
    result = market_temperature(_bars(Decimal("10"), 10), {})
    assert result.label == "无法判断"
    assert result.score is None


def test_temperature_uses_only_the_supplied_bars() -> None:
    index = _bars(Decimal("100"), 60, Decimal("1"), Decimal("10"))
    members = {f"S{i}": _bars(Decimal("20"), 60, Decimal("0.2"), Decimal("10")) for i in range(5)}
    result = market_temperature(index, members)
    assert result.status == "ok"
    assert result.label in {"偏弱", "中性", "偏强"}
    weights = {item.code: item.weight for item in result.factors}
    assert weights["volatility"] == Decimal("0.15")
    assert "不是收益预测" in result.notice


def test_high_volatility_is_not_scored_as_strength() -> None:
    day = date(2024, 1, 2)
    mixed: list[PriceBar] = []
    price = Decimal("100")
    for index in range(60):
        if index >= 40 and index % 2 == 0:
            price += Decimal("30")
        elif index >= 40:
            price -= Decimal("25")
        else:
            price += Decimal("0.1")
        mixed.append(PriceBar(day, price, Decimal("10")))
        day += timedelta(days=1)
    members = {f"S{i}": list(mixed) for i in range(5)}
    result = market_temperature(mixed, members)
    vol = next(item for item in result.factors if item.code == "volatility")
    assert vol.score == Decimal("30")
    assert "风险" in vol.note


def test_sector_states_survivor_bias_and_refuses_a_tiny_pool() -> None:
    index = _bars(Decimal("10"), 5)
    small = sector_strength(index, {"A": _bars(Decimal("10"), 5)})
    assert small.label == "无法判断"
    members = {name: _bars(Decimal("10"), 5, Decimal("1")) for name in ("A", "B", "C")}
    ready = sector_strength(index, members)
    assert ready.status == "ok"
    assert "幸存者偏差" in ready.notice
