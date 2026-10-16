"""A 股三大报表：东方财富 F10 数据中心的利润表、资产负债表、现金流量表。

和 akshare 的 ``stock_*_by_report_em`` 系列用的是同一批公开接口；这里直接调用，
不引入 pandas 一整套依赖，也方便做超时、限速和重试。

每个科目都映射成与 SEC companyfacts 同形的事实（概念名、期间、单位、公告日），
这样同比、比率、规则读数等现有计算可以原样复用。映射规则：

* 财报在 A 股是**累计口径**：一季报、中报（半年）、三季报、年报。流量科目的期间起点
  一律是当年 1 月 1 日，一季报跨度约 89 天算「单季」，其余按「累计」处理，和 XBRL 一致。
* ``filed`` 取公告日（NOTICE_DATE），时间旅行时只看 ``filed <= as_of``。
* 毛利 = 营业收入 − 营业成本，是唯一的派生科目，来源字段里写明公式，不当作上游原值。
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlencode

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient

_BASE = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_PAGE_SIZE = 40
SOURCE_PAGE = (
    "https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code={code}"
)
CONCEPT_PREFIX = "cn-gaap:"

INCOME = "RPT_F10_FINANCE_GINCOME"
BALANCE = "RPT_F10_FINANCE_GBALANCE"
CASHFLOW = "RPT_F10_FINANCE_GCASHFLOW"

_FORMS = {
    "03-31": ("一季报", "Q1"),
    "06-30": ("中报", "H1"),
    "09-30": ("三季报", "Q3"),
    "12-31": ("年报", "FY"),
}


@dataclass(frozen=True)
class CnFact:
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    form: str
    fiscal_year: int
    fiscal_period: str
    filed: date
    source_field: str
    source_url: str


def statement_url(report: str, secucode: str) -> str:
    query = urlencode(
        {
            "reportName": report,
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": 1,
            "pageSize": _PAGE_SIZE,
            "sortTypes": -1,
            "sortColumns": "REPORT_DATE",
            "source": "HSF10",
            "client": "PC",
        }
    )
    return f"{_BASE}?{query}"


async def fetch_statements(client: ResilientHttpClient, secucode: str) -> tuple[CnFact, ...]:
    """三张表各取一次；全部为空才算没有数据，只缺一张表就只缺那部分科目。"""

    facts: list[CnFact] = []
    for report in (INCOME, BALANCE, CASHFLOW):
        payload = await client.get(statement_url(report, secucode))
        facts.extend(parse_statement(payload, report, secucode))
    return tuple(facts)


def parse_statement(payload: bytes, report: str, secucode: str) -> tuple[CnFact, ...]:
    rows = _rows(payload)
    source = SOURCE_PAGE.format(code=secucode.replace(".", "").lower())
    facts: list[CnFact] = []
    for row in rows:
        end = _day(row.get("REPORT_DATE"))
        filed = _day(row.get("NOTICE_DATE"))
        if end is None or filed is None:
            continue
        form_key = end.strftime("%m-%d")
        if form_key not in _FORMS:
            continue
        form, period = _FORMS[form_key]
        unit = str(row.get("CURRENCY") or "CNY").upper()
        start = date(end.year, 1, 1)
        for concept, value, field, per_share, instant in _mapped(row, report):
            facts.append(
                CnFact(
                    concept=f"{CONCEPT_PREFIX}{concept}",
                    unit=f"{unit}/shares" if per_share else unit,
                    value=value,
                    period_start=None if instant else start,
                    period_end=end,
                    form=form,
                    fiscal_year=end.year,
                    fiscal_period=period,
                    filed=filed,
                    source_field=field,
                    source_url=source,
                )
            )
    return tuple(facts)


def _mapped(row: dict[str, object], report: str) -> list[tuple[str, Decimal, str, bool, bool]]:
    """(概念, 数值, 来源字段, 是否每股, 是否时点) 列表；缺值的科目直接略过。"""

    out: list[tuple[str, Decimal, str, bool, bool]] = []

    def put(concept: str, field: str, *, per_share: bool = False, instant: bool = False) -> None:
        number = _number(row.get(field))
        if number is not None:
            out.append((concept, number, field, per_share, instant))

    if report == INCOME:
        revenue_field = (
            "OPERATE_INCOME"
            if _number(row.get("OPERATE_INCOME")) is not None
            else "TOTAL_OPERATE_INCOME"
        )
        put("Revenues", revenue_field)
        put("CostOfRevenue", "OPERATE_COST")
        revenue, cost = _number(row.get(revenue_field)), _number(row.get("OPERATE_COST"))
        if revenue is not None and cost is not None:
            out.append(
                ("GrossProfit", revenue - cost, f"{revenue_field} - OPERATE_COST", False, False)
            )
        put("OperatingIncomeLoss", "OPERATE_PROFIT")
        put("NetIncomeLoss", "PARENT_NETPROFIT")
        put("EarningsPerShareDiluted", "DILUTED_EPS", per_share=True)
        put("ResearchAndDevelopmentExpense", "RESEARCH_EXPENSE")
    elif report == BALANCE:
        put("Assets", "TOTAL_ASSETS", instant=True)
        put("AssetsCurrent", "TOTAL_CURRENT_ASSETS", instant=True)
        put("Liabilities", "TOTAL_LIABILITIES", instant=True)
        put("LiabilitiesCurrent", "TOTAL_CURRENT_LIAB", instant=True)
        put("StockholdersEquity", "TOTAL_PARENT_EQUITY", instant=True)
        put("CashAndCashEquivalentsAtCarryingValue", "MONETARYFUNDS", instant=True)
        receivable_field = (
            "ACCOUNTS_RECE"
            if _number(row.get("ACCOUNTS_RECE")) is not None
            else "NOTE_ACCOUNTS_RECE"
        )
        put("AccountsReceivableNetCurrent", receivable_field, instant=True)
        put("InventoryNet", "INVENTORY", instant=True)
    elif report == CASHFLOW:
        put("NetCashProvidedByUsedInOperatingActivities", "NETCASH_OPERATE")
    return out


def _rows(payload: bytes) -> list[dict[str, object]]:
    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    result = cast(dict[str, object], decoded).get("result")
    if result is None:
        return []
    if not isinstance(result, dict):
        raise _payload_error()
    data = cast(dict[str, object], result).get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise _payload_error()
    return [cast(dict[str, object], item) for item in data if isinstance(item, dict)]


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _day(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message="无法解析东方财富财报响应", status_code=502
    )
