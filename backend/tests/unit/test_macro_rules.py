from datetime import date
from decimal import Decimal

from app.services.macro_rules import MacroPoint, assess_macro


def test_growth_uses_gdp_direction_and_does_not_add_payrolls() -> None:
    points = [
        MacroPoint("GDP", date(2020, 1, 1), Decimal("100"), date(2020, 4, 1)),
        MacroPoint("GDP", date(2020, 4, 1), Decimal("110"), date(2020, 7, 1)),
        MacroPoint("PAYEMS", date(2020, 1, 1), Decimal("1000"), date(2020, 2, 1)),
        MacroPoint("PAYEMS", date(2020, 2, 1), Decimal("900"), date(2020, 3, 1)),
        MacroPoint("CPIAUCSL", date(2020, 1, 1), Decimal("100"), date(2020, 2, 1)),
        MacroPoint("CPIAUCSL", date(2020, 2, 1), Decimal("101"), date(2020, 3, 1)),
        MacroPoint("FEDFUNDS", date(2020, 1, 1), Decimal("2"), date(2020, 2, 1)),
        MacroPoint("FEDFUNDS", date(2020, 2, 1), Decimal("1"), date(2020, 3, 1)),
    ]
    result = assess_macro(points)
    assert result.growth.value == 70
    assert result.inflation.value == 70
    assert result.liquidity.value == 70
    assert "PAYEMS" not in " ".join(result.growth.evidence)
    assert "1000" not in " ".join(result.growth.evidence)
    assert result.regime_code == "up-up-easy"
    assert "不是经济定论" in result.notice


def test_missing_series_does_not_invent_a_score() -> None:
    result = assess_macro([MacroPoint("GDP", date(2020, 1, 1), Decimal("100"), date(2020, 4, 1))])
    assert result.growth.value is None
    assert result.growth.reason == "insufficient_data"
    assert result.inflation.reason == "insufficient_data"
    assert result.regime_code == "insufficient_data"


def test_later_revision_is_only_scored_when_it_is_passed_in() -> None:
    early = [
        MacroPoint("GDP", date(2020, 1, 1), Decimal("100"), date(2020, 4, 1)),
        MacroPoint("GDP", date(2020, 4, 1), Decimal("90"), date(2020, 7, 1)),
    ]
    assert assess_macro(early).growth.value == 30
