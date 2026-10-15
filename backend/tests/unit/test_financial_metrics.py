from decimal import Decimal

from app.services.financial_metrics import (
    ReportedAmount,
    current_ratio,
    debt_ratio,
    evaluate_rules,
    gross_margin,
    pb_ratio,
    pe_ttm,
    single_quarter,
    ttm_from_single_quarters,
    year_over_year,
)


def _amount(value: str, version: str = "v1") -> ReportedAmount:
    return ReportedAmount(value=Decimal(value), unit="CNY", version=version)


def test_ytd_converts_each_quarter_and_stops_when_prior_is_missing() -> None:
    quarters = single_quarter(
        "income",
        q1=_amount("80"),
        q2=_amount("200"),
        q3=None,
        fy=_amount("500"),
    )
    assert quarters["Q1"].value == Decimal("80")
    assert quarters["Q2"].value == Decimal("120")
    assert quarters["Q3"].value is None
    assert quarters["Q3"].reason == "insufficient_data"
    assert quarters["Q4"].value is None


def test_q4_is_full_year_minus_third_quarter_ytd() -> None:
    quarters = single_quarter(
        "cashflow",
        q1=_amount("10"),
        q2=_amount("30"),
        q3=_amount("60"),
        fy=_amount("100"),
    )
    assert quarters["Q3"].value == Decimal("30")
    assert quarters["Q4"].value == Decimal("40")


def test_revision_mismatch_does_not_subtract() -> None:
    result = single_quarter(
        "income",
        q1=_amount("80", version="original"),
        q2=_amount("200", version="restated"),
    )
    assert result["Q2"].value is None
    assert result["Q2"].reason == "revision_mismatch"


def test_balance_sheet_is_not_differenced() -> None:
    result = single_quarter("balance", q1=_amount("400"), q2=_amount("500"))
    assert result["Q2"].value == Decimal("500")
    assert result["Q2"].reason == "balance_is_point_in_time"
    assert result["Q2"].value != Decimal("100")


def test_ttm_sums_four_single_quarters_and_rejects_a_gap() -> None:
    total = ttm_from_single_quarters((Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")))
    assert total.value == Decimal("100")
    missing = ttm_from_single_quarters((Decimal("10"), None, Decimal("30"), Decimal("40")))
    assert missing.value is None
    assert missing.reason == "ttm_requires_four_single_quarters"


def test_yoy_handles_positive_zero_and_negative_priors() -> None:
    assert year_over_year(Decimal("115"), Decimal("100")).value == Decimal("0.15")
    zero = year_over_year(Decimal("10"), Decimal("0"))
    assert zero.value is None
    assert zero.reason == "prior_not_positive"
    turned = year_over_year(Decimal("5"), Decimal("-2"))
    assert turned.display == "由负转正"
    assert turned.value is None
    widened = year_over_year(Decimal("-8"), Decimal("-3"))
    assert widened.display == "亏损扩大"


def test_margins_and_ratios_reject_invalid_denominators() -> None:
    assert gross_margin(Decimal("0"), Decimal("1")).reason == "revenue_not_positive"
    assert gross_margin(Decimal("100"), Decimal("40")).value == Decimal("0.6")
    assert debt_ratio(Decimal("40"), Decimal("0")).reason == "assets_not_positive"
    assert debt_ratio(Decimal("40"), Decimal("100")).value == Decimal("0.4")
    assert current_ratio(None, Decimal("10")).reason == "insufficient_data"
    assert current_ratio(Decimal("50"), Decimal("0")).reason == "insufficient_data"
    assert current_ratio(Decimal("50"), Decimal("100")).value == Decimal("0.5")


def test_loss_making_pe_and_negative_equity_are_not_negative_multiples() -> None:
    pe = pe_ttm(Decimal("100"), Decimal("-5"))
    assert pe.value is None
    assert pe.display == "N/A"
    pb = pb_ratio(Decimal("100"), Decimal("0"))
    assert pb.display == "N/A"
    assert pe_ttm(Decimal("100"), Decimal("5")).value == Decimal("20")


def test_rules_do_not_treat_missing_data_as_safe() -> None:
    rules = evaluate_rules(
        revenue_yoy=year_over_year(Decimal("130"), Decimal("100")),
        receivables_yoy=year_over_year(Decimal("180"), Decimal("100")),
        inventory_yoy=None,
        net_income_ttm=Decimal("40"),
        cfo_ttm=Decimal("10"),
        gross_margin_now=Decimal("0.30"),
        gross_margin_prior=Decimal("0.40"),
        margins_comparable=True,
        current_ratio_value=Decimal("0.8"),
        short_debt_yoy=Decimal("0.3"),
    )
    by_id = {rule.rule_id: rule for rule in rules}
    assert by_id["R001"].status == "triggered"
    assert by_id["R002"].status == "triggered"
    assert "造假" in by_id["R002"].caveat
    assert by_id["R003"].status == "triggered"
    assert by_id["R004"].status == "not_evaluated"
    assert by_id["R005"].status == "triggered"
    assert by_id["R006"].status == "clear"


def test_positive_signal_is_not_a_buy_call() -> None:
    rules = evaluate_rules(
        revenue_yoy=year_over_year(Decimal("120"), Decimal("100")),
        receivables_yoy=year_over_year(Decimal("110"), Decimal("100")),
        inventory_yoy=year_over_year(Decimal("110"), Decimal("100")),
        net_income_ttm=Decimal("40"),
        cfo_ttm=Decimal("30"),
        gross_margin_now=Decimal("0.40"),
        gross_margin_prior=Decimal("0.41"),
        margins_comparable=True,
        current_ratio_value=Decimal("1.5"),
        short_debt_yoy=Decimal("0"),
    )
    positive = next(rule for rule in rules if rule.rule_id == "R006")
    assert positive.status == "triggered"
    assert positive.severity == "info"
    assert "不是买入信号" in positive.caveat
    incomparable = evaluate_rules(
        revenue_yoy=year_over_year(Decimal("120"), Decimal("100")),
        receivables_yoy=year_over_year(Decimal("110"), Decimal("100")),
        inventory_yoy=year_over_year(Decimal("110"), Decimal("100")),
        net_income_ttm=Decimal("40"),
        cfo_ttm=Decimal("30"),
        gross_margin_now=Decimal("0.40"),
        gross_margin_prior=Decimal("0.41"),
        margins_comparable=False,
        current_ratio_value=Decimal("1.5"),
        short_debt_yoy=Decimal("0"),
    )
    assert next(rule for rule in incomparable if rule.rule_id == "R003").status == "not_evaluated"
