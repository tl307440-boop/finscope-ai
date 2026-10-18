"""中证指数官网日行情接口：收盘价与滚动市盈率（PE-TTM）。

`https://www.csindex.com.cn/csindex-home/perf/index-perf`
（2026-10 实测，沪深300 可回溯到 2015 年以前）：
- `close`：指数收盘点位；
- `peg`：字段名虽叫 peg，实际是中证指数公司披露的**滚动市盈率**（沪深300 约 12–14）。

这份日历同时拿来当 A 股交易日历：返回了哪几天，那几天就是交易日。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode

from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError

SOURCE = "csindex_index_perf"
HEADERS = {
    "Referer": "https://www.csindex.com.cn/",
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Accept": "application/json",
}
_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


@dataclass(frozen=True)
class IndexDay:
    trade_date: date
    index_code: str
    close: Decimal | None
    pe_ttm: Decimal | None


def index_perf_url(index_code: str, start: date, end: date) -> str:
    query = urlencode(
        {
            "indexCode": index_code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        }
    )
    return f"{_URL}?{query}"


def parse_index_perf(payload: bytes, index_code: str) -> tuple[IndexDay, ...]:
    try:
        decoded = cast(object, json.loads(payload, parse_float=Decimal))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    document = cast(dict[str, object], decoded)
    if document.get("code") not in (None, "200", 200):
        raise _payload_error()
    rows = document.get("data")
    if rows is None:
        return ()
    if not isinstance(rows, list):
        raise _payload_error()
    parsed: dict[date, IndexDay] = {}
    for item in cast(list[object], rows):
        if not isinstance(item, dict):
            raise _payload_error()
        row = cast(dict[str, object], item)
        raw_day = row.get("tradeDate")
        if not isinstance(raw_day, str) or len(raw_day) != 8 or not raw_day.isdigit():
            raise _payload_error()
        day = date(int(raw_day[:4]), int(raw_day[4:6]), int(raw_day[6:]))
        if row.get("indexCode") not in (None, index_code):
            # 返回了别的指数，说明参数被忽略；整批作废，免得把别的指数存到这个代码下。
            raise _payload_error()
        parsed[day] = IndexDay(
            trade_date=day,
            index_code=index_code,
            close=em._number(row.get("close")),
            pe_ttm=em._number(row.get("peg")),
        )
    return tuple(parsed[day] for day in sorted(parsed))


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message="无法解析中证指数日行情响应", status_code=502
    )
