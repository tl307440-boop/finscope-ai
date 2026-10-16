"""美股日线：Tiingo 官方 EOD 接口（免费档需要个人 API key）。

* 地址：``/tiingo/daily/{ticker}/prices?startDate=&endDate=&token=``，返回 JSON 数组。
* 免费档限额：每小时 50 次、每天 1000 次、每月 500 个不同标的，所以只做增量窗口、
  一次一个标的，并由调用方控制每轮登记数量。
* 不复权（adjustment=none）存原始 open/high/low/close/volume；复权价不存，避免口径混用。
* key 只放在请求地址的 token 参数里，错误信息和日志都不得带出完整地址。
"""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import quote, urlencode

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.quality import RawBar

TIINGO_MIN_INTERVAL_SECONDS = 1.0
_BASE = "https://api.tiingo.com/tiingo/daily"


def ticker_for_tiingo(symbol: str) -> str:
    """Tiingo 用连字符表示类别股（BRK-B）；SEC 表里本来就是连字符，这里只做大小写与转义。"""

    return quote(symbol.strip().lower(), safe="-")


def prices_url(symbol: str, start: date, end: date, token: str) -> str:
    query = urlencode({"startDate": start.isoformat(), "endDate": end.isoformat(), "token": token})
    return f"{_BASE}/{ticker_for_tiingo(symbol)}/prices?{query}"


async def fetch_daily(
    client: ResilientHttpClient, symbol: str, start: date, end: date, token: str
) -> tuple[RawBar, ...]:
    if not token.strip():
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="未配置 TIINGO_API_KEY，美股日线不可用",
            status_code=503,
        )
    payload = await client.get(
        prices_url(symbol, start, end, token),
        headers={"Content-Type": "application/json"},
    )
    return parse_prices(payload, symbol=symbol)


def parse_prices(payload: bytes, *, symbol: str) -> tuple[RawBar, ...]:
    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if isinstance(decoded, dict):
        # 未知代码、限额用尽等错误会返回 {"detail": "..."}。
        detail = cast(dict[str, object], decoded).get("detail")
        raise ProviderError(
            code="NOT_FOUND" if "not found" in str(detail).lower() else "UPSTREAM_UNAVAILABLE",
            message="Tiingo 没有这个代码的日线"
            if "not found" in str(detail).lower()
            else "Tiingo 暂时拒绝了请求（可能是限额用尽）",
            status_code=404 if "not found" in str(detail).lower() else 502,
        )
    if not isinstance(decoded, list):
        raise _payload_error()
    bars: list[RawBar] = []
    for item in decoded:
        if not isinstance(item, dict):
            raise _payload_error()
        record = cast(dict[str, object], item)
        trade_date = _day(record.get("date"))
        prices = [_number(record.get(key)) for key in ("open", "high", "low", "close")]
        volume = _number(record.get("volume"))
        if trade_date is None or volume is None or any(price is None for price in prices):
            continue
        open_, high, low, close = cast(list[Decimal], prices)
        bars.append(
            RawBar(
                trade_date=trade_date,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                amount=None,
                adjustment="none",
                currency="USD",
                unit="price",
                source_record_key=f"tiingo|{symbol.upper()}|{trade_date.isoformat()}|none",
            )
        )
    return tuple(bars)


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _day(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message="无法解析 Tiingo 日线响应", status_code=502
    )
