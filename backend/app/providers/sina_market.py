"""新浪财经公开行情：行业 / 概念板块涨幅、沪深 A 股排行、日经 225 与外盘期货。

只做 URL 构造与解析。东财 push2 被频控时作为后备源；页面上按实际来源标注。

口径（2026-10 实测）：
- 板块 `newSinaHy.php`（行业）/ `newFLJK.php?param=class`（概念）：GBK 的 JS 对象，值为逗号串
  `代码,名称,家数,均价,涨跌额,涨跌幅%,成交量,成交额(元),领涨代码,领涨涨幅%,领涨价,领涨涨跌,领涨名称`。
  板块涨跌幅为成分等权均值。
- 排行 `Market_Center.getHQNodeData`：JSON，changepercent 为 %，amount 为元，per 为动态市盈率。
- `hq.sinajs.cn`：`znb_NKY` 1 最新价、2 涨跌额；`hf_GC`/`hf_CL` 0 最新价、7 昨结算、12 日期。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import cast

from app.providers.errors import ProviderError

INDUSTRY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
CONCEPT_URL = "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"
HQ_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://finance.sina.com.cn/",
}
_RANK_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_HQ_LINE = re.compile(r'hq_str_(\w+)="([^"]*)"')


@dataclass(frozen=True)
class SinaBoard:
    code: str
    name: str
    members: int
    change_pct: Decimal | None
    amount: Decimal | None
    leader_code: str | None
    leader_name: str | None
    leader_change_pct: Decimal | None


@dataclass(frozen=True)
class SinaStock:
    code: str
    name: str
    last: Decimal | None
    change_pct: Decimal | None
    amount: Decimal | None
    turnover_pct: Decimal | None
    pe_dynamic: Decimal | None


@dataclass(frozen=True)
class SinaQuote:
    symbol: str
    last: Decimal | None
    prev_close: Decimal | None
    quote_time: str


@dataclass(frozen=True)
class FuturesBar:
    """外盘期货主连日 K（价格单位同合约报价）。新浪全球期货日 K 的成交量恒为 0，视为没有。"""

    day: str
    open: float
    high: float
    low: float
    close: float


_FUTURES_DAILY_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
    "GlobalFuturesService.getGlobalFuturesDailyKLine"
)


def futures_daily_url(symbol: str) -> str:
    """外盘期货主连日 K（`GC` COMEX 黄金、`CL` NYMEX 原油）；一次返回全部历史，由调用方截取。"""

    return f"{_FUTURES_DAILY_URL}?symbol={symbol}"


def parse_futures_daily(payload: bytes) -> list[FuturesBar]:
    """`/*…*/ var t=([{"date","open","high","low","close",…},…]);`，按日期升序。"""

    text = payload.decode("utf-8", errors="replace")
    start, end = text.find("[{"), text.rfind("}]")
    if start < 0 or end < start:
        raise _payload_error("期货日K")
    try:
        rows = cast(object, json.loads(text[start : end + 2]))
    except json.JSONDecodeError as exc:
        raise _payload_error("期货日K") from exc
    if not isinstance(rows, list):
        raise _payload_error("期货日K")
    bars: list[FuturesBar] = []
    for raw in cast(list[object], rows):
        if not isinstance(raw, dict):
            continue
        row = cast(dict[str, object], raw)
        day = row.get("date")
        values = [_number(row.get(key)) for key in ("open", "high", "low", "close")]
        if not isinstance(day, str) or any(value is None for value in values):
            continue
        open_, high, low, close = (float(value) for value in values if value is not None)
        bars.append(FuturesBar(day, open_, high, low, close))
    return sorted(bars, key=lambda bar: bar.day)


@dataclass(frozen=True)
class FuturesMinute:
    """外盘期货 1 分钟分时点；`clock` 为北京时间 HH:MM，`day` 为该点的北京日期。

    `avg` 为上游给的均价；`volume` 在上游全程为 0 时为 None（不当成 0 去画量柱）。
    """

    clock: str
    day: str
    price: float
    avg: float | None
    volume: float | None


_FUTURES_MINUTE_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
    "GlobalFuturesService.getGlobalFuturesMinLine"
)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CLOCK = re.compile(r"^\d{2}:\d{2}$")


def futures_minute_url(symbol: str) -> str:
    """外盘期货主连 1 分钟分时（`GC` COMEX 黄金、`CL` NYMEX 原油），自当日开盘起，北京时间。"""

    return f"{_FUTURES_MINUTE_URL}?symbol={symbol}"


def parse_futures_minute(payload: bytes) -> list[FuturesMinute]:
    """`/*<script>…</script>*/ var t=({"minLine_1d":[…]});`，保持数组顺序（会跨自然日）。

    第 0 行 10 项：`日期,昨结,交易所,"",HH:MM,价,量,?,均价,完整时间`；其余 6 项：
    `HH:MM,价,量,?,均价,完整时间`。
    """

    text = _COMMENT.sub("", payload.decode("utf-8", errors="replace"))
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise _payload_error("期货分时")
    try:
        document = cast(object, json.loads(text[start : end + 1]))
    except json.JSONDecodeError as exc:
        raise _payload_error("期货分时") from exc
    rows = (
        cast(dict[str, object], document).get("minLine_1d") if isinstance(document, dict) else None
    )
    if not isinstance(rows, list):
        raise _payload_error("期货分时")
    parsed: list[tuple[str, str, float, float | None, float | None]] = []
    for raw in cast(list[object], rows):
        if not isinstance(raw, list):
            continue
        row = [str(item) for item in cast(list[object], raw)]
        if len(row) == 10:
            clock, price, volume, avg, stamp = row[4], row[5], row[6], row[8], row[9]
        elif len(row) == 6:
            clock, price, volume, avg, stamp = row[0], row[1], row[2], row[4], row[5]
        else:
            continue
        last = _number(price)
        if not _CLOCK.match(clock) or last is None or last <= 0 or len(stamp) < 10:
            continue
        average, quantity = _number(avg), _number(volume)
        parsed.append(
            (
                clock,
                stamp[:10],
                float(last),
                float(average) if average is not None and average > 0 else None,
                float(quantity) if quantity is not None and quantity > 0 else None,
            )
        )
    if not parsed:
        raise _payload_error("期货分时")
    has_volume = any(item[4] is not None for item in parsed)
    return [
        FuturesMinute(clock, day, price, avg, (volume or 0.0) if has_volume else None)
        for clock, day, price, avg, volume in parsed
    ]


def rank_url(sort: str, count: int) -> str:
    if sort not in ("changepercent", "amount"):
        raise ProviderError(code="VALIDATION_ERROR", message="不支持的排序字段", status_code=422)
    return f"{_RANK_URL}?page=1&num={count}&sort={sort}&asc=0&node=hs_a&symbol=&_s_r_a=page"


def hq_url(symbols: list[str] | tuple[str, ...]) -> str:
    return HQ_URL + ",".join(symbols)


def parse_boards(payload: bytes) -> tuple[SinaBoard, ...]:
    text = payload.decode("gbk", errors="replace")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise _payload_error("板块")
    try:
        document = cast(object, json.loads(text[start : end + 1]))
    except json.JSONDecodeError as exc:
        raise _payload_error("板块") from exc
    if not isinstance(document, dict):
        raise _payload_error("板块")
    boards: list[SinaBoard] = []
    for value in cast(dict[str, object], document).values():
        parts = str(value).split(",")
        if len(parts) < 13 or not parts[2].isdigit():
            continue
        leader = parts[8].strip()
        boards.append(
            SinaBoard(
                code=parts[0],
                name=parts[1].strip(),
                members=int(parts[2]),
                change_pct=_number(parts[5]),
                amount=_number(parts[7]),
                leader_code=leader[2:] if len(leader) == 8 else None,
                leader_name=parts[12].replace(" ", "") or None,
                leader_change_pct=_number(parts[9]),
            )
        )
    return tuple(boards)


def parse_rank(payload: bytes) -> tuple[SinaStock, ...]:
    try:
        document = cast(object, json.loads(payload.decode("utf-8", errors="replace")))
    except json.JSONDecodeError as exc:
        raise _payload_error("排行") from exc
    if not isinstance(document, list):
        raise _payload_error("排行")
    rows: list[SinaStock] = []
    for raw in cast(list[object], document):
        if not isinstance(raw, dict):
            continue
        row = cast(dict[str, object], raw)
        code, name = row.get("code"), row.get("name")
        if not isinstance(code, str) or not isinstance(name, str):
            continue
        rows.append(
            SinaStock(
                code=code,
                name=name.replace(" ", ""),
                last=_number(row.get("trade")),
                change_pct=_number(row.get("changepercent")),
                amount=_number(row.get("amount")),
                turnover_pct=_number(row.get("turnoverratio")),
                pe_dynamic=_number(row.get("per")),
            )
        )
    return tuple(rows)


def parse_hq(payload: bytes) -> dict[str, SinaQuote]:
    quotes: dict[str, SinaQuote] = {}
    for symbol, body in _HQ_LINE.findall(payload.decode("gbk", errors="replace")):
        parts = body.split(",")
        if symbol.startswith("znb_") and len(parts) > 7:
            last, change = _number(parts[1]), _number(parts[2])
            prev = None if last is None or change is None else last - change
            quotes[symbol] = SinaQuote(symbol, last, prev, f"{parts[6]} {parts[7]}")
        elif symbol.startswith("hf_") and len(parts) > 12:
            quotes[symbol] = SinaQuote(
                symbol, _number(parts[0]), _number(parts[7]), f"{parts[12]} {parts[6]}"
            )
    return quotes


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or text in ("-", "--"):
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _payload_error(label: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message=f"无法解析新浪{label}响应", status_code=502
    )
