from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

METHOD_VERSION = "financial-metrics-v1"
StatementKind = Literal["income", "cashflow", "balance"]
Quarter = Literal["Q1", "Q2", "Q3", "Q4"]


@dataclass(frozen=True)
class ReportedAmount:
    value: Decimal
    unit: str
    version: str


@dataclass(frozen=True)
class MetricResult:
    value: Decimal | None
    reason: str | None
    display: str | None
    method_version: str = METHOD_VERSION
    input_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    status: Literal["triggered", "clear", "not_evaluated"]
    severity: Literal["info", "watch", "high"] | None
    rationale: str
    caveat: str


def single_quarter(
    kind: StatementKind,
    *,
    q1: ReportedAmount | None = None,
    q2: ReportedAmount | None = None,
    q3: ReportedAmount | None = None,
    fy: ReportedAmount | None = None,
) -> dict[Quarter, MetricResult]:
    if kind == "balance":
        return {
            "Q1": _point(q1),
            "Q2": _point(q2),
            "Q3": _point(q3),
            "Q4": _point(fy),
        }
    return {
        "Q1": _identity(q1, "Q1"),
        "Q2": _difference(q2, q1, "Q2", "Q1"),
        "Q3": _difference(q3, q2, "Q3", "Q2"),
        "Q4": _difference(fy, q3, "Q4", "Q3"),
    }


def ttm_from_single_quarters(
    quarters: tuple[Decimal | None, Decimal | None, Decimal | None, Decimal | None],
) -> MetricResult:
    if any(item is None for item in quarters):
        return _missing("ttm_requires_four_single_quarters")
    total = sum((item for item in quarters if item is not None), Decimal("0"))
    return MetricResult(value=total, reason=None, display=None, input_refs=("Q1", "Q2", "Q3", "Q4"))


def year_over_year(current: Decimal, prior: Decimal) -> MetricResult:
    if prior > 0:
        return MetricResult(
            value=(current / prior) - 1, reason=None, display=None, input_refs=("current", "prior")
        )
    if prior < 0 and current > 0:
        return MetricResult(
            value=None, reason="sign_change", display="由负转正", input_refs=("current", "prior")
        )
    if prior < 0 and current < prior:
        return MetricResult(
            value=None, reason="loss_widened", display="亏损扩大", input_refs=("current", "prior")
        )
    return _missing("prior_not_positive")


def gross_margin(revenue: Decimal, cost: Decimal) -> MetricResult:
    if revenue <= 0:
        return _missing("revenue_not_positive")
    return MetricResult(
        value=(revenue - cost) / revenue, reason=None, display=None, input_refs=("revenue", "cost")
    )


def debt_ratio(liabilities: Decimal, assets: Decimal) -> MetricResult:
    if assets <= 0:
        return _missing("assets_not_positive")
    return MetricResult(
        value=liabilities / assets,
        reason=None,
        display=None,
        input_refs=("liabilities", "assets"),
    )


def current_ratio(
    current_assets: Decimal | None, current_liabilities: Decimal | None
) -> MetricResult:
    if current_assets is None or current_liabilities is None or current_liabilities == 0:
        return _missing("insufficient_data")
    return MetricResult(
        value=current_assets / current_liabilities,
        reason=None,
        display=None,
        input_refs=("current_assets", "current_liabilities"),
    )


def pe_ttm(market_cap: Decimal, profit: Decimal) -> MetricResult:
    if profit <= 0:
        return MetricResult(
            value=None, reason="not_applicable", display="N/A", input_refs=("market_cap", "profit")
        )
    return MetricResult(
        value=market_cap / profit, reason=None, display=None, input_refs=("market_cap", "profit")
    )


def pb_ratio(market_cap: Decimal, equity: Decimal) -> MetricResult:
    if equity <= 0:
        return MetricResult(
            value=None, reason="not_applicable", display="N/A", input_refs=("market_cap", "equity")
        )
    return MetricResult(
        value=market_cap / equity, reason=None, display=None, input_refs=("market_cap", "equity")
    )


def evaluate_rules(
    *,
    revenue_yoy: MetricResult | None,
    receivables_yoy: MetricResult | None,
    inventory_yoy: MetricResult | None,
    net_income_ttm: Decimal | None,
    cfo_ttm: Decimal | None,
    gross_margin_now: Decimal | None,
    gross_margin_prior: Decimal | None,
    margins_comparable: bool,
    current_ratio_value: Decimal | None,
    short_debt_yoy: Decimal | None,
    short_debt_threshold: Decimal = Decimal("0.2"),
) -> tuple[RuleResult, ...]:
    return (
        _gap(
            "R001",
            "receivables_growth_gap",
            receivables_yoy,
            revenue_yoy,
            Decimal("0.20"),
            "季节性可能干扰",
        ),
        _cfo_rule(net_income_ttm, cfo_ttm),
        _margin_drop(gross_margin_now, gross_margin_prior, margins_comparable),
        _gap(
            "R004",
            "inventory_growth_gap",
            inventory_yoy,
            revenue_yoy,
            Decimal("0.25"),
            "需结合库存和行业",
        ),
        _debt_rule(current_ratio_value, short_debt_yoy, short_debt_threshold),
        _positive_rule(
            revenue_yoy, cfo_ttm, gross_margin_now, gross_margin_prior, margins_comparable
        ),
    )


def _identity(amount: ReportedAmount | None, ref: str) -> MetricResult:
    if amount is None:
        return _missing("insufficient_data")
    return MetricResult(value=amount.value, reason=None, display=None, input_refs=(ref,))


def _difference(
    later: ReportedAmount | None,
    earlier: ReportedAmount | None,
    later_ref: str,
    earlier_ref: str,
) -> MetricResult:
    if later is None or earlier is None:
        return _missing("insufficient_data")
    if later.unit != earlier.unit or later.version != earlier.version:
        return _missing("revision_mismatch")
    return MetricResult(
        value=later.value - earlier.value,
        reason=None,
        display=None,
        input_refs=(later_ref, earlier_ref),
    )


def _point(amount: ReportedAmount | None) -> MetricResult:
    if amount is None:
        return _missing("insufficient_data")
    return MetricResult(
        value=amount.value,
        reason="balance_is_point_in_time",
        display=None,
        input_refs=("balance",),
    )


def _missing(reason: str) -> MetricResult:
    return MetricResult(value=None, reason=reason, display=None)


def _comparable_percent(result: MetricResult | None) -> Decimal | None:
    if result is None or result.value is None or result.reason is not None:
        return None
    return result.value


def _gap(
    rule_id: str,
    name: str,
    left: MetricResult | None,
    right: MetricResult | None,
    threshold: Decimal,
    caveat: str,
) -> RuleResult:
    del name
    first = _comparable_percent(left)
    second = _comparable_percent(right)
    if first is None or second is None:
        return RuleResult(rule_id, "not_evaluated", None, "两期不可比或缺少同比", caveat)
    if first - second > threshold:
        return RuleResult(rule_id, "triggered", "watch", "增速差超过阈值", caveat)
    return RuleResult(rule_id, "clear", None, "未超过阈值", caveat)


def _cfo_rule(profit: Decimal | None, cfo: Decimal | None) -> RuleResult:
    caveat = "显示数值并非自动判定造假"
    if profit is None or cfo is None:
        return RuleResult("R002", "not_evaluated", None, "缺少 TTM", caveat)
    if profit > 0 and cfo < profit * Decimal("0.5"):
        return RuleResult("R002", "triggered", "watch", "经营现金流低于净利润的一半", caveat)
    return RuleResult("R002", "clear", None, "未触发", caveat)


def _margin_drop(now: Decimal | None, prior: Decimal | None, comparable: bool) -> RuleResult:
    caveat = "需要相同期间长度和业务口径"
    if not comparable or now is None or prior is None:
        return RuleResult("R003", "not_evaluated", None, "口径不可比或缺数据", caveat)
    if prior - now > Decimal("0.05"):
        return RuleResult("R003", "triggered", "watch", "毛利率同比下降超过 5 个百分点", caveat)
    return RuleResult("R003", "clear", None, "未超过 5 个百分点", caveat)


def _debt_rule(ratio: Decimal | None, debt_yoy: Decimal | None, threshold: Decimal) -> RuleResult:
    caveat = "阈值可配置"
    if ratio is None or debt_yoy is None:
        return RuleResult("R005", "not_evaluated", None, "流动比率或短期债务缺少数据", caveat)
    if ratio < 1 and debt_yoy > threshold:
        return RuleResult("R005", "triggered", "high", "流动比率低于 1 且短期债务上升", caveat)
    return RuleResult("R005", "clear", None, "未同时满足", caveat)


def _positive_rule(
    revenue_yoy: MetricResult | None,
    cfo: Decimal | None,
    margin_now: Decimal | None,
    margin_prior: Decimal | None,
    comparable: bool,
) -> RuleResult:
    caveat = "值得进一步研究的积极变化，不是买入信号"
    growth = _comparable_percent(revenue_yoy)
    if (
        growth is None
        or cfo is None
        or not comparable
        or margin_now is None
        or margin_prior is None
    ):
        return RuleResult("R006", "not_evaluated", None, "条件不完整", caveat)
    margin_ok = margin_prior - margin_now <= Decimal("0.05")
    if growth > Decimal("0.15") and cfo > 0 and margin_ok:
        return RuleResult("R006", "triggered", "info", caveat, caveat)
    return RuleResult("R006", "clear", None, "未同时满足", caveat)
