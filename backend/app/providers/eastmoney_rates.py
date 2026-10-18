"""东方财富数据中心的利率与景气数据：SHIBOR、中美国债收益率、中国制造业 PMI。

只做 URL 构造与解析。数据中心是 data.eastmoney.com 页面匿名调用的报表接口。

口径（2026-10 实测）：
- `RPT_IMP_INTRESTRATEN`：MARKET_CODE=001 为 SHIBOR，CURRENCY_CODE=CNY；
  INDICATOR_ID 001 隔夜、101 一周；IR_RATE 单位 %。
- `RPTA_WEB_TREASURYYIELD`：EMM00166462 中国 2 年、EMM00166466 中国 10 年、
  EMG00001306 美国 2 年、EMG00001310 美国 10 年，单位 %。
- `RPT_ECONOMY_PMI`：MAKE_INDEX 制造业 PMI，REPORT_DATE 为所属月份。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlencode

from app.providers import eastmoney_boards as em

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DATACENTER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://data.eastmoney.com/",
}
ShiborTenor = Literal["ON", "1W"]
_SHIBOR_ID: dict[ShiborTenor, str] = {"ON": "001", "1W": "101"}


@dataclass(frozen=True)
class DatedValue:
    day: date
    value: Decimal


@dataclass(frozen=True)
class YieldRow:
    day: date
    cn2y: Decimal | None
    cn10y: Decimal | None
    us2y: Decimal | None
    us10y: Decimal | None


def shibor_url(tenor: ShiborTenor, page_size: int = 120) -> str:
    return _url(
        {
            "reportName": "RPT_IMP_INTRESTRATEN",
            "columns": "REPORT_DATE,IR_RATE",
            "filter": (
                f'(MARKET_CODE="001")(CURRENCY_CODE="CNY")(INDICATOR_ID="{_SHIBOR_ID[tenor]}")'
            ),
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageSize": str(page_size),
            "pageNumber": "1",
        }
    )


def treasury_url(page_size: int = 120, page_number: int = 1) -> str:
    return _url(
        {
            "reportName": "RPTA_WEB_TREASURYYIELD",
            "columns": "SOLAR_DATE,EMM00166462,EMM00166466,EMG00001306,EMG00001310",
            "sortColumns": "SOLAR_DATE",
            "sortTypes": "-1",
            "pageSize": str(page_size),
            "pageNumber": str(page_number),
        }
    )


def pmi_url(page_size: int = 24) -> str:
    return _url(
        {
            "reportName": "RPT_ECONOMY_PMI",
            "columns": "REPORT_DATE,MAKE_INDEX",
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageSize": str(page_size),
            "pageNumber": "1",
        }
    )


def parse_dated(
    payload: bytes, value_column: str, date_column: str = "REPORT_DATE"
) -> tuple[DatedValue, ...]:
    rows: list[DatedValue] = []
    for row in _rows(payload):
        day, value = em._day(row.get(date_column)), em._number(row.get(value_column))
        if day is not None and value is not None:
            rows.append(DatedValue(day, value))
    return tuple(sorted(rows, key=lambda item: item.day))


def parse_treasury(payload: bytes) -> tuple[YieldRow, ...]:
    rows: list[YieldRow] = []
    for row in _rows(payload):
        day = em._day(row.get("SOLAR_DATE"))
        if day is None:
            continue
        rows.append(
            YieldRow(
                day=day,
                cn2y=em._number(row.get("EMM00166462")),
                cn10y=em._number(row.get("EMM00166466")),
                us2y=em._number(row.get("EMG00001306")),
                us10y=em._number(row.get("EMG00001310")),
            )
        )
    return tuple(sorted(rows, key=lambda item: item.day))


def _url(params: dict[str, str]) -> str:
    return f"{DATACENTER_URL}?{urlencode({**params, 'source': 'WEB', 'client': 'WEB'})}"


def _rows(payload: bytes) -> list[dict[str, object]]:
    document = em._object(em._decode(payload, "东财数据中心"), "东财数据中心")
    result = document.get("result")
    if result is None:
        return []
    data = em._object(result, "东财数据中心").get("data") or []
    return [em._object(item, "东财数据中心") for item in em._list(data, "东财数据中心")]
