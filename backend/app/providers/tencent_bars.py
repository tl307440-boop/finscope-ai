"""腾讯行情日线适配器（东方财富的备用通道）。

为什么需要它：push2his.eastmoney.com 对本机已经连续拒绝（直连与走代理都是
`RemoteProtocolError`，60 次尝试 0 成功），而 web.ifzq.gtimg.cn 同一时刻
直连与走代理都稳定 200。两个源给的字段不完全一样——腾讯只给成交量，不给成交额，
所以 amount 留空（见 quality.RawBar.amount 的说明），界面渲染「—」，不做倒推。

腾讯日线数组顺序是 `[日期, 开, 收, 高, 低, 成交量(手)]`，和东方财富
`klines` 的前六段一致，但没有第七段成交额。
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.parse import urlencode

from app.providers.akshare_bars import AdjustmentCode, DailyTarget
from app.providers.errors import ProviderError
from app.providers.quality import Adjustment, RawBar

_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_MARKET_PREFIX = {"SSE": "sh", "SZSE": "sz"}
# 腾讯单次最多返回约 640 根；日线一段最多这么多交易日。
MAX_BARS = 640
# 请求的 key 与响应里的数组名都随复权方式变化。
_ADJUSTMENT_KEY: dict[AdjustmentCode, str] = {"none": "", "qfq": "qfq", "hfq": "hfq"}


def market_code(exchange: str, symbol: str) -> str:
    prefix = _MARKET_PREFIX.get(exchange)
    if prefix is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="日线抓取只支持 SSE 与 SZSE 两个交易所",
            status_code=422,
        )
    return f"{prefix}{symbol}"


def bar_count(start: date, end: date) -> int:
    """按自然日估算请求根数：一周约 5 个交易日，留一倍余量再截到上限。"""
    span = (end - start).days
    guess = max(1, span * 5 // 7 + 20)
    return min(MAX_BARS, guess)


def kline_url(
    *,
    exchange: str,
    symbol: str,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> str:
    param = ",".join(
        (
            market_code(exchange, symbol),
            "day",
            start.isoformat(),
            end.isoformat(),
            str(bar_count(start, end)),
            _ADJUSTMENT_KEY[adjustment],
        )
    )
    return f"{_KLINE_URL}?{urlencode({'param': param})}"


def parse_klines(
    payload: bytes,
    *,
    target: DailyTarget,
    adjustment: AdjustmentCode,
) -> tuple[RawBar, ...]:
    try:
        decoded: object = json.loads(payload, parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    if not isinstance(decoded, dict):
        raise _payload_error()
    if cast(dict[str, object], decoded).get("code") not in (0, "0"):
        raise _payload_error()
    data = cast(dict[str, object], decoded).get("data")
    if data is None:
        return ()
    if not isinstance(data, dict):
        raise _payload_error()
    node = cast(dict[str, object], data).get(market_code(target.exchange, target.symbol))
    if node is None:
        # 代码不存在时腾讯返回 data:{"sh600519":""} 这类空壳，按「没有数据」处理。
        return ()
    if not isinstance(node, dict):
        return ()
    rows = _rows_of(cast(dict[str, object], node), adjustment)
    if rows is None:
        return ()
    return tuple(
        _one_row(row, display_symbol=target.display_symbol, adjustment=adjustment) for row in rows
    )


def _rows_of(node: dict[str, object], adjustment: AdjustmentCode) -> list[object] | None:
    """取出日线数组：不复权是 `day`，前复权是 `qfqday`，后复权是 `hfqday`。"""
    preferred = f"{_ADJUSTMENT_KEY[adjustment]}day"
    candidate = node.get(preferred)
    if isinstance(candidate, list):
        return cast(list[object], candidate)
    for key, value in node.items():
        if key.endswith("day") and isinstance(value, list):
            return cast(list[object], value)
    return None


def _one_row(row: object, *, display_symbol: str, adjustment: Adjustment) -> RawBar:
    if not isinstance(row, list) or len(row) < 6:
        raise _payload_error()
    parts = cast(list[object], row)
    try:
        trade_date = date.fromisoformat(str(parts[0]))
        open_price = Decimal(str(parts[1]))
        close = Decimal(str(parts[2]))
        high = Decimal(str(parts[3]))
        low = Decimal(str(parts[4]))
        volume = Decimal(str(parts[5]))
    except (ValueError, InvalidOperation) as exc:
        raise _payload_error() from exc
    return RawBar(
        trade_date=trade_date,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        amount=None,
        adjustment=adjustment,
        currency="CNY",
        unit="price",
        source_record_key=f"tencent|{display_symbol}|{trade_date.isoformat()}|{adjustment}",
    )


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD",
        message="无法解析腾讯日线响应",
        status_code=502,
    )
