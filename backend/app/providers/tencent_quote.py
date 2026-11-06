"""腾讯行情适配器：东财 push2 / push2his 在部分机房连不上时的备用通道。

生产服务器（阿里云杭州）实测：`push2.eastmoney.com`、`push2his.eastmoney.com` 及其数字镜像
全部连接失败，而 `qt.gtimg.cn`、`web.ifzq.gtimg.cn` 稳定返回 200。

腾讯给什么、不给什么（如实标注，缺的字段保持为空，不倒推）：

- 报价 `qt.gtimg.cn/q=`：A 股 / 指数 / 港股指数 / 美股指数的现价、涨跌额、涨跌幅；
  A 股与 A 股指数另有成交额（万元）、换手率、流通市值（亿元）。没有行业、没有商品期货。
- 日 K `fqkline`：只有成交量，没有成交额，所以这里只取交易日列表。
- 分时 `minute/query`：只有当日，每分钟带累计成交额（元）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.providers.errors import ProviderError

HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://gu.qq.com/",
}
_QUOTE_URL = "https://qt.gtimg.cn/q="
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"

# 东财的 secid（市场.代码）里这几个海外指数在腾讯的写法。
_OVERSEAS = {"100.HSI": "hkHSI", "100.NDX": "usNDX", "100.SPX": "usINX"}
_WAN = 1e4
_YI = 1e8


def tencent_code(secid: str) -> str | None:
    """东财 secid → 腾讯代码；没有对应写法（商品期货、港股个股等）返回 None。"""

    if secid in _OVERSEAS:
        return _OVERSEAS[secid]
    market, _, code = secid.partition(".")
    if not code.isdigit() or len(code) != 6:
        return None
    if market == "1":
        return f"sh{code}"
    if market == "0":
        if code.startswith(("8", "4", "92")):
            return f"bj{code}"
        return f"sz{code}"
    return None


def quote_url(codes: Sequence[str]) -> str:
    return _QUOTE_URL + ",".join(codes)


def _number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    price: float | None
    change: float | None
    change_pct: float | None
    amount_yuan: float | None
    turnover_pct: float | None
    float_cap_yuan: float | None


def parse_quotes(payload: bytes) -> dict[str, Quote]:
    """`v_sh000001="1~上证指数~000001~3649.18~…";` 逐行解析，键为腾讯代码（如 `sh000001`）。"""

    text = payload.decode("gbk", errors="replace")
    out: dict[str, Quote] = {}
    for line in text.splitlines():
        head, _, body = line.partition('="')
        if not head.startswith("v_") or not body:
            continue
        fields = body.rstrip('";').split("~")
        if len(fields) < 33:
            continue
        local = head[2:]
        is_cn = local[:2] in {"sh", "sz", "bj"}
        out[local] = Quote(
            code=fields[2],
            name=fields[1],
            price=_number(fields[3]),
            change=_number(fields[31]),
            change_pct=_number(fields[32]),
            amount_yuan=_scaled(fields, 37, _WAN) if is_cn else None,
            turnover_pct=_scaled(fields, 38, 1.0) if is_cn else None,
            float_cap_yuan=_scaled(fields, 44, _YI) if is_cn else None,
        )
    return out


def _scaled(fields: Sequence[str], index: int, factor: float) -> float | None:
    if index >= len(fields):
        return None
    value = _number(fields[index])
    return None if value is None else value * factor


def as_em_row(quote: Quote, secid: str) -> dict[str, Any]:
    """转成东财 ulist 行的键名（f2 现价、f3 涨幅、f4 涨跌额、f6 成交额…），让下游不用区分来源。"""

    market, _, code = secid.partition(".")
    row: dict[str, Any] = {
        "f2": quote.price,
        "f3": quote.change_pct,
        "f4": quote.change,
        "f6": quote.amount_yuan,
        "f8": quote.turnover_pct,
        "f12": code,
        "f13": int(market) if market.isdigit() else market,
        "f14": quote.name,
        "f21": quote.float_cap_yuan,
    }
    return {key: value for key, value in row.items() if value is not None}


def kline_url(code: str, limit: int) -> str:
    return f"{_KLINE_URL}?{urlencode({'param': f'{code},day,,,{limit},'})}"


def parse_kline_days(payload: bytes, code: str) -> list[str]:
    """日 K 的交易日列表（升序）；盘中最后一天就是今天。"""

    node = _data_node(payload, code)
    rows = node.get("day")
    if not isinstance(rows, list):
        raise _payload_error()
    days = [str(row[0]) for row in rows if isinstance(row, list) and row]
    return sorted(set(days))


@dataclass(frozen=True)
class MinuteBar:
    clock: str  # HH:MM
    price: float
    amount_yuan: float  # 当日累计成交额


@dataclass(frozen=True)
class Minutes:
    day: str  # YYYY-MM-DD
    bars: tuple[MinuteBar, ...]

    @property
    def total_amount(self) -> float | None:
        return self.bars[-1].amount_yuan if self.bars else None

    def amount_until(self, clock: str) -> float | None:
        """截至 `clock`（含）的累计成交额。"""

        picked = [bar.amount_yuan for bar in self.bars if bar.clock <= clock]
        return picked[-1] if picked else None


def minute_url(code: str) -> str:
    return f"{_MINUTE_URL}?{urlencode({'code': code})}"


def parse_minutes(payload: bytes, code: str) -> Minutes:
    node = _data_node(payload, code)
    inner = node.get("data")
    if not isinstance(inner, dict):
        raise _payload_error()
    raw_day = str(inner.get("date", ""))
    if len(raw_day) != 8 or not raw_day.isdigit():
        raise _payload_error()
    bars: list[MinuteBar] = []
    for row in inner.get("data") or []:
        parts = str(row).split()
        if len(parts) < 4 or len(parts[0]) != 4:
            continue
        price, amount = _number(parts[1]), _number(parts[3])
        if price is None or amount is None:
            continue
        bars.append(MinuteBar(f"{parts[0][:2]}:{parts[0][2:]}", price, amount))
    return Minutes(f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}", tuple(bars))


def _data_node(payload: bytes, code: str) -> dict[str, Any]:
    try:
        doc = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(doc, dict) or doc.get("code") not in (0, "0"):
        raise _payload_error()
    data = doc.get("data")
    node = data.get(code) if isinstance(data, dict) else None
    if not isinstance(node, dict):
        raise _payload_error()
    return node


def _payload_error() -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message="无法解析腾讯行情响应", status_code=502)
