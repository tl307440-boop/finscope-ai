"""东方财富数据中心：全市场估值截面、上市日期、GDP 累计值。

只做 URL 构造与解析。口径（2026-10 实测）：

- `RPT_VALUEANALYSIS_DET`：按 `TRADE_DATE` 过滤能取到那一天的全市场截面（当时在市的股票，
  2021-10-15 为 4391 只、2026-10-16 为 5577 只，所以没有幸存者偏差）。
  `TOTAL_MARKET_CAP` 单位是**元**，`PB_MRQ` 是最近一期财报的市净率。
  **注意**：`SECURITY_NAME_ABBR` 是**当前**简称，不是那一天的简称
  （2021-10-15 的数据里能看到 2024 年才戴帽的 ST 晨鸣），所以回填历史时按名字剔除 ST 是近似。
- `RPT_F10_ORG_BASICINFO`：`LISTING_DATE` 上市日期，用来剔除上市不满 180 天的次新股。
- `RPT_ECONOMY_GDP`：`DOMESTICL_PRODUCT_BASE` 是**年内累计**的 GDP（亿元），
  不是单季值；`REPORT_DATE` 落在报告期所在月份的 1 号。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from app.providers import eastmoney_boards as em

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://data.eastmoney.com/",
}
CROSS_SECTION_SOURCE = "eastmoney_valuation_cross_section"
GDP_SOURCE = "eastmoney_gdp"
TREASURY_SOURCE = "eastmoney_treasury_yield"
PAGE_SIZE = 500


@dataclass(frozen=True)
class StockValuation:
    code: str
    name: str
    total_market_cap: Decimal | None
    pb_mrq: Decimal | None


@dataclass(frozen=True)
class CrossSectionPage:
    rows: tuple[StockValuation, ...]
    pages: int
    count: int


@dataclass(frozen=True)
class GdpPoint:
    quarter_end: date
    cumulative_billion_cny: Decimal
    label: str


def cross_section_url(day: date, page: int, page_size: int = PAGE_SIZE) -> str:
    return _url(
        {
            "reportName": "RPT_VALUEANALYSIS_DET",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,TOTAL_MARKET_CAP,PB_MRQ,TRADE_DATE",
            "filter": f"(TRADE_DATE='{day.isoformat()}')",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": str(page_size),
            "pageNumber": str(page),
        }
    )


def listing_url(page: int, page_size: int = PAGE_SIZE) -> str:
    return _url(
        {
            "reportName": "RPT_F10_ORG_BASICINFO",
            "columns": "SECURITY_CODE,LISTING_DATE",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "pageSize": str(page_size),
            "pageNumber": str(page),
        }
    )


def gdp_url(page_size: int = 120) -> str:
    return _url(
        {
            "reportName": "RPT_ECONOMY_GDP",
            "columns": "REPORT_DATE,TIME,DOMESTICL_PRODUCT_BASE",
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageSize": str(page_size),
            "pageNumber": "1",
        }
    )


def parse_cross_section(payload: bytes, day: date) -> CrossSectionPage:
    result = _result(payload)
    if result is None:
        return CrossSectionPage(rows=(), pages=0, count=0)
    rows: list[StockValuation] = []
    for item in em._list(result.get("data") or [], "东财估值截面"):
        row = em._object(item, "东财估值截面")
        code = _code(row.get("SECURITY_CODE"))
        if code is None:
            continue
        row_day = em._day(row.get("TRADE_DATE"))
        if row_day is not None and row_day != day:
            # 过滤条件被忽略时上游会回别的日期，整页作废。
            raise em._payload_error("东财估值截面")
        rows.append(
            StockValuation(
                code=code,
                name=str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                total_market_cap=em._number(row.get("TOTAL_MARKET_CAP")),
                pb_mrq=em._number(row.get("PB_MRQ")),
            )
        )
    return CrossSectionPage(
        rows=tuple(rows),
        pages=_int(result.get("pages")),
        count=_int(result.get("count")),
    )


def parse_listing_dates(payload: bytes) -> tuple[dict[str, date], int]:
    """返回 (代码 → 上市日期, 总页数)。没有上市日期的证券直接跳过，不猜。"""

    result = _result(payload)
    if result is None:
        return {}, 0
    found: dict[str, date] = {}
    for item in em._list(result.get("data") or [], "东财上市日期"):
        row = em._object(item, "东财上市日期")
        code = _code(row.get("SECURITY_CODE"))
        listed = em._day(row.get("LISTING_DATE"))
        if code is not None and listed is not None:
            found[code] = listed
    return found, _int(result.get("pages"))


def parse_gdp(payload: bytes) -> tuple[GdpPoint, ...]:
    result = _result(payload)
    if result is None:
        return ()
    points: dict[date, GdpPoint] = {}
    for item in em._list(result.get("data") or [], "东财 GDP"):
        row = em._object(item, "东财 GDP")
        report = em._day(row.get("REPORT_DATE"))
        value = em._number(row.get("DOMESTICL_PRODUCT_BASE"))
        if report is None or value is None or report.month not in (3, 6, 9, 12):
            continue
        quarter_end = date(
            report.year, report.month, calendar.monthrange(report.year, report.month)[1]
        )
        points[quarter_end] = GdpPoint(
            quarter_end=quarter_end,
            cumulative_billion_cny=value,
            label=str(row.get("TIME") or "").strip(),
        )
    return tuple(points[day] for day in sorted(points))


def _result(payload: bytes) -> dict[str, object] | None:
    document = em._object(em._decode(payload, "东财数据中心"), "东财数据中心")
    result = document.get("result")
    if result is None:
        return None
    return em._object(result, "东财数据中心")


def _code(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int(value: object) -> int:
    number = em._number(value)
    return int(number) if number is not None else 0


def _url(params: dict[str, str]) -> str:
    return f"{DATACENTER_URL}?{urlencode({**params, 'source': 'WEB', 'client': 'WEB'})}"
