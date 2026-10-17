"""腾讯行情公开接口：指数 / 个股快照、分时、日 K（含成交额）、外汇。

只做 URL 构造与响应解析，不发请求。这些是 gu.qq.com 页面自身匿名调用的接口。

口径（2026-10 实测）：
- `qt.gtimg.cn/q=sh000001,...`：GBK 文本，每行 `v_代码="字段~字段~..."`。
  3 最新价，4 昨收，5 今开，30 时间，32 涨跌幅%，33 最高，34 最低，37 成交额（万元），
  38 换手率%，39 市盈率(TTM)，45 总市值（亿元），46 市净率，52 市盈率(动)。
  深证成指 399001 的成交额即深市全市场成交额（与深证综指一致）。
- `newfqkline`：日 / 周 / 月 K 行 `[日期, 开, 收, 高, 低, 成交量(手), {}, 换手, 成交额(万元)]`，
  不复权；周 K、月 K 的日期是该周期最后一个交易日，当期未走完时为最新交易日。
- `minute/query`：分时行 `"HHMM 价格 累计量 累计额(元)"`。
- 外汇 `whUSDCNY` 字段少于 40 个：3 最新价，12 涨跌，13 涨跌幅%，末位日期。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from app.providers.errors import ProviderError

KlinePeriod = Literal["day", "week", "month"]

QUOTE_URL = "https://qt.gtimg.cn/q="
KLINE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
TENCENT_HEADERS = {"User-Agent": "Mozilla/5.0 (FinScope research)", "Referer": "https://gu.qq.com/"}
_WAN = Decimal(10000)
_YI = Decimal(100_000_000)
_LINE = re.compile(r'v_([A-Za-z0-9_.$]+)="([^"]*)"')
_SYMBOL = re.compile(r"^(sh|sz|bj)\d{6}$")


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    last: Decimal | None
    prev_close: Decimal | None
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    change_pct: Decimal | None
    amount: Decimal | None
    turnover_pct: Decimal | None
    pe_ttm: Decimal | None
    pe_dynamic: Decimal | None
    pb: Decimal | None
    market_cap: Decimal | None
    quote_time: str

    @property
    def quote_date(self) -> date | None:
        """quote_time 形如 20261016161403；非交易日仍是上一交易日的收盘快照。"""

        try:
            return date(
                int(self.quote_time[:4]), int(self.quote_time[4:6]), int(self.quote_time[6:8])
            )
        except ValueError:
            return None


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    amount: Decimal | None


@dataclass(frozen=True)
class MinutePoint:
    clock: str
    price: Decimal
    cumulative_amount: Decimal


@dataclass(frozen=True)
class FxQuote:
    last: Decimal | None
    change_pct: Decimal | None
    quote_date: str


def a_share_symbol(code: str) -> str:
    """6 开头沪市，0/3 开头深市，4/8/92 开头北交所。"""

    if len(code) != 6 or not code.isdigit():
        raise ProviderError(
            code="VALIDATION_ERROR", message="股票代码必须是 6 位数字", status_code=422
        )
    if code.startswith("6"):
        return f"sh{code}"
    if code[0] in "48" or code.startswith("92"):
        return f"bj{code}"
    return f"sz{code}"


def quote_url(symbols: list[str] | tuple[str, ...]) -> str:
    return QUOTE_URL + ",".join(symbols)


def kline_url(symbol: str, count: int, period: KlinePeriod = "day") -> str:
    _check_symbol(symbol)
    return f"{KLINE_URL}?param={symbol},{period},,,{count},"


def minute_url(symbol: str) -> str:
    _check_symbol(symbol)
    return f"{MINUTE_URL}?code={symbol}"


def parse_quotes(payload: bytes) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for symbol, fields in _lines(payload):
        if len(fields) < 46:
            continue
        market_cap = _number(fields[45])
        amount = _number(fields[37])
        quotes[symbol] = Quote(
            symbol=symbol,
            name=fields[1].replace(" ", ""),
            last=_number(fields[3]),
            prev_close=_number(fields[4]),
            open=_number(fields[5]),
            high=_number(fields[33]),
            low=_number(fields[34]),
            change_pct=_number(fields[32]),
            amount=None if amount is None else amount * _WAN,
            turnover_pct=_number(fields[38]),
            pe_ttm=_number(fields[39]),
            pe_dynamic=_number(fields[52]) if len(fields) > 52 else None,
            pb=_number(fields[46]),
            market_cap=None if market_cap is None else market_cap * _YI,
            quote_time=fields[30],
        )
    return quotes


def parse_fx(payload: bytes, symbol: str) -> FxQuote:
    for found, fields in _lines(payload):
        if found == symbol and len(fields) > 13:
            return FxQuote(
                last=_number(fields[3]), change_pct=_number(fields[13]), quote_date=fields[-1]
            )
    raise _payload_error("外汇行情")


def parse_klines(payload: bytes, symbol: str, period: KlinePeriod = "day") -> tuple[DailyBar, ...]:
    document = _object(_decode(payload, "日K"), "日K")
    data = _object(document.get("data"), "日K")
    block = data.get(symbol)
    if block is None:
        return ()
    rows = _object(block, "日K").get(period) or []
    if not isinstance(rows, list):
        raise _payload_error("日K")
    bars: list[DailyBar] = []
    for raw in cast(list[object], rows):
        if not isinstance(raw, list) or len(raw) < 6:
            raise _payload_error("日K")
        row = cast(list[object], raw)
        try:
            day = date.fromisoformat(str(row[0]))
        except ValueError as exc:
            raise _payload_error("日K") from exc
        values = [_number(row[index]) for index in range(1, 6)]
        if any(value is None for value in values):
            continue
        amount = _number(row[8]) if len(row) > 8 else None
        open_, close, high, low, volume = (cast(Decimal, value) for value in values)
        bars.append(
            DailyBar(
                trade_date=day,
                open=open_,
                close=close,
                high=high,
                low=low,
                volume=volume,
                amount=None if amount is None else amount * _WAN,
            )
        )
    return tuple(sorted(bars, key=lambda bar: bar.trade_date))


def parse_minutes(payload: bytes, symbol: str) -> tuple[MinutePoint, ...]:
    document = _object(_decode(payload, "分时"), "分时")
    data = _object(document.get("data"), "分时")
    block = data.get(symbol)
    if block is None:
        return ()
    inner = _object(_object(block, "分时").get("data"), "分时").get("data") or []
    if not isinstance(inner, list):
        raise _payload_error("分时")
    points: list[MinutePoint] = []
    for raw in cast(list[object], inner):
        parts = str(raw).split(" ")
        if len(parts) < 4 or len(parts[0]) != 4:
            raise _payload_error("分时")
        price, amount = _number(parts[1]), _number(parts[3])
        if price is None or amount is None:
            continue
        points.append(MinutePoint(f"{parts[0][:2]}:{parts[0][2:]}", price, amount))
    return tuple(points)


def _lines(payload: bytes) -> list[tuple[str, list[str]]]:
    text = payload.decode("gbk", errors="replace")
    return [(match.group(1), match.group(2).split("~")) for match in _LINE.finditer(text)]


def _check_symbol(symbol: str) -> None:
    if not _SYMBOL.match(symbol):
        raise ProviderError(
            code="VALIDATION_ERROR", message="证券代码形如 sh000001", status_code=422
        )


def _decode(payload: bytes, label: str) -> object:
    try:
        return cast(object, json.loads(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error(label) from exc


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _payload_error(label)
    return cast(dict[str, object], value)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool | dict | list):
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _payload_error(label: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message=f"无法解析腾讯{label}响应", status_code=502
    )
