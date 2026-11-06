"""腾讯行情适配器：东财 push2 / push2his 在部分机房连不上时的备用通道。

生产服务器（阿里云杭州）实测：`push2.eastmoney.com`、`push2his.eastmoney.com` 及其数字镜像
全部连接失败，而 `qt.gtimg.cn`、`web.ifzq.gtimg.cn` 稳定返回 200。

腾讯给什么、不给什么（如实标注，缺的字段保持为空，不倒推）：

- 报价 `qt.gtimg.cn/q=`：A 股 / 指数 / 港股指数 / 美股指数的现价、涨跌额、涨跌幅；
  A 股与 A 股指数另有最高 / 最低 / 昨收 / 振幅 / 成交额（万元）/ 换手率 / 流通市值（亿元）。
  外盘期货 `hf_GC`（COMEX 黄金）、`hf_CL`（NYMEX 原油）是逗号分隔的另一种格式。没有行业、概念。
- 日 K `newfqkline`：成交量（手）与成交额（万元，指数含北证 50；个股前复权行可能没有）。
- 分时 `minute/query`（A 股，仅当日，每分钟带累计成交额）、`hkMinute`（港股）、`UsMinute`（美股，
  只有累计量没有成交额）。
- 五日分时 `day/query`：最近 5 个交易日的分时，上证 / 深证系指数带累计成交额，北证只有累计量。
- 榜单 `getBoardRankList`（A 股涨幅 / 振幅榜）、`getRank`（概念板块，含主力净流入，万元）。
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
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get"
_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
_HK_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/hkMinute/query"
_US_MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/UsMinute/query"
_FIVE_DAY_URL = "https://web.ifzq.gtimg.cn/appstock/app/day/query"
_RANK_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
_CONCEPT_URL = "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank"

# 东财的 secid（市场.代码）里海外指数、商品期货在腾讯的写法。
_OVERSEAS = {
    "100.HSI": "hkHSI",
    "100.NDX": "usNDX",
    "100.SPX": "usINX",
    "101.GC00Y": "hf_GC",
    "102.CL00Y": "hf_CL",
}
# 美股指数的分时接口要带交易所后缀。
_US_MINUTE = {"usNDX": "usNDX.OQ", "usINX": "usINX.N"}
_WAN = 1e4
_YI = 1e8


def tencent_code(secid: str) -> str | None:
    """东财 secid → 腾讯代码；没有对应写法（港股个股等）返回 None。"""

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


def a_share_code(code: str) -> str:
    """6 位 A 股代码 → 腾讯代码（sh / sz / bj）。"""

    if code.startswith("6"):
        return f"sh{code}"
    if code.startswith(("8", "4", "92")):
        return f"bj{code}"
    return f"sz{code}"


def quote_url(codes: Sequence[str]) -> str:
    return _QUOTE_URL + ",".join(codes)


def _number(value: object) -> float | None:
    try:
        return float(str(value))
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
    prev_close: float | None = None
    high: float | None = None
    low: float | None = None
    amplitude_pct: float | None = None


def parse_quotes(payload: bytes) -> dict[str, Quote]:
    """`v_sh000001="1~上证指数~000001~3649.18~…";` 逐行解析，键为腾讯代码（如 `sh000001`）。"""

    text = payload.decode("gbk", errors="replace")
    out: dict[str, Quote] = {}
    for line in text.splitlines():
        head, _, body = line.partition('="')
        if not head.startswith("v_") or not body:
            continue
        local = head[2:]
        if local.startswith("hf_"):
            future = _future(local, body.rstrip('";'))
            if future is not None:
                out[local] = future
            continue
        fields = body.rstrip('";').split("~")
        if len(fields) < 33:
            continue
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
            prev_close=_number(fields[4]),
            high=_number(fields[33]),
            low=_number(fields[34]),
            amplitude_pct=_scaled(fields, 43, 1.0) if is_cn else None,
        )
    return out


def _future(local: str, body: str) -> Quote | None:
    """外盘期货：`现价,涨跌幅%,买,卖,最高,最低,时间,昨结,开盘,…,日期,名称`。

    涨跌额 = 现价 − 昨结。
    """

    fields = body.split(",")
    if len(fields) < 13:
        return None
    price, pct, prev = _number(fields[0]), _number(fields[1]), _number(fields[7])
    if price is None:
        return None
    return Quote(
        code=local[3:],
        name=fields[13] if len(fields) > 13 else local[3:],
        price=price,
        change=None if prev is None else round(price - prev, 4),
        change_pct=pct,
        amount_yuan=None,
        turnover_pct=None,
        float_cap_yuan=None,
        prev_close=prev,
        high=_number(fields[4]),
        low=_number(fields[5]),
    )


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
        "f7": quote.amplitude_pct,
        "f8": quote.turnover_pct,
        "f12": code,
        "f13": int(market) if market.isdigit() else market,
        "f14": quote.name,
        "f15": quote.high,
        "f16": quote.low,
        "f18": quote.prev_close,
        "f21": quote.float_cap_yuan,
    }
    return {key: value for key, value in row.items() if value is not None}


# ───────────── 日 K ─────────────


@dataclass(frozen=True)
class DayBar:
    day: str
    open: float
    close: float
    high: float
    low: float
    volume: float  # 手
    amount_yuan: float | None


def kline_url(code: str, limit: int, adjust: str = "") -> str:
    """日 K；`adjust` 为 "" 不复权（指数、成交额用）或 "qfq" 前复权（个股涨跌幅用）。"""

    return f"{_KLINE_URL}?{urlencode({'param': f'{code},day,,,{limit},{adjust}'})}"


def parse_kline_days(payload: bytes, code: str) -> list[str]:
    """日 K 的交易日列表（升序）；盘中最后一天就是今天。"""

    return sorted({bar.day for bar in parse_daily(payload, code)})


def parse_daily(payload: bytes, code: str) -> list[DayBar]:
    """行：`[日期, 开, 收, 高, 低, 成交量(手), {}, 换手, 成交额(万元), …]`；按日期升序。"""

    node = _data_node(payload, code)
    rows = node.get("day") or node.get("qfqday")
    if not isinstance(rows, list):
        raise _payload_error()
    bars: list[DayBar] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        values = [_number(row[i]) for i in range(1, 6)]
        if any(v is None for v in values):
            continue
        open_, close, high, low, volume = (float(v) for v in values if v is not None)
        amount = _number(row[8]) if len(row) > 8 else None
        bars.append(
            DayBar(
                day=str(row[0]),
                open=open_,
                close=close,
                high=high,
                low=low,
                volume=volume,
                amount_yuan=None if amount is None else amount * _WAN,
            )
        )
    return sorted(bars, key=lambda bar: bar.day)


# ───────────── 分时 ─────────────


@dataclass(frozen=True)
class MinuteBar:
    clock: str  # HH:MM
    price: float
    volume: float | None  # 当日累计成交量（手；港股指数为股，口径不同）
    amount_yuan: float | None  # 当日累计成交额，没有就为空


@dataclass(frozen=True)
class Minutes:
    day: str  # YYYY-MM-DD
    bars: tuple[MinuteBar, ...]

    @property
    def total_amount(self) -> float | None:
        amounts = [bar.amount_yuan for bar in self.bars if bar.amount_yuan is not None]
        return amounts[-1] if amounts else None

    def amount_until(self, clock: str) -> float | None:
        """截至 `clock`（含）的累计成交额。"""

        picked = [
            bar.amount_yuan
            for bar in self.bars
            if bar.clock <= clock and bar.amount_yuan is not None
        ]
        return picked[-1] if picked else None


def minute_code(code: str) -> str:
    """分时接口里的代码：美股指数要带交易所后缀。"""

    return _US_MINUTE.get(code, code)


def minute_url(code: str) -> str:
    if code.startswith("hk"):
        return f"{_HK_MINUTE_URL}?{urlencode({'code': code})}"
    if code.startswith("us"):
        return f"{_US_MINUTE_URL}?{urlencode({'code': minute_code(code)})}"
    return f"{_MINUTE_URL}?{urlencode({'code': code})}"


def parse_minutes(payload: bytes, code: str) -> Minutes:
    node = _data_node(payload, minute_code(code))
    inner = node.get("data")
    if not isinstance(inner, dict):
        raise _payload_error()
    return _minutes_of(inner)


def five_day_url(code: str) -> str:
    return f"{_FIVE_DAY_URL}?{urlencode({'code': code})}"


def parse_five_day(payload: bytes, code: str) -> list[Minutes]:
    """最近 5 个交易日的分时，按日期升序。"""

    node = _data_node(payload, code)
    days = node.get("data")
    if not isinstance(days, list):
        raise _payload_error()
    out = [_minutes_of(item) for item in days if isinstance(item, dict)]
    return sorted(out, key=lambda m: m.day)


def _minutes_of(inner: Mapping[str, Any]) -> Minutes:
    raw_day = str(inner.get("date", ""))
    if len(raw_day) != 8 or not raw_day.isdigit():
        raise _payload_error()
    bars: list[MinuteBar] = []
    for row in inner.get("data") or []:
        parts = str(row).split()
        if len(parts) < 3 or len(parts[0]) != 4:
            continue
        price = _number(parts[1])
        if price is None:
            continue
        amount = _number(parts[3]) if len(parts) > 3 else None
        bars.append(MinuteBar(f"{parts[0][:2]}:{parts[0][2:]}", price, _number(parts[2]), amount))
    return Minutes(f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}", tuple(bars))


# ───────────── 榜单 ─────────────


@dataclass(frozen=True)
class RankRow:
    code: str
    name: str
    change_pct: float | None
    amplitude_pct: float | None
    net_inflow_wan: float | None  # 主力净流入（万元，仅概念板块榜）


def rank_url(sort: str, offset: int, count: int) -> str:
    """A 股榜单；`sort` 取 `priceRatio`（涨跌幅）或 `amplitude`（振幅），降序，单页最多 200。"""

    query = {
        "board_code": "aStock",
        "sort_type": sort,
        "direct": "down",
        "offset": str(offset),
        "count": str(count),
    }
    return f"{_RANK_URL}?{urlencode(query)}"


def concept_rank_url(offset: int, count: int) -> str:
    """概念板块榜（按涨跌幅降序），带主力净流入；单页最多 200。"""

    query = {
        "board_type": "gn",
        "sort_type": "priceRatio",
        "direct": "down",
        "offset": str(offset),
        "count": str(count),
    }
    return f"{_CONCEPT_URL}?{urlencode(query)}"


def parse_rank(payload: bytes) -> tuple[int, list[RankRow]]:
    """返回 (榜单总数, 本页行)。"""

    try:
        doc = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(doc, dict) or doc.get("code") not in (0, "0"):
        raise _payload_error()
    data = doc.get("data")
    rows = data.get("rank_list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise _payload_error()
    total = _number(data.get("total", len(rows))) if isinstance(data, dict) else None
    out = [
        RankRow(
            code=str(row.get("code", "")),
            name=str(row.get("name", "")),
            change_pct=_number(row.get("zdf")),
            amplitude_pct=_number(row.get("zf")),
            net_inflow_wan=_number(row.get("zljlr")) if "zljlr" in row else None,
        )
        for row in rows
        if isinstance(row, dict)
    ]
    return int(total if total is not None else len(out)), out


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
