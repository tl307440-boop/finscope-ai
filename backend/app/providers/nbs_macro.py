"""国家统计局数据发布库（data.stats.gov.cn 新版）的宏观序列。

只做请求体构造与解析，HTTP 超时 / 重试 / 限速由 ``ResilientHttpClient`` 负责。

接口（2026-10 实测，匿名可读，页面自己也用这一组接口）：
``POST /dg/website/publicrelease/web/external/stream/esData``
请求体 ``{cid, indicatorIds, das, showType, rootId, dts}``：
- ``rootId``：月度数据 / 季度数据目录的根节点；``cid``：指标所在目录叶子；
- ``dts``：``["201801MM-202610MM"]``（月）或 ``["201801SS-202602SS"]``（季，``02`` 是第 2 季）；
- 返回 ``data = [{code: "202609MM", name: "2026年9月", values: [{_id, i_showname, value}]}]``，
  按时间倒序，尚未公布的期次 ``value`` 为空字符串。

目录 ID / 指标 ID 都是国家统计局内部的 GUID，目录改版时可能整体换掉。所以每个指标登记一个
``expect`` 片段，解析时核对返回的 ``i_showname``：对不上就报错，而不是静默取成别的指标。

口径说明：
- 「累计同比」类（工业企业利润、固定资产投资、商品房销售额、财政支出）是年初至当月累计，
  1—2 月合并公布，所以 1 月没有值；页面「较上期」只在紧邻上一期存在时才给。
- 核心 CPI 原值是「上年同月 = 100」的指数，同比 % = 指数 − 100；GDP 同理（上年同期 = 100）。
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.providers.china_macro_sources import published_upper_bound
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.services.china_macro import CsvObservation

ES_DATA_URL = "https://data.stats.gov.cn/dg/website/publicrelease/web/external/stream/esData"
MONTH_ROOT_ID = "fc982599aa684be7969d7b90b1bd0e84"
QUARTER_ROOT_ID = "a94b8b7365a94874968cabbe392cf679"
NATION = {"text": "全国", "value": "000000000000"}
HISTORY_START_YEAR = 2018
SOURCE_NAME = "国家统计局数据发布库"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
}

Frequency = Literal["monthly", "quarterly"]
_MONTH_CODE = re.compile(r"^(\d{4})(\d{2})MM$")
_QUARTER_CODE = re.compile(r"^(\d{4})0([1-4])SS$")


@dataclass(frozen=True)
class NbsIndicator:
    series_code: str
    indicator_id: str
    # 返回的 i_showname 必须包含这段文字，防止目录改版后静默取错指标。
    expect: str
    # 取到的值减去它：「上年同月 = 100」的指数 → 同比 %；本身就是 % 的填 0。
    offset: Decimal = Decimal(0)
    unit: str = "Percent"


@dataclass(frozen=True)
class NbsRequest:
    cid: str
    frequency: Frequency
    indicators: tuple[NbsIndicator, ...]


_HUNDRED = Decimal(100)

NBS_REQUESTS: tuple[NbsRequest, ...] = (
    # 核心 CPI 的目录按年代拆成几段，每段的指标 ID 不同：2026- 与 2021-2025。
    NbsRequest(
        cid="5c7452825c7c4dcba391db5ca7f335c5",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "CORE_CPI_CN", "71be3d43d2fb44188199840272463ae0", "不包括食品和能源", _HUNDRED
            ),
        ),
    ),
    NbsRequest(
        cid="809d2522b0fe4be89142650341b19083",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "CORE_CPI_CN", "c2050e97c49a4763a6d0f0f38bf0b4ed", "不包括食品和能源", _HUNDRED
            ),
        ),
    ),
    NbsRequest(
        cid="93ffbb1aa85740d3aa2618371508b606",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "PMI_NEW_ORDERS_CN",
                "4151df33b53f4d02ae9f51fe402f1a50",
                "新订单指数",
                unit="Index",
            ),
            NbsIndicator(
                "PMI_NEW_EXPORT_ORDERS_CN",
                "fb245388740743d0974af1b00cfd27bb",
                "新出口订单指数",
                unit="Index",
            ),
        ),
    ),
    NbsRequest(
        cid="d760d189206642ce8afad57640408e09",
        frequency="monthly",
        indicators=(
            NbsIndicator("INDUS_PROFIT_CN", "7f30595d047445e0a3880ab928795892", "利润总额累计增长"),
        ),
    ),
    NbsRequest(
        cid="5129067b149d4ddfbec1ffc478d35bfb",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "PRIVATE_INVEST_CN", "287f55a9520d43a9b3e045860a49fedd", "民间固定资产投资累计增长"
            ),
        ),
    ),
    NbsRequest(
        cid="9002859582574437a91914e0fd50166b",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "MFG_INVEST_CN", "d17718248ba14e2ba7b34d39623001ea", "制造业固定资产投资额累计增长"
            ),
        ),
    ),
    NbsRequest(
        cid="5530aa75038249fab2e5e10a9997c18b",
        frequency="monthly",
        indicators=(
            NbsIndicator(
                "HOUSE_SALES_CN", "4617f4bb61234af9ab075b3695a1f0bf", "新建商品房销售额累计增长"
            ),
        ),
    ),
    NbsRequest(
        cid="37564a0046c14c059382e3ad26a0d94f",
        frequency="monthly",
        indicators=(
            NbsIndicator("FISCAL_SPEND_CN", "8dc84fd764df46a294e54a7662b1a38a", "财政支出"),
        ),
    ),
    NbsRequest(
        cid="f9b694c9b79e4ce5958bc88c6410fa67",
        frequency="quarterly",
        indicators=(
            NbsIndicator(
                "GDP_CN", "170e7f00f8c24ede863c0526b42ae81f", "国内生产总值指数", _HUNDRED
            ),
        ),
    ),
)


def request_codes() -> set[str]:
    return {item.series_code for request in NBS_REQUESTS for item in request.indicators}


def request_body(request: NbsRequest, today: date) -> bytes:
    if request.frequency == "quarterly":
        quarter = (today.month - 1) // 3 + 1
        window = f"{HISTORY_START_YEAR}01SS-{today.year}0{quarter}SS"
        root = QUARTER_ROOT_ID
    else:
        window = f"{HISTORY_START_YEAR}01MM-{today.year}{today.month:02d}MM"
        root = MONTH_ROOT_ID
    body = {
        "cid": request.cid,
        "indicatorIds": [item.indicator_id for item in request.indicators],
        "daCatalogId": "",
        "das": [NATION],
        "showType": "1",
        "rootId": root,
        "dts": [window],
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _period_start(code: object) -> date | None:
    if not isinstance(code, str):
        return None
    month = _MONTH_CODE.match(code)
    if month is not None:
        number = int(month.group(2))
        return date(int(month.group(1)), number, 1) if 1 <= number <= 12 else None
    quarter = _QUARTER_CODE.match(code)
    if quarter is not None:
        return date(int(quarter.group(1)), (int(quarter.group(2)) - 1) * 3 + 1, 1)
    return None


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = Decimal(value.strip())
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def parse_es_data(payload: bytes, request: NbsRequest) -> tuple[CsvObservation, ...]:
    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise ProviderError(
            code="BAD_PAYLOAD", message="国家统计局返回的不是 JSON", status_code=502
        ) from exc
    periods = body.get("data") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("success") is not True
        or not isinstance(periods, list)
    ):
        raise ProviderError(
            code="UPSTREAM_REJECTED", message="国家统计局没有给出数据", status_code=502
        )
    by_id = {item.indicator_id: item for item in request.indicators}
    observations: list[CsvObservation] = []
    for period in periods:
        if not isinstance(period, dict):
            continue
        observed = _period_start(period.get("code"))
        values = period.get("values")
        if observed is None or not isinstance(values, list):
            continue
        for entry in values:
            if not isinstance(entry, dict):
                continue
            indicator = by_id.get(str(entry.get("_id")))
            if indicator is None:
                continue
            name = entry.get("i_showname")
            if not isinstance(name, str) or indicator.expect not in name:
                raise ProviderError(
                    code="BAD_PAYLOAD",
                    message=(
                        f"{indicator.series_code} 的指标名与预期不符（{name!r}），"
                        "国家统计局目录可能改版，已停止写入"
                    ),
                    status_code=502,
                )
            value = _decimal(entry.get("value"))
            if value is None:
                continue
            observations.append(
                CsvObservation(
                    series_code=indicator.series_code,
                    observation_date=observed,
                    value=value - indicator.offset,
                    unit=indicator.unit,
                    source=SOURCE_NAME,
                    published_at=published_upper_bound(indicator.series_code, observed),
                )
            )
    return tuple(observations)


async def fetch_nbs(
    client: ResilientHttpClient, request: NbsRequest, today: date
) -> tuple[CsvObservation, ...]:
    payload = await client.post(ES_DATA_URL, content=request_body(request, today), headers=HEADERS)
    return parse_es_data(payload, request)
