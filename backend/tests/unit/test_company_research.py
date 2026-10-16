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


def _flow(concept: str, start: str, end: str, value: str, filed: str) -> SourcedAmount:
    return SourcedAmount(
        concept=concept,
        unit="USD",
        value=Decimal(value),
        period_end=date.fromisoformat(end),
        filed=date.fromisoformat(filed),
        form="10-Q",
        accession_no=f"acc-{end}-{start}",
        source_url="https://www.sec.gov/",
        period_start=date.fromisoformat(start),
    )


def test_change_pairs_same_period_length_and_refuses_quarter_vs_ytd() -> None:
    result = compare_periods(
        [
            _flow("us-gaap:NetIncomeLoss", "2024-12-29", "2025-03-29", "100", "2025-05-01"),
            _flow("us-gaap:NetIncomeLoss", "2025-12-28", "2026-03-28", "130", "2026-05-01"),
            _flow("us-gaap:NetIncomeLoss", "2025-09-28", "2026-03-28", "260", "2026-05-01"),
            _flow("us-gaap:GrossProfit", "2024-12-29", "2025-03-29", "50", "2025-05-01"),
            _flow("us-gaap:GrossProfit", "2025-09-28", "2026-03-28", "120", "2026-05-01"),
        ],
        date(2025, 3, 29),
        date(2026, 3, 28),
    )
    by_concept = {metric.concept: metric for metric in result.metrics}
    income = by_concept["us-gaap:NetIncomeLoss"]
    assert income.later is not None and income.later.value == Decimal("130")
    assert income.change.value == Decimal("0.3")
    assert by_concept["us-gaap:GrossProfit"].change.reason == "incomparable_period"
