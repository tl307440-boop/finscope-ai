from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlencode

from app.providers.errors import ProviderError
from app.providers.quality import Adjustment, RawBar

AdjustmentCode = Literal["none", "qfq", "hfq"]
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"
_ADJUSTMENT_CODE = {"none": "0", "qfq": "1", "hfq": "2"}
_EXCHANGE_MARKET = {"SSE": "1", "SZSE": "0"}


@dataclass(frozen=True)
class DailyTarget:
    display_symbol: str
    exchange: str
    symbol: str
    name_cn: str


DAILY_TARGETS: tuple[DailyTarget, ...] = (
    DailyTarget("600519.SH", "SSE", "600519", "贵州茅台"),
    DailyTarget("601318.SH", "SSE", "601318", "中国平安"),
    DailyTarget("000858.SZ", "SZSE", "000858", "五粮液"),
    DailyTarget("000001.SH", "SSE", "000001", "上证指数"),
    DailyTarget("399001.SZ", "SZSE", "399001", "深证成指"),
    DailyTarget("399006.SZ", "SZSE", "399006", "创业板指"),
    DailyTarget("000300.SH", "SSE", "000300", "沪深300"),
)

# 交易所由代码后缀决定，不靠前缀猜：600519.SH 在上交所，000858.SZ 在深交所，
# 而 000001.SH 是上证指数、000001.SZ 是平安银行——同一串数字分属两个市场。
_SUFFIX_EXCHANGE = {"SH": "SSE", "SZ": "SZSE"}
_KNOWN_NAMES = {target.display_symbol: target.name_cn for target in DAILY_TARGETS}

# 代码格式不合法时统一用这句话，避免多处各写一份说明。
UNSUPPORTED_SYMBOL_MESSAGE = "只支持 600519.SH / 000858.SZ 这类六位代码加交易所后缀"


def target_for(display_symbol: str) -> DailyTarget | None:
    """把 `600519.SH` 形式的代码解析成抓取目标。

    形如 `NNNNNN.SH` / `NNNNNN.SZ` 的代码一律可抓，不再限制在固定几只；
    代码格式不合法（位数不对、市场后缀不认识）才返回 None。
    """
    code = display_symbol.strip().upper()
    symbol, _, suffix = code.partition(".")
    exchange = _SUFFIX_EXCHANGE.get(suffix)
    if exchange is None or len(symbol) != 6 or not symbol.isdigit():
        return None
    return DailyTarget(code, exchange, symbol, _KNOWN_NAMES.get(code, ""))


def secid_for(exchange: str, symbol: str) -> str:
    market = _EXCHANGE_MARKET.get(exchange)
    if market is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="日线抓取只支持 SSE 与 SZSE 两个交易所",
            status_code=422,
        )
    return f"{market}.{symbol}"


def kline_url(
    *,
    exchange: str,
    symbol: str,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> str:
    query = urlencode(
        {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": _EASTMONEY_UT,
            "klt": "101",
            "fqt": _ADJUSTMENT_CODE[adjustment],
            "secid": secid_for(exchange, symbol),
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        }
    )
    return f"{_KLINE_URL}?{query}"


def parse_klines(
    payload: bytes,
    *,
    display_symbol: str,
    adjustment: AdjustmentCode,
) -> tuple[RawBar, ...]:
    import json
    from typing import cast

    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    document = cast(dict[str, object], decoded)
    data = document.get("data")
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise _payload_error()
    body = cast(dict[str, object], data)
    raw_rows = body.get("klines")
    if raw_rows is None:
        return ()
    if not isinstance(raw_rows, list):
        raise _payload_error()
    parsed: list[RawBar] = []
    for raw in raw_rows:
        if not isinstance(raw, str):
            raise _payload_error()
        parsed.append(_one_line(raw, display_symbol=display_symbol, adjustment=adjustment))
    return tuple(parsed)


def dedupe_bars(bars: tuple[RawBar, ...] | list[RawBar]) -> tuple[list[RawBar], int]:
    """同一交易日只保留第一条，避免一次响应里的重复行写成两套价格。"""

    seen: set[date] = set()
    kept: list[RawBar] = []
    skipped = 0
    for bar in bars:
        if bar.trade_date in seen:
            skipped += 1
            continue
        seen.add(bar.trade_date)
        kept.append(bar)
    return kept, skipped


def _one_line(line: str, *, display_symbol: str, adjustment: Adjustment) -> RawBar:
    parts = line.split(",")
    if len(parts) < 7:
        raise _payload_error()
    try:
        trade_date = date.fromisoformat(parts[0])
        open_price = Decimal(parts[1])
        close = Decimal(parts[2])
        high = Decimal(parts[3])
        low = Decimal(parts[4])
        volume = Decimal(parts[5])
        amount = Decimal(parts[6])
    except (ValueError, InvalidOperation) as exc:
        raise _payload_error() from exc
    return RawBar(
        trade_date=trade_date,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=amount,
        adjustment=adjustment,
        currency="CNY",
        unit="price",
        source_record_key=f"akshare|{display_symbol}|{trade_date.isoformat()}|{adjustment}",
    )


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD",
        message="无法解析 AKShare 日线响应",
        status_code=502,
    )
