"""外国发行人（20-F / 40-F / 6-K）的 IFRS 事实归一：读取时统一成项目内部用的 us-gaap 短名。

库里保留 SEC 原始概念（`ifrs-full:Revenue`）和原币种，这里只在读取时做两件事：

1. 把 IFRS 概念映射到同口径的 us-gaap 概念，下游（财务卡片、体检快照、分析师）只认一套名字；
   一个概念有多个候选时按优先级取，如归母净利润优先于合并净利润。
2. 一家公司只保留一种报告币种。companyfacts 里可能同时出现本币和美元便利换算，
   混着算比率会得到毫无意义的数，所以只留事实最多的那种货币（并列时取 USD）。

不做汇率换算：单位原样带给界面，让用户看到的是披露时的币种。
"""

from collections import Counter
from dataclasses import replace

from app.repositories.filings import FactRecord

# (ifrs-full 概念, 内部 us-gaap 概念)；同一内部概念的多个来源，越靠前优先级越高。
IFRS_TO_US_GAAP: tuple[tuple[str, str], ...] = (
    ("Revenue", "Revenues"),
    ("RevenueFromContractsWithCustomers", "Revenues"),
    ("CostOfSales", "CostOfRevenue"),
    ("GrossProfit", "GrossProfit"),
    ("ProfitLossFromOperatingActivities", "OperatingIncomeLoss"),
    ("ProfitLossAttributableToOwnersOfParent", "NetIncomeLoss"),
    ("ProfitLoss", "NetIncomeLoss"),
    ("DilutedEarningsLossPerShare", "EarningsPerShareDiluted"),
    ("CashFlowsFromUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivities"),
    ("Assets", "Assets"),
    ("CurrentAssets", "AssetsCurrent"),
    ("Liabilities", "Liabilities"),
    ("CurrentLiabilities", "LiabilitiesCurrent"),
    ("EquityAttributableToOwnersOfParent", "StockholdersEquity"),
    ("Equity", "StockholdersEquity"),
    ("TradeAndOtherCurrentReceivables", "AccountsReceivableNetCurrent"),
    ("Inventories", "InventoryNet"),
    ("ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpense"),
    ("CashAndCashEquivalents", "CashAndCashEquivalentsAtCarryingValue"),
)
IFRS_CONCEPTS = frozenset(f"ifrs-full:{name}" for name, _target in IFRS_TO_US_GAAP)
_PRIORITY = {f"ifrs-full:{name}": rank for rank, (name, _target) in enumerate(IFRS_TO_US_GAAP)}
_TARGET = {f"ifrs-full:{name}": f"us-gaap:{target}" for name, target in IFRS_TO_US_GAAP}

NON_CURRENCY_UNITS = frozenset({"shares", "pure"})


def currency_of(unit: str) -> str | None:
    """`TWD` / `TWD/shares` 都返回 `TWD`；`shares`、`pure` 这类非货币单位返回 None。"""

    head = unit.split("/", 1)[0]
    return None if head.lower() in NON_CURRENCY_UNITS else head


def reporting_currency(facts: list[FactRecord]) -> str | None:
    counts = Counter(
        currency
        for fact in facts
        if "/" not in fact.unit and (currency := currency_of(fact.unit)) is not None
    )
    if not counts:
        return None
    top = max(counts.values())
    leaders = sorted(code for code, count in counts.items() if count == top)
    return "USD" if "USD" in leaders else leaders[0]


def normalize_facts(facts: list[FactRecord]) -> list[FactRecord]:
    """IFRS 概念转 us-gaap 短名、只留一种报告币种；纯 us-gaap 公司基本原样返回。"""

    if not facts:
        return facts
    currency = reporting_currency(facts)
    kept: dict[tuple[str, str, object, object], tuple[int, FactRecord]] = {}
    for fact in facts:
        fact_currency = currency_of(fact.unit)
        if currency is not None and fact_currency not in (None, currency):
            continue
        rank = _PRIORITY.get(fact.concept, -1)
        mapped = fact
        if fact.concept in _TARGET:
            mapped = replace(fact, concept=_TARGET[fact.concept])
        elif fact.concept.startswith("ifrs-full:"):
            continue
        key = (mapped.concept, mapped.unit, mapped.period_start, mapped.period_end)
        held = kept.get(key)
        # 已有的来自优先级更高的概念（rank 更小，且不是原生 us-gaap 的 -1）就不覆盖。
        if held is not None and _outranks(held, rank, mapped):
            continue
        kept[key] = (rank, mapped)
    return [item for _rank, item in kept.values()]


def _outranks(held: tuple[int, FactRecord], rank: int, candidate: FactRecord) -> bool:
    held_rank, held_fact = held
    if held_rank == rank:
        return held_fact.filed >= candidate.filed
    if held_rank == -1:
        return True  # 原生 us-gaap 事实永远优先于从 IFRS 映射来的
    if rank == -1:
        return False
    return held_rank < rank
