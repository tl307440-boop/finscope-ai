from datetime import date
from decimal import Decimal

from app.services.company_research import SourcedAmount, compare_periods


def _fact(period: str, value: str, accession: str, filed: str) -> SourcedAmount:
    return SourcedAmount(
        concept="us-gaap:Revenues",
        unit="USD",
        value=Decimal(value),
        period_end=date.fromisoformat(period),
        filed=date.fromisoformat(filed),
        form="10-K",
        accession_no=accession,
        source_url="https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
    )


def test_two_periods_keep_sources_and_do_not_invent_risks() -> None:
    result = compare_periods(
        [
            _fact("2022-12-31", "80", "0000320193-23-000001", "2023-02-01"),
            _fact("2023-12-31", "120", "0000320193-24-000099", "2024-06-01"),
        ],
        date(2023, 12, 31),
        date(2022, 12, 31),
    )
    assert result.earlier_period == date(2022, 12, 31)
    assert result.later_period == date(2023, 12, 31)
    metric = result.metrics[0]
    assert metric.earlier is not None
    assert metric.later is not None
    assert metric.earlier.accession_no == "0000320193-23-000001"
    assert metric.later.accession_no == "0000320193-24-000099"
    assert metric.change.value == Decimal("0.5")
    assert result.highlights == ()
    assert result.risks == ()
    assert any(
        rule.rule_id == "R006" and rule.status == "not_evaluated" for rule in result.unevaluated
    )
