"""定时生成每家公司的财务体检快照，写入 financial_metrics。

只读真实披露的事实（SEC / 东方财富），一个事实也没有就什么都不写：宁可没有，也不拿样例凑数。
所有数字都由后端按已披露事实计算，每个指标记录取数来源，算不出来的存 NULL 并写明原因。
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import Engine

from app.repositories.filings import FactRecord
from app.repositories.financial_metrics import FinancialMetricRepository
from app.repositories.instruments import InstrumentRepository
from app.services.company_facts import live_facts
from app.services.company_financials import (
    FLOW_PREFER,
    FactBook,
    period_kind,
    period_label,
    ratio,
    ratio_pct,
    year_ago,
)
from app.services.financial_metrics import gross_margin, year_over_year

CENT = Decimal("0.01")
PERIOD_TYPES = {"annual": "FY", "quarter": "Q", "ytd": "YTD", "instant": "LATEST"}
METRIC_CODES = (
    "revenue_yoy",
    "net_income_yoy",
    "gross_margin",
    "debt_ratio",
    "current_ratio",
    "cfo_to_net_income",
)


@dataclass(frozen=True)
class Computed:
    code: str
    value: Decimal | None
    unit: str
    reason: str | None
    inputs: tuple[FactRecord | None, ...]


def snapshot_company_metrics(engine: Engine, instrument_id_text: str, as_of: date) -> int:
    """为一家公司写入最新一期的指标快照，返回写入（含更新）的行数。"""

    instrument = InstrumentRepository(engine).get(UUID(instrument_id_text))
    if instrument is None:
        return 0
    facts = live_facts(engine, instrument, as_of)
    if not facts:
        return 0
    end, period_type, metrics = compute_metrics(FactBook(facts))
    # 算不出来的指标没有自己的取数来源，用整套事实中最晚的披露日当可见日期，避免时间旅行时提前露面。
    known_since = _published_at(tuple(facts))
    repository = FinancialMetricRepository(engine)
    for metric in metrics:
        repository.save(
            instrument_id=instrument.id,
            metric_code=metric.code,
            period_type=period_type,
            period_end=end,
            value=metric.value,
            unit=metric.unit,
            quality_flag="ok" if metric.value is not None else "unavailable",
            source_facts=_source_payload(metric),
            published_at=_published_at(metric.inputs) or known_since,
        )
    return len(metrics)


def compute_metrics(book: FactBook) -> tuple[date, str, list[Computed]]:
    ends = sorted({point.period_end for point in book.facts}, reverse=True)
    end = next((candidate for candidate in ends if book.pick("revenue", candidate)), ends[0])
    revenue = book.pick("revenue", end)
    period_type = PERIOD_TYPES[period_kind(revenue)] if revenue else "LATEST"
    net_income = book.like("netIncome", revenue) if revenue else book.pick("netIncome", end)
    cash_flow = book.pick("operatingCashFlow", end, FLOW_PREFER)
    metrics = [
        _growth("revenue_yoy", book, revenue),
        _growth("net_income_yoy", book, net_income),
        _gross_margin(book, revenue),
        _simple(
            "debt_ratio",
            book.ratio_at("debtRatio", end, ("instant",)),
            (book.pick("liabilities", end, ("instant",)), book.pick("assets", end, ("instant",))),
        ),
        _current_ratio(book, end),
        _cash_conversion(book, cash_flow),
    ]
    return end, period_type, metrics


def _simple(code: str, value: Decimal | None, inputs: tuple[FactRecord | None, ...]) -> Computed:
    reason = None if value is not None else "缺少分子或分母的已披露事实"
    return Computed(code, value, "%", reason, inputs)


def _growth(code: str, book: FactBook, current: FactRecord | None) -> Computed:
    if current is None:
        return Computed(code, None, "%", "缺少当期已披露事实", ())
    prior = year_ago(book.facts, current)
    if prior is None:
        return Computed(code, None, "%", "缺少去年同期同口径事实", (current,))
    result = year_over_year(current.value, prior.value)
    if result.value is None:
        return Computed(
            code, None, "%", result.display or "基期不为正，不计算增速", (current, prior)
        )
    value = (result.value * 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return Computed(code, value, "%", None, (current, prior))


def _gross_margin(book: FactBook, revenue: FactRecord | None) -> Computed:
    if revenue is None:
        return Computed("gross_margin", None, "%", "缺少营业收入", ())
    gross = book.like("grossProfit", revenue)
    direct = ratio_pct(gross, revenue)
    if direct is not None:
        return Computed("gross_margin", direct, "%", None, (gross, revenue))
    cost = book.like("cost", revenue)
    if cost is not None:
        derived = gross_margin(revenue.value, cost.value)
        if derived.value is not None:
            value = (derived.value * 100).quantize(CENT, rounding=ROUND_HALF_UP)
            return Computed("gross_margin", value, "%", None, (revenue, cost))
    return Computed("gross_margin", None, "%", "缺少毛利或营业成本", (revenue,))


def _current_ratio(book: FactBook, end: date) -> Computed:
    assets = book.pick("currentAssets", end, ("instant",))
    liabilities = book.pick("currentLiabilities", end, ("instant",))
    value = ratio(assets, liabilities)
    if value is None:
        return Computed("current_ratio", None, "x", "缺少流动资产或流动负债", (assets, liabilities))
    return Computed(
        "current_ratio",
        value.quantize(CENT, rounding=ROUND_HALF_UP),
        "x",
        None,
        (assets, liabilities),
    )


def _cash_conversion(book: FactBook, cash_flow: FactRecord | None) -> Computed:
    income = book.like("netIncome", cash_flow)
    if cash_flow is None or income is None:
        return Computed("cfo_to_net_income", None, "x", "缺少经营现金流或同口径净利润", ())
    if income.value <= 0:
        return Computed(
            "cfo_to_net_income", None, "x", "净利润不为正，现金含量没有意义", (cash_flow, income)
        )
    value = (cash_flow.value / income.value).quantize(CENT, rounding=ROUND_HALF_UP)
    return Computed("cfo_to_net_income", value, "x", None, (cash_flow, income))


def _source_payload(metric: Computed) -> dict[str, object]:
    return {
        "reason": metric.reason,
        "inputs": [
            {
                "concept": point.concept,
                "value": str(point.value),
                "unit": point.unit,
                "period_end": point.period_end.isoformat(),
                "period_label": period_label(point),
                "form": point.form,
                "filed": point.filed.isoformat(),
                "accession_no": point.accession_no,
                "source_url": point.source_url,
            }
            for point in metric.inputs
            if point is not None
        ],
    }


def _published_at(inputs: tuple[FactRecord | None, ...]) -> datetime | None:
    filed = [point.filed for point in inputs if point is not None]
    if not filed:
        return None
    return datetime.combine(max(filed), time.min, tzinfo=UTC)
