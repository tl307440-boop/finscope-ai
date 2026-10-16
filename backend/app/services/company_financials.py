"""公司财务总览：按申报口径配对后计算同比、环比与比率。

只用 `published(filed) <= as_of` 的事实（由 FilingRepository.list_facts 保证）。
可比口径 = 同一概念、同一单位、期间长度相差不超过 20 天；同比找期末相隔 365 ± 14 天的同口径事实。
配不上就返回 None，界面显示「—」，不补数。
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.repositories.filings import FactRecord

PeriodKind = Literal["instant", "quarter", "ytd", "annual"]
Mode = Literal["quarter", "annual"]

CENT = Decimal("0.01")
SPARK_POINTS = 8
TREND_POINTS = 12
TABLE_COLUMNS = 5
FLOW_PREFER: tuple[PeriodKind, ...] = ("quarter", "ytd", "annual")


@dataclass(frozen=True)
class MetricDef:
    key: str
    label: str
    concepts: tuple[str, ...]


METRICS: dict[str, MetricDef] = {
    item.key: item
    for item in (
        MetricDef(
            "revenue",
            "营业收入",
            (
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
            ),
        ),
        MetricDef("cost", "营业成本", ("CostOfGoodsAndServicesSold", "CostOfRevenue")),
        MetricDef("grossProfit", "毛利", ("GrossProfit",)),
        MetricDef("operatingIncome", "营业利润", ("OperatingIncomeLoss",)),
        MetricDef("netIncome", "净利润", ("NetIncomeLoss",)),
        MetricDef("rnd", "研发费用", ("ResearchAndDevelopmentExpense",)),
        MetricDef("epsDiluted", "稀释 EPS", ("EarningsPerShareDiluted",)),
        MetricDef(
            "operatingCashFlow", "经营现金流", ("NetCashProvidedByUsedInOperatingActivities",)
        ),
        MetricDef("assets", "总资产", ("Assets",)),
        MetricDef("currentAssets", "流动资产", ("AssetsCurrent",)),
        MetricDef("liabilities", "总负债", ("Liabilities",)),
        MetricDef("currentLiabilities", "流动负债", ("LiabilitiesCurrent",)),
        MetricDef("equity", "股东权益", ("StockholdersEquity",)),
        MetricDef("cash", "现金及等价物", ("CashAndCashEquivalentsAtCarryingValue",)),
        MetricDef("receivables", "应收账款", ("AccountsReceivableNetCurrent",)),
        MetricDef("inventory", "存货", ("InventoryNet",)),
    )
}

TREND_METRICS = (
    "revenue",
    "netIncome",
    "grossProfit",
    "operatingIncome",
    "operatingCashFlow",
    "rnd",
    "epsDiluted",
)
TABLE_ROWS = (
    "revenue",
    "cost",
    "grossProfit",
    "grossMargin",
    "operatingIncome",
    "netIncome",
    "rnd",
    "epsDiluted",
    "operatingCashFlow",
    "assets",
    "currentAssets",
    "liabilities",
    "currentLiabilities",
    "debtRatio",
    "equity",
    "cash",
    "receivables",
    "inventory",
)
RATIOS: dict[str, tuple[str, str, str]] = {
    "grossMargin": ("毛利率", "grossProfit", "revenue"),
    "debtRatio": ("资产负债率", "liabilities", "assets"),
}


def short_name(concept: str) -> str:
    return concept.split(":", 1)[-1]


def span(fact: FactRecord) -> int | None:
    return None if fact.period_start is None else (fact.period_end - fact.period_start).days


def period_kind(fact: FactRecord) -> PeriodKind:
    days = span(fact)
    if days is None:
        return "instant"
    if days <= 100:
        return "quarter"
    return "annual" if days >= 340 else "ytd"


def period_label(fact: FactRecord) -> str:
    kind = period_kind(fact)
    if kind == "instant":
        return "期末时点"
    if kind == "quarter":
        return "单季"
    if kind == "annual":
        return "全年"
    return f"前 {round((span(fact) or 0) / 91)} 季累计"


def same_shape(left: FactRecord, right: FactRecord) -> bool:
    if left.concept != right.concept or left.unit != right.unit:
        return False
    left_span, right_span = span(left), span(right)
    if left_span is None or right_span is None:
        return left_span == right_span
    return abs(left_span - right_span) <= 20


def _latest(points: Iterable[FactRecord]) -> FactRecord | None:
    best: FactRecord | None = None
    for point in points:
        if best is None or point.filed > best.filed:
            best = point
    return best


def find_comparable(
    points: Sequence[FactRecord], ref: FactRecord, target_end: date, tolerance: int = 14
) -> FactRecord | None:
    return _latest(
        point
        for point in points
        if point is not ref
        and same_shape(point, ref)
        and abs((point.period_end - target_end).days) <= tolerance
    )


def year_ago(points: Sequence[FactRecord], ref: FactRecord) -> FactRecord | None:
    return find_comparable(points, ref, ref.period_end - timedelta(days=365))


def is_year_apart(earlier: date, later: date) -> bool:
    return abs((later - earlier).days - 365) <= 14


def pct_change(current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    """变化率（%），口径同 financial_metrics.year_over_year：基数不为正时不给百分比。"""
    if current is None or prior is None or prior <= 0:
        return None
    return ((current / prior - 1) * 100).quantize(CENT, rounding=ROUND_HALF_UP)


def ratio(numerator: FactRecord | None, denominator: FactRecord | None) -> Decimal | None:
    """未舍入的比值：分子分母必须同期末、同期间长度，分母不为正时不出数。"""
    if numerator is None or denominator is None or denominator.value <= 0:
        return None
    if numerator.period_end != denominator.period_end:
        return None
    left, right = span(numerator), span(denominator)
    if (left is None) != (right is None):
        return None
    if left is not None and right is not None and abs(left - right) > 20:
        return None
    return numerator.value / denominator.value


def ratio_pct(numerator: FactRecord | None, denominator: FactRecord | None) -> Decimal | None:
    """比率（%），口径同 ratio。"""
    value = ratio(numerator, denominator)
    return None if value is None else (value * 100).quantize(CENT, rounding=ROUND_HALF_UP)


def _difference(now: Decimal | None, prior: Decimal | None) -> Decimal | None:
    return None if now is None or prior is None else (now - prior).quantize(CENT)


def is_per_share(unit: str) -> bool:
    return unit.lower().endswith("/shares")


class FactBook:
    """一家公司在观察日可见的全部事实，以及按指标取数的方法。"""

    def __init__(self, facts: Sequence[FactRecord]) -> None:
        self.facts = list(facts)

    def metric_points(self, key: str) -> list[FactRecord]:
        for concept in METRICS[key].concepts:
            hits = [point for point in self.facts if short_name(point.concept) == concept]
            if hits:
                return hits
        return []

    def pick(
        self, key: str, end: date, prefer: Sequence[PeriodKind] = FLOW_PREFER
    ) -> FactRecord | None:
        for concept in METRICS[key].concepts:
            at_end = [
                point
                for point in self.facts
                if short_name(point.concept) == concept and point.period_end == end
            ]
            for kind in prefer:
                hit = _latest(point for point in at_end if period_kind(point) == kind)
                if hit is not None:
                    return hit
        return None

    def like(self, key: str, ref: FactRecord | None) -> FactRecord | None:
        """与 ref 同期末、同期间口径的另一个指标（比率的分子）。"""
        return None if ref is None else self.pick(key, ref.period_end, (period_kind(ref),))

    def series_like(self, ref: FactRecord, until: date | None = None) -> list[FactRecord]:
        by_end: dict[date, FactRecord] = {}
        for point in self.facts:
            if not same_shape(point, ref) or (until is not None and point.period_end > until):
                continue
            current = by_end.get(point.period_end)
            if current is None or point.filed > current.filed:
                by_end[point.period_end] = point
        return [by_end[end] for end in sorted(by_end)]

    def metric_series(self, key: str, kind: PeriodKind) -> list[FactRecord]:
        pool = [point for point in self.metric_points(key) if period_kind(point) == kind]
        if not pool:
            return []
        return FactBook(pool).series_like(pool[0])

    def ratio_at(self, key: str, end: date, kinds: Sequence[PeriodKind]) -> Decimal | None:
        _, numerator, denominator = RATIOS[key]
        return ratio_pct(self.pick(numerator, end, kinds), self.pick(denominator, end, kinds))

    def yoy(self, point: FactRecord | None) -> Decimal | None:
        if point is None:
            return None
        prior = year_ago(self.facts, point)
        return pct_change(point.value, None if prior is None else prior.value)


def _source(point: FactRecord | None) -> dict[str, object] | None:
    if point is None:
        return None
    return {
        "form": point.form,
        "filed": point.filed.isoformat(),
        "accession_no": point.accession_no,
        "source_url": point.source_url,
        "period_end": point.period_end.isoformat(),
        "period_label": period_label(point),
        "unit": point.unit,
    }


def _plain(value: Decimal) -> str:
    """去掉 XBRL 数值的尾零（109417000000.000000 → 109417000000），不改精度。"""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _value(point: FactRecord | None) -> Decimal | None:
    return None if point is None else point.value


class Overview:
    def __init__(self, book: FactBook, reference: date, compare: date | None) -> None:
        self.book = book
        self.reference = reference
        self.compare = compare

    def compared(self, current: FactRecord | None) -> FactRecord | None:
        if current is None:
            return None
        if self.compare is not None:
            return find_comparable(self.book.facts, current, self.compare)
        return year_ago(self.book.facts, current)

    def amount_tile(self, key: str, instant: bool) -> dict[str, object]:
        prefer: tuple[PeriodKind, ...] = ("instant",) if instant else FLOW_PREFER
        current = self.book.pick(key, self.reference, prefer)
        prior = self.compared(current)
        history = (
            []
            if current is None
            else self.book.series_like(current, self.reference)[-SPARK_POINTS:]
        )
        line = instant or (current is not None and is_per_share(current.unit))
        return {
            "key": key,
            "label": METRICS[key].label,
            "kind": "amount",
            "value": None if current is None else _plain(current.value),
            "unit": "" if current is None else current.unit,
            "delta": _text(pct_change(_value(current), _value(prior))),
            "delta_unit": "%",
            "spark": [_plain(point.value) for point in history],
            "spark_kind": "line" if line else "bar",
            "source": _source(current),
        }

    def ratio_tile(self, key: str, instant: bool, formula: str) -> dict[str, object]:
        label, numerator_key, denominator_key = RATIOS[key]
        prefer: tuple[PeriodKind, ...] = ("instant",) if instant else FLOW_PREFER
        denominator = self.book.pick(denominator_key, self.reference, prefer)
        now = ratio_pct(self.book.like(numerator_key, denominator), denominator)
        prior_denominator = self.compared(denominator)
        prior = ratio_pct(self.book.like(numerator_key, prior_denominator), prior_denominator)
        spark: list[str] = []
        if denominator is not None:
            for point in self.book.series_like(denominator, self.reference)[-SPARK_POINTS:]:
                ratio = ratio_pct(self.book.like(numerator_key, point), point)
                if ratio is not None:
                    spark.append(str(ratio))
        return {
            "key": key,
            "label": label,
            "kind": "ratio",
            "value": _text(now),
            "unit": "%",
            "delta": _text(_difference(now, prior)),
            "delta_unit": "pct",
            "spark": spark,
            "spark_kind": "line",
            "formula": formula,
            "source": _source(denominator),
        }

    def tiles(self) -> list[dict[str, object]]:
        return [
            self.amount_tile("revenue", False),
            self.amount_tile("netIncome", False),
            self.amount_tile("operatingCashFlow", False),
            self.ratio_tile("grossMargin", False, "毛利 ÷ 营业收入，同一期间口径"),
            self.amount_tile("epsDiluted", False),
            self.ratio_tile("debtRatio", True, "总负债 ÷ 总资产，期末时点"),
        ]

    def trend(self, mode: Mode) -> list[dict[str, object]]:
        kind: PeriodKind = "quarter" if mode == "quarter" else "annual"
        found: list[dict[str, object]] = []
        for key in TREND_METRICS:
            series = [
                point
                for point in self.book.metric_series(key, kind)
                if point.period_end <= self.reference
            ][-TREND_POINTS:]
            if len(series) < 2:
                continue
            found.append(
                {
                    "key": key,
                    "label": METRICS[key].label,
                    "unit": series[0].unit,
                    "points": [
                        {
                            "period_end": point.period_end.isoformat(),
                            "value": _plain(point.value),
                            "yoy": _text(self.book.yoy(point)),
                        }
                        for point in series
                    ],
                }
            )
        return found

    def table(self, mode: Mode) -> dict[str, object]:
        kind: PeriodKind = "quarter" if mode == "quarter" else "annual"
        cell_kinds: tuple[PeriodKind, ...] = (kind, "instant")
        ends: set[date] = set()
        for key in ("revenue", "netIncome", "grossProfit", "operatingIncome"):
            ends.update(
                point.period_end
                for point in self.book.metric_series(key, kind)
                if point.period_end <= self.reference
            )
        columns = sorted(ends, reverse=True)[:TABLE_COLUMNS]
        rows: list[dict[str, object]] = []
        if not columns:
            return {"columns": [], "rows": rows}
        head = columns[0]

        def quarter_before(point: FactRecord | None) -> FactRecord | None:
            if point is None or mode != "quarter":
                return None
            return find_comparable(self.book.facts, point, head - timedelta(days=91))

        for row in TABLE_ROWS:
            if row in RATIOS:
                label, _, denominator_key = RATIOS[row]
                cells = [self.book.ratio_at(row, end, cell_kinds) for end in columns]
                if all(cell is None for cell in cells):
                    continue
                base = self.book.pick(denominator_key, head, cell_kinds)
                prior = year_ago(self.book.facts, base) if base else None
                last = quarter_before(base)

                def ratio_on(point: FactRecord | None, key: str = row) -> Decimal | None:
                    return (
                        None
                        if point is None
                        else self.book.ratio_at(key, point.period_end, cell_kinds)
                    )

                rows.append(
                    {
                        "key": row,
                        "label": label,
                        "kind": "ratio",
                        "unit": "%",
                        "cells": [_text(cell) for cell in cells],
                        "yoy": _text(_difference(cells[0], ratio_on(prior))),
                        "qoq": _text(_difference(cells[0], ratio_on(last))),
                        "delta_unit": "pct",
                    }
                )
                continue
            points = [self.book.pick(row, end, cell_kinds) for end in columns]
            sample = next((point for point in points if point is not None), None)
            if sample is None:
                continue
            current = points[0]
            last = quarter_before(current)
            rows.append(
                {
                    "key": row,
                    "label": METRICS[row].label,
                    "kind": "amount",
                    "unit": sample.unit,
                    "cells": [None if point is None else _plain(point.value) for point in points],
                    "yoy": _text(self.book.yoy(current)),
                    "qoq": _text(pct_change(_value(current), _value(last))),
                    "delta_unit": "%",
                }
            )
        return {"columns": [end.isoformat() for end in columns], "rows": rows}

    def cash_vs_profit(self) -> list[dict[str, object]]:
        """年度经营现金流与净利润（同一财年期末），以及现金流 ÷ 净利润的倍数。"""
        cash = {p.period_end: p for p in self.book.metric_series("operatingCashFlow", "annual")}
        profit = {p.period_end: p for p in self.book.metric_series("netIncome", "annual")}
        ends = sorted(end for end in set(cash) | set(profit) if end <= self.reference)[-5:]
        rows: list[dict[str, object]] = []
        for end in ends:
            flow, income = cash.get(end), profit.get(end)
            either = flow or income
            multiple = None
            if flow is not None and income is not None and income.value > 0:
                multiple = (flow.value / income.value).quantize(CENT, rounding=ROUND_HALF_UP)
            rows.append(
                {
                    "period_end": end.isoformat(),
                    "unit": either.unit if either is not None else "",
                    "operating_cash_flow": None if flow is None else _plain(flow.value),
                    "net_income": None if income is None else _plain(income.value),
                    "multiple": _text(multiple),
                }
            )
        return rows

    def rule_observations(self) -> dict[str, str]:
        """规则 R001–R006 用到的读数，只陈述数字，结论仍以规则引擎为准。"""
        book, end = self.book, self.reference
        revenue = book.pick("revenue", end, ("quarter", "ytd", "annual"))
        prior_revenue = year_ago(book.facts, revenue) if revenue else None
        cash_flow = book.pick("operatingCashFlow", end, ("ytd", "annual", "quarter"))
        net_income = book.like("netIncome", cash_flow)
        revenue_yoy = book.yoy(revenue)
        receivables_yoy = book.yoy(book.pick("receivables", end, ("instant",)))
        inventory_yoy = book.yoy(book.pick("inventory", end, ("instant",)))
        margin_now = ratio_pct(book.like("grossProfit", revenue), revenue)
        margin_prior = ratio_pct(book.like("grossProfit", prior_revenue), prior_revenue)
        current_ratio = ratio(
            book.pick("currentAssets", end, ("instant",)),
            book.pick("currentLiabilities", end, ("instant",)),
        )
        found: dict[str, str] = {}
        if receivables_yoy is not None and revenue_yoy is not None:
            found["R001"] = (
                f"应收同比 {_signed(receivables_yoy)}%，收入同比 {_signed(revenue_yoy)}%"
            )
        if net_income is not None and cash_flow is not None and net_income.value > 0:
            multiple = (cash_flow.value / net_income.value).quantize(CENT, rounding=ROUND_HALF_UP)
            found["R002"] = f"经营现金流为净利润的 {multiple} 倍"
        if margin_now is not None and margin_prior is not None:
            found["R003"] = (
                f"毛利率 {margin_now}%，同比 {_signed(_difference(margin_now, margin_prior))}pct"
            )
        if inventory_yoy is not None and revenue_yoy is not None:
            found["R004"] = f"存货同比 {_signed(inventory_yoy)}%，收入同比 {_signed(revenue_yoy)}%"
        if current_ratio is not None:
            found["R005"] = f"流动比率 {current_ratio.quantize(CENT, rounding=ROUND_HALF_UP)}"
        if revenue_yoy is not None and net_income is not None and cash_flow is not None:
            sign = "为正" if cash_flow.value > 0 else "为零" if cash_flow.value == 0 else "为负"
            found["R006"] = f"收入同比 {_signed(revenue_yoy)}%，经营现金流{sign}"
        return found


def _signed(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"+{value}" if value > 0 else str(value)


def build_overview(
    facts: Sequence[FactRecord], reference: date | None, compare: date | None
) -> dict[str, object]:
    book = FactBook(facts)
    latest = max((point.period_end for point in book.facts), default=None)
    chosen = reference or latest
    if chosen is None:
        return {
            "reference_end": None,
            "compare_end": None,
            "delta_label": "同比",
            "tiles": [],
            "trend": {"quarter": [], "annual": []},
            "table": {
                "quarter": {"columns": [], "rows": []},
                "annual": {"columns": [], "rows": []},
            },
            "cash_vs_profit": [],
            "rule_observations": {},
        }
    paired = compare if compare is not None and compare != chosen else None
    if paired is not None and paired > chosen:
        chosen, paired = paired, chosen
    overview = Overview(book, chosen, paired)
    label = "同比" if paired is None or is_year_apart(paired, chosen) else f"较 {paired}"
    return {
        "reference_end": chosen.isoformat(),
        "compare_end": None if paired is None else paired.isoformat(),
        "delta_label": label,
        "tiles": overview.tiles(),
        "trend": {"quarter": overview.trend("quarter"), "annual": overview.trend("annual")},
        "table": {"quarter": overview.table("quarter"), "annual": overview.table("annual")},
        "cash_vs_profit": overview.cash_vs_profit(),
        "rule_observations": overview.rule_observations(),
    }
