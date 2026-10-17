"""东方财富数据中心「美国经济数据」报表：目前只取 ISM 制造业 / 服务业 PMI。

只做 URL 构造、解析与一次 GET。
报表 `RPT_ECONOMICVALUE_USA` 每行是一个指标的一个所属月（2026-10 实测）：

- `REPORT_DATE` 所属月首日；`PUBLISH_DATE` 美国当地发布日；`VALUE` 公布值。
- 财经日历会预排下一期：`VALUE` 为 null、`PUBLISH_DATE` 在未来，跳过。
- 只给当前值、不给修订历史；版本由 `latest_revisions` 自首次同步起记录。
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_ECONOMICVALUE_USA"
HEADERS = {"Referer": "https://data.eastmoney.com/"}
PAGE_SIZE = 300  # 2008-01 起约 226 行


@dataclass(frozen=True)
class LatestValue:
    observation_date: date
    published_on: date
    value: Decimal


def indicator_url(indicator_id: str) -> str:
    query = urlencode(
        {
            "reportName": REPORT_NAME,
            "columns": "INDICATOR_ID,REPORT_DATE,PUBLISH_DATE,VALUE",
            "filter": f'(INDICATOR_ID="{indicator_id}")',
            "pageNumber": 1,
            "pageSize": PAGE_SIZE,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        }
    )
    return f"{DATACENTER_URL}?{query}"


def parse_indicator(payload: bytes, indicator_id: str) -> tuple[LatestValue, ...]:
    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise _bad(indicator_id, "返回的不是 JSON") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise _bad(indicator_id, "没有给出数据")
    result = body.get("result")
    if not isinstance(result, dict):
        raise _bad(indicator_id, "缺少数据列表")
    rows = result.get("data")
    if rows is None:
        raise _bad(indicator_id, "没有可用的公布值")
    if not isinstance(rows, list):
        raise _bad(indicator_id, "缺少数据列表")
    points: dict[date, LatestValue] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("INDICATOR_ID") != indicator_id:
            continue
        observed = _day(row.get("REPORT_DATE"))
        published = _day(row.get("PUBLISH_DATE"))
        value = _decimal(row.get("VALUE"))
        if observed is None or published is None or value is None:
            continue
        month = observed.replace(day=1)
        kept = points.get(month)
        if kept is None or published > kept.published_on:
            points[month] = LatestValue(month, published, value)
    if not points:
        raise _bad(indicator_id, "没有可用的公布值")
    return tuple(points[month] for month in sorted(points))


async def fetch_indicator(
    client: ResilientHttpClient, indicator_id: str
) -> tuple[LatestValue, ...]:
    return parse_indicator(
        await client.get(indicator_url(indicator_id), headers=HEADERS), indicator_id
    )


def _bad(indicator_id: str, reason: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message=f"东方财富 {indicator_id} {reason}", status_code=502
    )


def _day(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _decimal(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None
