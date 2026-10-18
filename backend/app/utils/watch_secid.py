"""东财 secid（市场编号.代码）的解析、分类与展示名。

自选里除了沪深京 A 股个股，用户还会收藏行业板块、全球指数、美股、国债收益率、期货、
ETF 等。它们没有对应的本系统证券记录，就按东财 secid 原样保存（watchlist_items.ext_secid），
行情、名称由东财现查。这里只做无副作用的字符串规则，方便离线测试。
"""

from __future__ import annotations

import re

# 东财市场编号 -> 类别。未列出的编号一律归为 other，不猜。
_MARKET_KIND: dict[int, str] = {
    90: "board",
    100: "index",
    124: "index",
    251: "index",
    133: "fx",
    101: "futures",
    102: "futures",
    112: "futures",
    171: "bond_yield",
    105: "us_stock",
    106: "us_stock",
    107: "us_stock",
    116: "hk_stock",
}

KIND_LABELS: dict[str, str] = {
    "cn_stock": "A股",
    "fund": "基金/ETF",
    "bond": "债券",
    "board": "板块",
    "index": "指数",
    "fx": "外汇",
    "futures": "期货",
    "bond_yield": "国债收益率",
    "us_stock": "美股",
    "hk_stock": "港股",
    "other": "其他",
}

# 东财给美股的三个市场编号，对应本系统 instruments.exchange。
US_MARKET_BY_EXCHANGE: dict[str, int] = {"NASDAQ": 105, "NYSE": 106, "AMEX": 107}

_SECID = re.compile(r"^(\d{1,3})\.([A-Za-z0-9_\-]{1,20})$")
_A_CODE = re.compile(r"^\d{6}$")


def parse_secid(token: str) -> tuple[int, str] | None:
    """'105.NVDA' -> (105, 'NVDA')；格式不对返回 None。"""

    match = _SECID.match(token.strip())
    if match is None:
        return None
    return int(match.group(1)), match.group(2).upper()


def make_secid(market: int, code: str) -> str:
    return f"{market}.{code.upper()}"


def fund_market(code: str) -> int | None:
    """没写前缀的 6 位基金 / 债券代码，按号段推断沪(1)深(0)；推断不了返回 None。"""

    if not _A_CODE.match(code):
        return None
    if code.startswith(("5", "11")):
        return 1
    if code.startswith(("15", "16", "12")):
        return 0
    return None


def kind_of(market: int, code: str) -> str:
    """按市场编号和代码判断类别，用于界面分组与配色。"""

    if market in (0, 1) and _A_CODE.match(code):
        if market == 1:
            if code.startswith("6"):
                return "cn_stock"
            if code.startswith("5"):
                return "fund"
            if code.startswith("11"):
                return "bond"
            if code.startswith("000"):
                return "index"
            return "other"
        if code.startswith("399"):
            return "index"
        if code.startswith(("15", "16")):
            return "fund"
        if code.startswith("12"):
            return "bond"
        if code.startswith(("0", "3", "4", "8", "9")):
            return "cn_stock"
        return "other"
    return _MARKET_KIND.get(market, "other")


def kind_of_secid(secid: str) -> str:
    parsed = parse_secid(secid)
    return "other" if parsed is None else kind_of(*parsed)


def kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, KIND_LABELS["other"])


def display_symbol(secid: str) -> str:
    """A 股与基金用 600519.SH 写法，其余市场直接用东财代码。"""

    parsed = parse_secid(secid)
    if parsed is None:
        return secid
    market, code = parsed
    if market in (0, 1) and _A_CODE.match(code):
        suffix = "SH" if market == 1 else ("BJ" if code.startswith(("4", "8", "92")) else "SZ")
        return f"{code}.{suffix}"
    return code


def a_share_secid(exchange: str, code: str) -> str:
    """本系统 (交易所, 6 位代码) -> 东财 secid。"""

    return make_secid(1 if exchange == "SSE" else 0, code)
