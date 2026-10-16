from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.providers.sec_edgar import ParsedFact
from app.repositories.filings import FactRecord
from app.services.company_financials import FactBook
from app.services.data_sync import wanted_sec_fact
from app.services.ifrs import normalize_facts, reporting_currency
from app.services.metrics_snapshot import compute_metrics

START, END = date(2025, 1, 1), date(2025, 12, 31)
PRIOR_START, PRIOR_END = date(2024, 1, 1), date(2024, 12, 31)


def _fact(
    concept: str,
    value: str,
    end: date,
    *,
    start: date | None = None,
    unit: str = "TWD",
    form: str = "20-F",
    filed: date | None = None,
) -> FactRecord:
    return FactRecord(
        id=uuid4(),
        concept=concept,
        unit=unit,
        value=Decimal(value),
        period_start=start,
        period_end=end,
        form=form,
        fiscal_year=end.year,
        fiscal_period="FY",
        frame=None,
        filed=filed or date(end.year + 1, 4, 1),
        accession_no="0001-26-000001",
        source_url="https://example.test",
        filing_status="edgar",
    )


def test_ifrs_concepts_are_mapped_and_wanted() -> None:
    parsed = ParsedFact(
        cik="0001046179",
        entity_name="TSMC",
        concept="ifrs-full:Revenue",
        unit="TWD",
        value=Decimal("1"),
        period_start=START,
        period_end=END,
        form="20-F",
        fiscal_year=2025,
        fiscal_period="FY",
        frame=None,
        filed=date(2026, 4, 1),
        accession_no="a",
        source_url="u",
    )
    assert wanted_sec_fact(parsed)
    assert not wanted_sec_fact(
        ParsedFact(**{**parsed.__dict__, "concept": "ifrs-full:SomethingObscure"})
    )
    assert not wanted_sec_fact(ParsedFact(**{**parsed.__dict__, "form": "8-K"}))


def test_foreign_issuer_metrics_come_out_in_reporting_currency() -> None:
    facts = normalize_facts(
        [
            _fact("ifrs-full:Revenue", "120", END, start=START),
            _fact("ifrs-full:Revenue", "100", PRIOR_END, start=PRIOR_START),
            _fact("ifrs-full:CostOfSales", "72", END, start=START),
            _fact("ifrs-full:ProfitLoss", "25", END, start=START),
            _fact("ifrs-full:ProfitLossAttributableToOwnersOfParent", "20", END, start=START),
            _fact(
                "ifrs-full:ProfitLossAttributableToOwnersOfParent",
                "10",
                PRIOR_END,
                start=PRIOR_START,
            ),
            _fact("ifrs-full:Assets", "100", END),
            _fact("ifrs-full:Liabilities", "40", END),
            # 同一事实的美元便利换算：不能和新台币混着算
            _fact("ifrs-full:Revenue", "4", END, start=START, unit="USD"),
        ]
    )
    concepts = {fact.concept for fact in facts}
    assert "us-gaap:Revenues" in concepts
    assert not any(fact.concept.startswith("ifrs-full:") for fact in facts)
    assert {fact.unit for fact in facts} == {"TWD"}
    net_income = [fact for fact in facts if fact.concept == "us-gaap:NetIncomeLoss"]
    assert [fact.value for fact in net_income if fact.period_end == END] == [Decimal("20")]

    _end, period_type, metrics = compute_metrics(FactBook(facts))
    values = {item.code: item.value for item in metrics}
    assert period_type == "FY"
    assert values["revenue_yoy"] == Decimal("20.00")
    assert values["net_income_yoy"] == Decimal("100.00")
    assert values["gross_margin"] == Decimal("40.00")
    assert values["debt_ratio"] == Decimal("40.00")


def test_native_us_gaap_facts_pass_through_unchanged() -> None:
    facts = [
        _fact("us-gaap:Revenues", "120", END, start=START, unit="USD", form="10-K"),
        _fact("us-gaap:EarningsPerShareDiluted", "2.5", END, start=START, unit="USD/shares"),
    ]
    assert normalize_facts(facts) == facts
    assert reporting_currency(facts) == "USD"


def test_per_share_units_in_other_currencies_follow_the_reporting_currency() -> None:
    facts = normalize_facts(
        [
            _fact("ifrs-full:Revenue", "100", END, start=START, unit="TWD"),
            _fact(
                "ifrs-full:DilutedEarningsLossPerShare", "9", END, start=START, unit="TWD/shares"
            ),
            _fact(
                "ifrs-full:DilutedEarningsLossPerShare", "0.3", END, start=START, unit="USD/shares"
            ),
        ]
    )
    eps = [fact for fact in facts if fact.concept == "us-gaap:EarningsPerShareDiluted"]
    assert [(fact.unit, fact.value) for fact in eps] == [("TWD/shares", Decimal("9"))]
