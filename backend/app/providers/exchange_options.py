"""交易所官方期权日统计：上交所、深交所、中金所。

只做 URL 构造与解析，不发请求。三家口径（2026-10 实测）：

- 上交所 `COMMON_SSE_ZQPZ_YSP_QQ_SJTJ_MRTJ_CX`：按标的给认购 / 认沽成交量与未平仓量，
  `CP_RATE` 是 **成交量** 认沽/认购比（%）。参数必须用 `tradeDate=YYYYMMDD`；
  `TRADE_DATE` 之类的写法会被忽略并返回最新一天，所以解析时再核对一遍日期。
- 深交所 `ysprdzb` / `tab1`：同样按标的给认购 / 认沽成交量与未平仓量，
  `rcrpccb` 是 **持仓量** 认沽/认购比（%），与上交所口径不同。
- 中金所逐日 CSV（`YYYYMMDD_1.csv`，GBK）：期货与期权混在一个文件里，
  期权合约形如 `MO2610-C-6000`；这里按品种 + 认购 / 认沽把成交量与持仓量加总。

数量单位一律是「张 / 手」，PCR 只在同一品种内求比，单位自然抵消。
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast
from urllib.parse import urlencode

from app.providers.errors import ProviderError

Exchange = Literal["SSE", "SZSE", "CFFEX"]
RatioKind = Literal["volume_pcr", "oi_pcr"]

SSE_SOURCE = "sse_option_daily_stats"
SZSE_SOURCE = "szse_option_daily_stats"
CFFEX_SOURCE = "cffex_daily_csv"

SSE_HEADERS = {"Referer": "http://www.sse.com.cn/", "User-Agent": "Mozilla/5.0 (FinScope research)"}
SZSE_HEADERS = {
    "Referer": "https://www.szse.cn/",
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Accept": "application/json",
}
CFFEX_HEADERS = {"User-Agent": "Mozilla/5.0 (FinScope research)"}

_SSE_URL = "http://query.sse.com.cn/commonQuery.do"
_SZSE_URL = "https://www.szse.cn/api/report/ShowReport/data"
_CFFEX_URL = "http://www.cffex.com.cn/sj/hqsj/rtj"
_CFFEX_OPTION = re.compile(r"^(HO|IO|MO)\d{4}-([CP])-\d+$")
_JSONP = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.DOTALL)


@dataclass(frozen=True)
class OptionStat:
    trade_date: date
    exchange: Exchange
    underlying_code: str
    underlying_name: str | None
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    # 交易所自己披露的比值（百分数）与它的口径，仅用于对账；中金所没有。
    reported_ratio: Decimal | None
    reported_ratio_kind: RatioKind | None
    # 交易所披露的总成交量（认购 + 认沽），仅用于对账。
    reported_total_volume: int | None
    source: str


def sse_url(day: date) -> str:
    query = urlencode(
        {
            "jsonCallBack": "cb",
            "isPagination": "false",
            "sqlId": "COMMON_SSE_ZQPZ_YSP_QQ_SJTJ_MRTJ_CX",
            "tradeDate": day.strftime("%Y%m%d"),
            "_": "1",
        }
    )
    return f"{_SSE_URL}?{query}"


def szse_url(day: date) -> str:
    query = urlencode(
        {
            "SHOWTYPE": "JSON",
            "CATALOGID": "ysprdzb",
            "TABKEY": "tab1",
            "txtQueryDate": day.isoformat(),
        }
    )
    return f"{_SZSE_URL}?{query}"


def cffex_url(day: date) -> str:
    stamp = day.strftime("%Y%m%d")
    return f"{_CFFEX_URL}/{stamp[:6]}/{stamp[6:]}/{stamp}_1.csv"


def parse_sse(payload: bytes, day: date) -> tuple[OptionStat, ...]:
    """上交所返回 JSONP。非交易日 / 尚未发布时 result 为空，返回空元组。"""

    text = _utf8(payload, "上交所期权统计")
    matched = _JSONP.match(text.strip())
    if matched is None:
        raise _payload_error("上交所期权统计")
    document = _object(_loads(matched.group(1), "上交所期权统计"), "上交所期权统计")
    rows = document.get("result")
    if rows is None or rows == []:
        return ()
    stats: list[OptionStat] = []
    for item in _list(rows, "上交所期权统计"):
        row = _object(item, "上交所期权统计")
        # 参数写错时上游会悄悄回最新一天，日期对不上就当作没有数据，绝不张冠李戴。
        if _date_text(row.get("TRADE_DATE")) != day:
            return ()
        code = _text(row.get("SECURITY_CODE"))
        if code is None:
            raise _payload_error("上交所期权统计")
        stats.append(
            OptionStat(
                trade_date=day,
                exchange="SSE",
                underlying_code=code,
                underlying_name=_text(row.get("SECURITY_ABBR")),
                call_volume=_count(row.get("CALL_VOLUME")),
                put_volume=_count(row.get("PUT_VOLUME")),
                call_oi=_count(row.get("LEAVES_CALL_QTY")),
                put_oi=_count(row.get("LEAVES_PUT_QTY")),
                reported_ratio=_ratio(row.get("CP_RATE")),
                reported_ratio_kind="volume_pcr",
                reported_total_volume=_count_or_none(row.get("TOTAL_VOLUME")),
                source=SSE_SOURCE,
            )
        )
    return tuple(stats)


def parse_szse(payload: bytes, day: date) -> tuple[OptionStat, ...]:
    document = _loads(_utf8(payload, "深交所期权统计"), "深交所期权统计")
    sections = _list(document, "深交所期权统计")
    if not sections:
        return ()
    section = _object(sections[0], "深交所期权统计")
    rows = section.get("data")
    if not rows:
        # 接口对非法日期返回 error 文案、对非交易日返回空 data；两者都按「没有数据」处理。
        return ()
    stats: list[OptionStat] = []
    for item in _list(rows, "深交所期权统计"):
        row = _object(item, "深交所期权统计")
        code = _text(row.get("bddm"))
        if code is None:
            raise _payload_error("深交所期权统计")
        stats.append(
            OptionStat(
                trade_date=day,
                exchange="SZSE",
                underlying_code=code,
                underlying_name=_text(row.get("bdmc")),
                call_volume=_count(row.get("rccjl")),
                put_volume=_count(row.get("rpcjl")),
                call_oi=_count(row.get("wpcrchys")),
                put_oi=_count(row.get("wpcrphys")),
                reported_ratio=_ratio(row.get("rcrpccb")),
                reported_ratio_kind="oi_pcr",
                reported_total_volume=_count_or_none(row.get("cjl")),
                source=SZSE_SOURCE,
            )
        )
    return tuple(stats)


def parse_cffex(payload: bytes, day: date) -> tuple[OptionStat, ...]:
    """中金所日统计 CSV → 每个期权品种一行（HO 上证50 / IO 沪深300 / MO 中证1000）。"""

    try:
        text = payload.decode("gbk")
    except UnicodeDecodeError as exc:
        raise _payload_error("中金所日统计") from exc
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if header is None or len(header) < 7 or header[0].strip() != "合约代码":
        raise _payload_error("中金所日统计")
    # (品种, C/P) → [成交量, 持仓量]。老文件的合约代码后面带空格，先 strip。
    totals: dict[tuple[str, str], list[int]] = {}
    for row in reader:
        if not row or not row[0].strip():
            continue
        matched = _CFFEX_OPTION.match(row[0].strip())
        if matched is None:
            continue
        if len(row) < 7:
            raise _payload_error("中金所日统计")
        bucket = totals.setdefault((matched.group(1), matched.group(2)), [0, 0])
        bucket[0] += _count(row[4])
        bucket[1] += _count(row[6])
    stats: list[OptionStat] = []
    for product in ("HO", "IO", "MO"):
        call = totals.get((product, "C"))
        put = totals.get((product, "P"))
        if call is None and put is None:
            continue
        call = call or [0, 0]
        put = put or [0, 0]
        stats.append(
            OptionStat(
                trade_date=day,
                exchange="CFFEX",
                underlying_code=product,
                underlying_name=None,
                call_volume=call[0],
                put_volume=put[0],
                call_oi=call[1],
                put_oi=put[1],
                reported_ratio=None,
                reported_ratio_kind=None,
                reported_total_volume=None,
                source=CFFEX_SOURCE,
            )
        )
    return tuple(stats)


def _utf8(payload: bytes, label: str) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _payload_error(label) from exc


def _loads(text: str, label: str) -> object:
    try:
        return cast(object, json.loads(text))
    except json.JSONDecodeError as exc:
        raise _payload_error(label) from exc


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _payload_error(label)
    return cast(dict[str, object], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise _payload_error(label)
    return cast(list[object], value)


def _text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _date_text(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _count_or_none(value: object) -> int | None:
    text = _text(value) if not isinstance(value, int) else str(value)
    if text is None or text in {"-", "--"}:
        return None
    try:
        number = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise _payload_error("期权统计数量") from None
    if number != number.to_integral_value():
        raise _payload_error("期权统计数量")
    return int(number)


def _count(value: object) -> int:
    """数量字段缺失就是上游格式变了，宁可报错也不补 0。"""

    parsed = _count_or_none(value)
    if parsed is None:
        raise _payload_error("期权统计数量")
    return parsed


def _ratio(value: object) -> Decimal | None:
    text = _text(value)
    if text is None or text in {"-", "--"}:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _payload_error(label: str) -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message=f"无法解析{label}响应", status_code=502)
