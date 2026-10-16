"""中国宏观六序列的公开数据源。

* CPI / PPI / PMI / M2 / 规模以上工业增加值：东方财富数据中心 ``RPT_ECONOMY_*``
  （转载国家统计局、中国人民银行的月度公布值）。
* 社会融资规模增量：商务部数据中心公开接口（转载中国人民银行口径，单位亿元）。

这些接口只给「观察月」，不给公布日。时点回看要求 ``published_at <= as_of``，所以这里为每个序列
定一个**保守上界**：官方实际公布日一定不晚于它，这样回看时只会晚看到、不会提前看到未公布的数据。
上界规则写在 ``PUBLISH_RULES`` 里，改规则要同步改使用说明。
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlencode

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.services.china_macro import CsvObservation

EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
MOFCOM_URL = "https://data.mofcom.gov.cn/datamofcom/front/gnmy/shrzgmQuery"
PAGE_SIZE = 120  # 约 10 年月度数据；重复写入由去重键保证幂等

PublishRule = Literal["next_month_15", "next_month_1", "next_month_20"]
# 官方公布日（大致）：CPI/PPI 次月 9-15 日；PMI 当月最后一天；M2、工业增加值次月 10-17 日；
# 社融次月 10-15 日。上界取更晚的整日，宁晚勿早。
PUBLISH_RULES: dict[str, PublishRule] = {
    "CPI_CN": "next_month_15",
    "PPI_CN": "next_month_15",
    "PMI_CN": "next_month_1",
    "M2_CN": "next_month_20",
    "INDUS_CN": "next_month_20",
    "SOCIAL_FINANCING": "next_month_20",
}


@dataclass(frozen=True)
class EastmoneySpec:
    series_code: str
    report: str
    field: str
    unit: str
    source: str


EASTMONEY_SPECS: tuple[EastmoneySpec, ...] = (
    EastmoneySpec(
        "CPI_CN", "RPT_ECONOMY_CPI", "NATIONAL_SAME", "Percent", "东方财富数据中心（国家统计局）"
    ),
    EastmoneySpec(
        "PPI_CN", "RPT_ECONOMY_PPI", "BASE_SAME", "Percent", "东方财富数据中心（国家统计局）"
    ),
    EastmoneySpec(
        "PMI_CN", "RPT_ECONOMY_PMI", "MAKE_INDEX", "Index", "东方财富数据中心（国家统计局）"
    ),
    EastmoneySpec(
        "M2_CN",
        "RPT_ECONOMY_CURRENCY_SUPPLY",
        "BASIC_CURRENCY_SAME",
        "Percent",
        "东方财富数据中心（中国人民银行）",
    ),
    EastmoneySpec(
        "INDUS_CN",
        "RPT_ECONOMY_INDUS_GROW",
        "BASE_SAME",
        "Percent",
        "东方财富数据中心（国家统计局）",
    ),
)
SOCIAL_FINANCING_SOURCE = "商务部数据中心（中国人民银行口径）"
SOCIAL_FINANCING_UNIT = "亿元"


def published_upper_bound(series_code: str, observation_date: date) -> date:
    rule = PUBLISH_RULES[series_code]
    year, month = observation_date.year, observation_date.month
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    day = {"next_month_15": 15, "next_month_1": 1, "next_month_20": 20}[rule]
    return date(next_year, next_month, day)


def eastmoney_url(spec: EastmoneySpec) -> str:
    query = urlencode(
        {
            "reportName": spec.report,
            "columns": "ALL",
            "pageNumber": 1,
            "pageSize": PAGE_SIZE,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        }
    )
    return f"{EASTMONEY_URL}?{query}"


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_eastmoney(payload: bytes, spec: EastmoneySpec) -> tuple[CsvObservation, ...]:
    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise ProviderError(
            code="BAD_PAYLOAD", message=f"{spec.series_code} 返回的不是 JSON", status_code=502
        ) from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise ProviderError(
            code="UPSTREAM_REJECTED",
            message=f"{spec.series_code} 上游没有给出数据",
            status_code=502,
        )
    result = body.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ProviderError(
            code="BAD_PAYLOAD", message=f"{spec.series_code} 缺少数据列表", status_code=502
        )
    observations: list[CsvObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        observed = _month_start(row.get("REPORT_DATE"))
        value = _decimal(row.get(spec.field))
        if observed is None or value is None:
            continue
        observations.append(
            CsvObservation(
                series_code=spec.series_code,
                observation_date=observed,
                value=value,
                unit=spec.unit,
                source=spec.source,
                published_at=published_upper_bound(spec.series_code, observed),
            )
        )
    return tuple(observations)


def _month_start(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 7:
        return None
    try:
        return date(int(raw[0:4]), int(raw[5:7]), 1)
    except ValueError:
        return None


def parse_mofcom(payload: bytes) -> tuple[CsvObservation, ...]:
    try:
        rows = json.loads(payload)
    except ValueError as exc:
        raise ProviderError(
            code="BAD_PAYLOAD", message="社会融资规模返回的不是 JSON", status_code=502
        ) from exc
    if not isinstance(rows, list):
        raise ProviderError(code="BAD_PAYLOAD", message="社会融资规模缺少数据列表", status_code=502)
    observations: list[CsvObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_month = row.get("date")
        value = _decimal(row.get("tiosfs"))
        if not isinstance(raw_month, str) or len(raw_month) != 6 or value is None:
            continue
        try:
            observed = date(int(raw_month[:4]), int(raw_month[4:]), 1)
        except ValueError:
            continue
        observations.append(
            CsvObservation(
                series_code="SOCIAL_FINANCING",
                observation_date=observed,
                value=value,
                unit=SOCIAL_FINANCING_UNIT,
                source=SOCIAL_FINANCING_SOURCE,
                published_at=published_upper_bound("SOCIAL_FINANCING", observed),
            )
        )
    return tuple(observations)


async def fetch_eastmoney(
    client: ResilientHttpClient, spec: EastmoneySpec
) -> tuple[CsvObservation, ...]:
    return parse_eastmoney(await client.get(eastmoney_url(spec)), spec)


async def fetch_social_financing(client: ResilientHttpClient) -> tuple[CsvObservation, ...]:
    payload = await client.post(
        MOFCOM_URL,
        content=b"",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return parse_mofcom(payload)
