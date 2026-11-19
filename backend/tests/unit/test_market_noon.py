"""北证午盘成交额快照：解析、交易日判断与「当天没有快照就不猜」的离线测试。

样本形状取自 2026-11-19 实测的 `qt.gtimg.cn/q=bj899050`：第 37 位是当日累计成交额（万元）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from app.providers.errors import ProviderError
from app.services import market_noon
from app.services.ashare_panorama import ASharePanoramaService, _Acc
from app.utils.market_time import SHANGHAI

DAY = date(2026, 11, 19)
PREV = date(2026, 11, 18)


def _payload(*, amount_wan: str = "963546.91", code: str = "bj899050") -> bytes:
    """拼一条腾讯报价：字段个数与真实返回一致（44 号之后不再读，但保留长度）。"""

    fields = [str(index) for index in range(45)]
    fields[1] = "北证50"
    fields[2] = "899050"
    fields[3] = "798.64"
    fields[37] = amount_wan
    return f'v_{code}="{"~".join(fields)}";'.encode("gbk")


class _FakeGateway:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        return self.payload


class _FakeWriter:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def upsert(
        self,
        *,
        trade_date: date,
        market: str,
        amount_yuan: Decimal,
        captured_at: datetime,
        source: str,
    ) -> None:
        self.rows.append(
            {
                "trade_date": trade_date,
                "market": market,
                "amount_yuan": amount_yuan,
                "captured_at": captured_at,
                "source": source,
            }
        )


def _now() -> datetime:
    return datetime(2026, 11, 19, 11, 35, tzinfo=SHANGHAI)


def test_parse_noon_amount_reads_wan_field() -> None:
    amount = market_noon.parse_noon_amount(_payload(), "bj899050")
    assert amount == Decimal("9635469100.00")
    # 没有这个字段就是 None，绝不用现价或成交量推算
    assert market_noon.parse_noon_amount(_payload(amount_wan="-"), "bj899050") is None
    assert market_noon.parse_noon_amount(_payload(), "sh000001") is None


async def _open() -> bool:
    return True


async def _closed() -> bool:
    return False


def test_non_trading_day_writes_nothing() -> None:
    gateway, writer = _FakeGateway(_payload()), _FakeWriter()
    written = asyncio.run(
        market_noon.capture_noon_amounts(
            object(),  # 非交易日分支不碰数据库
            now=_now(),
            gateway=gateway,
            writer=writer,
            session_open=_closed,
        )
    )
    assert written == 0
    assert writer.rows == []
    assert gateway.calls == []


def test_capture_writes_snapshot_for_trading_day() -> None:
    gateway, writer = _FakeGateway(_payload()), _FakeWriter()
    written = asyncio.run(
        market_noon.capture_noon_amounts(
            object(),
            now=_now(),
            gateway=gateway,
            writer=writer,
            session_open=_open,
        )
    )
    assert written == 1
    row = writer.rows[0]
    assert row["market"] == "bj"
    assert row["trade_date"] == DAY
    assert row["amount_yuan"] == Decimal("9635469100.00")
    assert row["captured_at"] == _now()
    assert "11:30" in row["source"]
    assert gateway.calls and "bj899050" in gateway.calls[0]


def test_capture_without_amount_is_upstream_error() -> None:
    with pytest.raises(ProviderError):
        asyncio.run(
            market_noon.capture_noon_amounts(
                object(),
                now=_now(),
                gateway=_FakeGateway(_payload(amount_wan="-")),
                writer=_FakeWriter(),
                session_open=_open,
            )
        )


def _amounts() -> dict[str, dict[str, float]]:
    return {
        "sh": {PREV.isoformat(): 6.0e11, DAY.isoformat(): 6.3e11},
        "sz": {PREV.isoformat(): 6.5e11, DAY.isoformat(): 6.6e11},
        "bj": {PREV.isoformat(): 9.0e10, DAY.isoformat(): 9.6e10},
    }


def test_snapshot_fills_bj_noon_and_prev_day() -> None:
    acc = _Acc()
    noon_today: dict[str, float | None] = {"sh": 3.7e11, "sz": 4.0e11}
    noon_prev: dict[str, float | None] = {"sh": 3.6e11, "sz": 4.1e11}
    snapshot = {DAY.isoformat(): 9.5e10, PREV.isoformat(): 9.0e10}

    ASharePanoramaService._fill_noon_snapshot(
        snapshot, [PREV.isoformat(), DAY.isoformat()], DAY, noon_today, noon_prev, acc
    )

    assert noon_today["bj"] == 9.5e10
    assert noon_prev["bj"] == 9.0e10
    assert any("11:30" in note for note in acc.notes)
    # 全市场午盘这时才算得出来：沪 + 深 + 京
    series = ASharePanoramaService._volume_series(
        _amounts(), noon_today, noon_prev, [PREV.isoformat(), DAY.isoformat()]
    )
    overall = next(item for item in series if item.key == "all")
    assert overall.noonAmountYi == pytest.approx(round((3.7e11 + 4.0e11 + 9.5e10) / 1e8, 2))


def test_snapshot_missing_keeps_bj_absent_and_says_why() -> None:
    acc = _Acc()
    noon_today: dict[str, float | None] = {"sh": 3.7e11, "sz": 4.0e11}
    noon_prev: dict[str, float | None] = {"sh": 3.6e11, "sz": 4.1e11}

    ASharePanoramaService._fill_noon_snapshot(
        {}, [PREV.isoformat(), DAY.isoformat()], DAY, noon_today, noon_prev, acc
    )

    assert "bj" not in noon_today
    assert any("暂缺" in note for note in acc.notes)
    series = ASharePanoramaService._volume_series(
        _amounts(), noon_today, noon_prev, [PREV.isoformat(), DAY.isoformat()]
    )
    overall = next(item for item in series if item.key == "all")
    assert overall.noonAmountYi is None


def test_snapshot_does_not_overwrite_live_noon() -> None:
    acc = _Acc()
    noon_today: dict[str, float | None] = {"sh": 3.7e11, "sz": 4.0e11, "bj": 9.9e10}
    noon_prev: dict[str, float | None] = {"sh": 3.6e11, "sz": 4.1e11}

    ASharePanoramaService._fill_noon_snapshot(
        {DAY.isoformat(): 9.5e10},
        [PREV.isoformat(), DAY.isoformat()],
        DAY,
        noon_today,
        noon_prev,
        acc,
    )

    assert noon_today["bj"] == 9.9e10
    assert acc.notes == []
