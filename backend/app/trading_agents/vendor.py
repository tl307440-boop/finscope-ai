from dataclasses import dataclass
from datetime import date
from decimal import Decimal

PINNED_UPSTREAM = "1394a3f72aa4393e1a98f51b382434c4b4c2d972"


@dataclass(frozen=True)
class IncomeRow:
    symbol: str
    notice_date: date
    period: str
    revenue: Decimal


# 规格书 21.4 已核对的茅台累计数，只给 Mock 回放，不当作新抓到的行情。
MOUTAI_INCOME = (
    IncomeRow("600519.SH", date(2026, 4, 25), "2026Q1", Decimal("54702912385.23")),
    IncomeRow("600519.SH", date(2026, 8, 15), "2026H1", Decimal("92278072083.21")),
)
MOUTAI_Q2_REVENUE = Decimal("37575159697.98")


def income_visible(symbol: str, as_of: date) -> str:
    rows = [row for row in MOUTAI_INCOME if row.symbol == symbol and row.notice_date <= as_of]
    if symbol != "600519.SH":
        return "DATA_UNAVAILABLE: 这只证券没有已核对的报表夹具"
    if not rows:
        return "NO_DATA_AVAILABLE: 没有不晚于 as_of 的报表"
    lines = [
        f"{row.period} 累计营业收入 {row.revenue} 披露日 {row.notice_date.isoformat()}"
        for row in rows
    ]
    return "\n".join(lines)


def single_quarter_check(symbol: str, as_of: date) -> str:
    if symbol != "600519.SH":
        return "DATA_UNAVAILABLE: 这只证券没有已核对的报表夹具"
    visible = {row.period: row for row in MOUTAI_INCOME if row.notice_date <= as_of}
    first = visible.get("2026Q1")
    half = visible.get("2026H1")
    if first is None or half is None:
        return "NO_DATA_AVAILABLE: 差分需要一季报和中报都已披露"
    gap = half.revenue - first.revenue
    delta = abs(gap - MOUTAI_Q2_REVENUE)
    return f"中报累计减一季报累计 {gap}，单季营收 {MOUTAI_Q2_REVENUE}，差额 {delta}"


def bars_note(symbol: str, trade_date: date) -> str:
    if symbol != "600519.SH":
        return "DATA_UNAVAILABLE: 日线只使用已经入库的记录，这只证券不在夹具里"
    return f"NO_DATA_AVAILABLE: Mock 不补造 {trade_date.isoformat()} 的收盘价"


def insider_note() -> str:
    return "A 股无该工具覆盖"
