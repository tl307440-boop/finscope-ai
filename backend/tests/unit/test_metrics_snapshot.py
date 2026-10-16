from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.repositories.filings import FactRecord
from app.services.company_financials import FactBook
from app.services.metrics_snapshot import METRIC_CODES, compute_metrics

SOURCE = "https://example.test/filing"


def _fact(
    concept: str,
    value: str,
    end: date,
    *,
    start: date | None = None,
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
        form="10-K",
        fiscal_year=end.year,
        fiscal_period="FY",
        frame=None,
        filed=filed or end,
        accession_no="0000000000-26-000001",
        source_url=SOURCE,
        filing_status="edgar",
    )


NOW_START, NOW_END = date(2025, 1, 1), date(2025, 12, 31)
OLD_START, OLD_END = date(2024, 1, 1), date(2024, 12, 31)


def _book(net_income_now: str = "20") -> FactBook:
    return FactBook(
        [
            _fact("Revenues", "120", NOW_END, start=NOW_START),
            _fact("Revenues", "100", OLD_END, start=OLD_START),
            _fact("CostOfRevenue", "72", NOW_END, start=NOW_START),
            _fact("NetIncomeLoss", net_income_now, NOW_END, start=NOW_START),
            _fact("NetIncomeLoss", "10", OLD_END, start=OLD_START),
            _fact("NetCashProvidedByUsedInOperatingActivities", "30", NOW_END, start=NOW_START),
            _fact("Liabilities", "40", NOW_END),
            _fact("Assets", "100", NOW_END),
            _fact("AssetsCurrent", "60", NOW_END),
            _fact("LiabilitiesCurrent", "30", NOW_END),
        ]
    )


def _by_code(book: FactBook) -> dict[str, tuple[Decimal | None, str | None]]:
    _end, _period, metrics = compute_metrics(book)
    return {item.code: (item.value, item.reason) for item in metrics}


def test_all_codes_computed_from_disclosed_facts() -> None:
    end, period_type, metrics = compute_metrics(_book())
    values = {item.code: item.value for item in metrics}
    assert end == NOW_END
    assert period_type == "FY"
    assert tuple(values) == METRIC_CODES
    assert values["revenue_yoy"] == Decimal("20.00")
    assert values["net_income_yoy"] == Decimal("100.00")
    # 没有毛利科目时，用营业收入减营业成本推出来：(120-72)/120
    assert values["gross_margin"] == Decimal("40.00")
    assert values["debt_ratio"] == Decimal("40.00")
    assert values["current_ratio"] == Decimal("2.00")
    assert values["cfo_to_net_income"] == Decimal("1.50")


def test_non_positive_net_income_is_unavailable_with_reason() -> None:
    result = _by_code(_book(net_income_now="-5"))
    assert result["cfo_to_net_income"][0] is None
    assert result["cfo_to_net_income"][1] is not None


def test_missing_prior_year_gives_null_not_a_made_up_growth() -> None:
    book = FactBook([_fact("Revenues", "120", NOW_END, start=NOW_START)])
    result = _by_code(book)
    assert result["revenue_yoy"][0] is None
    assert "去年同期" in (result["revenue_yoy"][1] or "")
    assert result["debt_ratio"][0] is None
    assert result["current_ratio"][0] is None


def test_loss_to_profit_growth_is_not_reported_as_a_percentage() -> None:
    book = FactBook(
        [
            _fact("NetIncomeLoss", "20", NOW_END, start=NOW_START),
            _fact("NetIncomeLoss", "-10", OLD_END, start=OLD_START),
            _fact("Revenues", "120", NOW_END, start=NOW_START),
        ]
    )
    result = _by_code(book)
    assert result["net_income_yoy"][0] is None
    assert result["net_income_yoy"][1]
