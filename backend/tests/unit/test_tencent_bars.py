"""腾讯行情适配器与「多源依次尝试」的单元测试。"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal

import pytest
from app.providers import tencent_bars
from app.providers.akshare_bars import DailyTarget, target_for
from app.providers.errors import ProviderDisconnected, ProviderError
from app.providers.quality import RawBar
from app.services import akshare_sync

PAYLOAD = json.dumps(
    {
        "code": 0,
        "msg": "",
        "data": {
            "sz000858": {
                "day": [
                    ["1999-01-04", "1.10", "1.20", "1.30", "1.00", "10.000"],
                    ["1999-01-05", "2.10", "2.20", "2.30", "2.00", "12.000"],
                ]
            }
        },
    }
).encode()


def _target(symbol: str = "000858.SZ") -> DailyTarget:
    target = target_for(symbol)
    assert target is not None
    return target


def test_market_code_uses_the_exchange_not_a_guess() -> None:
    assert tencent_bars.market_code("SSE", "600519") == "sh600519"
    assert tencent_bars.market_code("SZSE", "000858") == "sz000858"
    # 000001.SH 是上证指数、000001.SZ 是平安银行，前缀必须跟着交易所走。
    assert tencent_bars.market_code("SSE", "000001") == "sh000001"
    assert tencent_bars.market_code("SZSE", "000001") == "sz000001"


def test_market_code_rejects_unknown_exchange() -> None:
    with pytest.raises(ProviderError) as caught:
        tencent_bars.market_code("XNYS", "AAPL")
    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.status_code == 422


def test_kline_url_carries_the_window_and_adjustment() -> None:
    url = tencent_bars.kline_url(
        exchange="SZSE",
        symbol="000858",
        start=date(1999, 1, 4),
        end=date(1999, 1, 5),
        adjustment="qfq",
    )
    assert url.startswith("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=")
    assert "sz000858%2Cday%2C1999-01-04%2C1999-01-05%2C" in url
    assert url.endswith("%2Cqfq")


def test_bar_count_is_capped() -> None:
    assert tencent_bars.bar_count(date(2026, 1, 1), date(2026, 1, 10)) <= tencent_bars.MAX_BARS
    assert tencent_bars.bar_count(date(1990, 1, 1), date(2026, 10, 15)) == tencent_bars.MAX_BARS


def test_parser_reads_the_tencent_field_order_and_leaves_amount_empty() -> None:
    bars = tencent_bars.parse_klines(PAYLOAD, target=_target(), adjustment="none")
    assert [bar.trade_date for bar in bars] == [date(1999, 1, 4), date(1999, 1, 5)]
    first = bars[0]
    # 腾讯的顺序是 日期,开,收,高,低,量——不是 OHLC。
    assert first.open == Decimal("1.10")
    assert first.close == Decimal("1.20")
    assert first.high == Decimal("1.30")
    assert first.low == Decimal("1.00")
    assert first.volume == Decimal("10.000")
    assert first.amount is None
    assert first.currency == "CNY"
    assert first.source_record_key == "tencent|000858.SZ|1999-01-04|none"


def test_parser_reads_the_qfq_array_when_adjusted() -> None:
    payload = json.dumps(
        {
            "code": 0,
            "data": {"sz000858": {"qfqday": [["1999-01-04", "1.10", "1.20", "1.30", "1.00", "5"]]}},
        }
    ).encode()
    bars = tencent_bars.parse_klines(payload, target=_target(), adjustment="qfq")
    assert len(bars) == 1
    assert bars[0].adjustment == "qfq"


def test_empty_or_unknown_symbol_is_not_an_error() -> None:
    assert (
        tencent_bars.parse_klines(b'{"code":0,"data":{}}', target=_target(), adjustment="none")
        == ()
    )
    assert (
        tencent_bars.parse_klines(
            b'{"code":0,"data":{"sz000858":""}}', target=_target(), adjustment="none"
        )
        == ()
    )


def test_broken_payload_is_reported_without_echoing_the_body() -> None:
    with pytest.raises(ProviderError) as caught:
        tencent_bars.parse_klines(b"super-secret-body", target=_target(), adjustment="none")
    assert caught.value.code == "UPSTREAM_PAYLOAD"
    assert "super-secret-body" not in caught.value.message


def test_short_row_is_rejected() -> None:
    payload = json.dumps(
        {"code": 0, "data": {"sz000858": {"day": [["1999-01-04", "1.10"]]}}}
    ).encode()
    with pytest.raises(ProviderError) as caught:
        tencent_bars.parse_klines(payload, target=_target(), adjustment="none")
    assert caught.value.code == "UPSTREAM_PAYLOAD"


def _bar(day: date) -> RawBar:
    return RawBar(
        trade_date=day,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("2"),
        volume=Decimal("1"),
        amount=None,
        currency="CNY",
        source_record_key=f"test|{day.isoformat()}",
    )


async def _failing(
    target: DailyTarget, start: date, end: date, adjustment: str
) -> tuple[RawBar, ...]:
    raise ProviderDisconnected


def test_fetch_bars_falls_back_to_the_second_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def failing(
        target: DailyTarget, start: date, end: date, adjustment: str
    ) -> tuple[RawBar, ...]:
        calls.append("eastmoney")
        raise ProviderDisconnected

    async def working(
        target: DailyTarget, start: date, end: date, adjustment: str
    ) -> tuple[RawBar, ...]:
        calls.append("tencent")
        return (_bar(date(1999, 1, 4)),)

    monkeypatch.setattr(akshare_sync, "_SOURCES", (("东方财富", failing), ("腾讯行情", working)))
    bars = asyncio.run(
        akshare_sync.fetch_bars("600519.SH", date(1999, 1, 4), date(1999, 1, 5), "none")
    )
    assert calls == ["eastmoney", "tencent"]
    assert len(bars) == 1


def test_fetch_bars_reports_every_source_before_giving_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing(
        target: DailyTarget, start: date, end: date, adjustment: str
    ) -> tuple[RawBar, ...]:
        raise ProviderError(code="UPSTREAM_ERROR", message="上游请求失败", status_code=502)

    async def empty(
        target: DailyTarget, start: date, end: date, adjustment: str
    ) -> tuple[RawBar, ...]:
        return ()

    monkeypatch.setattr(akshare_sync, "_SOURCES", (("东方财富", failing), ("腾讯行情", empty)))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            akshare_sync.fetch_bars("600519.SH", date(1999, 1, 4), date(1999, 1, 5), "none")
        )
    assert caught.value.code == "UPSTREAM_UNAVAILABLE"
    assert caught.value.status_code == 502
    assert "东方财富" in caught.value.message
    assert "腾讯行情" in caught.value.message


def test_first_source_wins_when_it_works(monkeypatch: pytest.MonkeyPatch) -> None:
    async def eastmoney(
        target: DailyTarget, start: date, end: date, adjustment: str
    ) -> tuple[RawBar, ...]:
        return (_bar(date(1999, 1, 4)), _bar(date(1999, 1, 5)))

    monkeypatch.setattr(
        akshare_sync,
        "_SOURCES",
        (("东方财富", eastmoney), ("腾讯行情", _failing)),
    )
    bars = asyncio.run(
        akshare_sync.fetch_bars("600519.SH", date(1999, 1, 4), date(1999, 1, 5), "none")
    )
    assert len(bars) == 2


def test_fetch_bars_still_rejects_a_malformed_code() -> None:
    with pytest.raises(ProviderError) as caught:
        asyncio.run(akshare_sync.fetch_bars("BADCODE", date(1999, 1, 4), date(1999, 1, 5), "none"))
    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.status_code == 422
