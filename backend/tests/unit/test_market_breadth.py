"""全市场涨跌家数统计的离线用例。"""

from __future__ import annotations

import json

from app.providers.tencent_quote import Quote
from app.services.market_breadth import bse_codes, count_breadth, parse_fuyao_snapshot


def _quote(price: float | None, prev: float | None, amount: float | None) -> Quote:
    return Quote(
        code="000000",
        name="测试",
        price=price,
        change=None,
        change_pct=None,
        amount_yuan=amount,
        turnover_pct=None,
        float_cap_yuan=None,
        prev_close=prev,
    )


def test_bse_codes_filters_index_and_delisted() -> None:
    payload = json.dumps(
        {
            "stockList": [
                {"code": "920000", "zwjc": "安徽凤凰", "category": "A股"},
                {"code": "430047", "zwjc": "诺思兰德", "category": "A股"},
                {"code": "873833", "zwjc": "美心翼申", "category": "A股"},
                {"code": "920305", "zwjc": "云创退", "category": "A股"},
                {"code": "899050", "zwjc": "北证50", "category": "指数"},
                {"code": "400001", "zwjc": "老三板", "category": "三板"},
                {"code": "000001", "zwjc": "平安银行", "category": "A股"},
            ]
        },
        ensure_ascii=False,
    ).encode()

    assert bse_codes(payload) == ["430047", "873833", "920000", "920305"]


def test_bse_codes_handles_broken_payload() -> None:
    assert bse_codes(b"<html>502</html>") == []
    assert bse_codes(b'{"stockList": "oops"}') == []


def test_count_breadth_skips_halted_and_invalid() -> None:
    quotes = [
        _quote(10.5, 10.0, 1e8),  # 涨
        _quote(9.4, 10.0, 2e8),  # 跌
        _quote(10.0, 10.0, 3e7),  # 平
        _quote(8.17, 8.17, 0.0),  # 停牌（成交额 0）
        _quote(0.0, 8.17, 0.0),  # 无报价
        _quote(None, 8.17, 1e6),  # 缺现价
        _quote(5.0, None, 1e6),  # 缺昨收
    ]

    counts = count_breadth(quotes)

    assert (counts.up, counts.down, counts.flat, counts.total) == (1, 1, 1, 3)
    assert "沪深京" in counts.source
    assert "停牌" in counts.note()


def test_parse_fuyao_snapshot_counts_ratio_and_skips_null() -> None:
    doc = {
        "code": 0,
        "message": "success",
        "data": {
            "total": 5,
            "item": [
                {"thscode": "000001.SZ", "price_change_ratio_pct": 1.495, "volume": 213498890},
                {"thscode": "600185.SH", "price_change_ratio_pct": -3.2, "volume": 8e7},
                {"thscode": "002122.SZ", "price_change_ratio_pct": 0.0, "volume": 1e6},
                {"thscode": "301661.SZ", "price_change_ratio_pct": None, "volume": 0},
                {"thscode": "920305.BJ", "price_change_ratio_pct": "-2.5", "volume": 0},
            ],
        },
    }

    counts = parse_fuyao_snapshot(doc)

    assert counts is not None
    assert (counts.up, counts.down, counts.flat, counts.total) == (1, 1, 1, 3)
    assert "扶摇" in counts.source


def test_parse_fuyao_snapshot_rejects_bad_envelope() -> None:
    assert parse_fuyao_snapshot({"code": 2003, "message": "Invalid or revoked API key"}) is None
    assert parse_fuyao_snapshot({"code": 0, "data": {"item": []}}) is None
    assert (
        parse_fuyao_snapshot({"code": 0, "data": {"item": [{"price_change_ratio_pct": None}]}})
        is None
    )
    assert parse_fuyao_snapshot("oops") is None
