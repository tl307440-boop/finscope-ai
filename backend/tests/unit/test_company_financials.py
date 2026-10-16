from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.repositories.filings import FactRecord
from app.services.company_financials import (
    FactBook,
    build_overview,
    pct_change,
    period_kind,
    ratio_pct,
    year_ago,
)
from app.services.company_research import compare_periods
from app.services.filing_report import _sourced, _used_facts


def fact(
    concept: str,
    value: str,
    end: date,
    start: date | None,
    *,
    filed: date | None = None,
    unit: str = "USD",
) -> FactRecord:
    return FactRecord(
        id=uuid4(),
        concept=f"us-gaap:{concept}",
        unit=unit,
        value=Decimal(value),
        period_start=start,
        period_end=end,
        form="10-Q",
        fiscal_year=end.year,
        fiscal_period="Q",
        frame=None,
        filed=filed or date(end.year, end.month, 28),
        accession_no=f"0000000000-{end.year % 100:02d}-{end.month:06d}",
        source_url="https://www.sec.gov/Archives/edgar/data/1/x/",
        filing_status="edgar",
    )


Q_2025 = (date(2025, 3, 29), date(2024, 12, 29))
Q_2026 = (date(2026, 3, 28), date(2025, 12, 28))


def facts() -> list[FactRecord]:
    return [
        fact("Revenues", "100", *Q_2025),
        fact("Revenues", "120", *Q_2026),
        fact("GrossProfit", "40", *Q_2025),
        fact("GrossProfit", "54", *Q_2026),
        fact("Revenues", "430", date(2025, 9, 27), date(2024, 9, 29)),
        fact("Liabilities", "60", date(2026, 3, 28), None),
        fact("Assets", "200", date(2026, 3, 28), None),
        fact("Liabilities", "50", date(2025, 3, 29), None),
        fact("Assets", "200", date(2025, 3, 29), None),
    ]


def test_period_kinds_follow_span() -> None:
    book = FactBook(facts())
    kinds = {period_kind(point) for point in book.facts}
    assert kinds == {"quarter", "annual", "instant"}


def test_year_ago_tolerates_52_53_week_calendar() -> None:
    book = FactBook(facts())
    current = book.pick("revenue", date(2026, 3, 28))
    assert current is not None
    prior = year_ago(book.facts, current)
    assert prior is not None and prior.value == Decimal("100")
    assert book.yoy(current) == Decimal("20.00")


def test_ratio_requires_same_period_shape() -> None:
    book = FactBook(facts())
    revenue = book.pick("revenue", date(2026, 3, 28))
    annual = book.pick("revenue", date(2025, 9, 27), ("annual",))
    assert ratio_pct(book.like("grossProfit", revenue), revenue) == Decimal("45.00")
    assert ratio_pct(book.pick("grossProfit", date(2026, 3, 28)), annual) is None


def test_non_positive_or_missing_base_gives_no_number() -> None:
    assert pct_change(Decimal("5"), Decimal("0")) is None
    assert pct_change(None, Decimal("5")) is None
    assert pct_change(Decimal("-5"), Decimal("-10")) is None
    assert pct_change(Decimal("12"), Decimal("10")) == Decimal("20.00")


def test_overview_tiles_table_and_rules() -> None:
    data = build_overview(facts(), None, None)
    assert data["reference_end"] == "2026-03-28"
    assert data["delta_label"] == "同比"
    tiles = {tile["key"]: tile for tile in data["tiles"]}  # type: ignore[attr-defined]
    assert tiles["revenue"]["value"] == "120"
    assert tiles["revenue"]["delta"] == "20.00"
    assert tiles["grossMargin"]["value"] == "45.00"
    assert tiles["grossMargin"]["delta"] == "5.00"
    assert tiles["grossMargin"]["delta_unit"] == "pct"
    assert tiles["debtRatio"]["value"] == "30.00"
    assert tiles["netIncome"]["value"] is None
    quarter = data["table"]["quarter"]  # type: ignore[index]
    assert quarter["columns"] == ["2026-03-28", "2025-03-29"]
    rows = {row["key"]: row for row in quarter["rows"]}
    assert rows["revenue"]["cells"] == ["120", "100"]
    assert rows["grossMargin"]["yoy"] == "5.00"
    trend = {item["key"]: item for item in data["trend"]["quarter"]}  # type: ignore[index]
    assert [point["yoy"] for point in trend["revenue"]["points"]] == [None, "20.00"]
    observations = data["rule_observations"]
    assert observations == {"R003": "毛利率 45.00%，同比 +5.00pct"}  # type: ignore[comparison-overlap]


def test_explicit_compare_period_changes_label() -> None:
    data = build_overview(facts(), date(2026, 3, 28), date(2025, 9, 27))
    assert data["compare_end"] == "2025-09-27"
    assert data["delta_label"] == "较 2025-09-27"


def test_negative_denominator_or_loss_gives_no_ratio() -> None:
    end, start = Q_2026
    revenue = fact("Revenues", "-10", end, start)
    gross = fact("GrossProfit", "5", end, start)
    assert ratio_pct(gross, revenue) is None

    ytd = date(2025, 9, 28)
    loss = [
        *facts(),
        fact("NetCashProvidedByUsedInOperatingActivities", "500", end, ytd),
        fact("NetIncomeLoss", "-200", end, ytd),
        fact("AssetsCurrent", "123.496", end, None),
        fact("LiabilitiesCurrent", "100", end, None),
    ]
    observations = build_overview(loss, None, None)["rule_observations"]
    assert "R002" not in observations  # type: ignore[operator]
    assert observations["R005"] == "流动比率 1.23"  # type: ignore[index]


def test_report_cites_the_quarter_fact_not_the_ytd_one_in_the_same_filing() -> None:
    end, start = Q_2026
    quarter = fact("NetIncomeLoss", "150", end, start)
    ytd = fact("NetIncomeLoss", "260", end, date(2025, 9, 28))
    prior = fact("NetIncomeLoss", "110", *Q_2025)
    pool = [quarter, ytd, prior]
    comparison = compare_periods([_sourced(item) for item in pool], Q_2025[0], end)
    used = _used_facts(pool, comparison.metrics)
    assert {item.id for item in used} == {quarter.id, prior.id}


def test_no_facts_is_empty_not_zero() -> None:
    data = build_overview([], None, None)
    assert data["reference_end"] is None
    assert data["tiles"] == []
