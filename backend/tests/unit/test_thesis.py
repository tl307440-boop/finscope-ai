from decimal import Decimal

from app.services.thesis import fact_quote, thesis_status


def test_status_needs_both_sides_and_does_not_declare_a_winner() -> None:
    status, rationale = thesis_status(1, 1)
    assert status == "contested"
    assert "不是已证实的结论" in rationale
    assert thesis_status(1, 0)[0] == "has_support"
    assert thesis_status(0, 1)[0] == "has_challenge"
    assert thesis_status(0, 0)[0] == "open"


def test_fact_quote_uses_the_stored_number_and_accession() -> None:
    quote = fact_quote("us-gaap:Revenues", Decimal("120"), "USD", "0000320193-24-000099")
    assert quote == "us-gaap:Revenues 120 USD 申报号 0000320193-24-000099"
    assert "999" not in quote
