from datetime import date, timedelta
from decimal import Decimal

from app.repositories.market_bars import StoredDailyBar
from app.schemas.bars import build_bar_page


def _bars(closes: list[str]) -> list[StoredDailyBar]:
    start = date(2026, 1, 5)
    return [
        StoredDailyBar(
            trade_date=start + timedelta(days=index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
            amount=None,
            adjustment="none",
            quality_flag="ok",
        )
        for index, close in enumerate(closes)
    ]


def _page(rows: list[StoredDailyBar]) -> dict[str, str]:
    return build_bar_page(
        instrument_id="x",
        display_symbol="600519.SH",
        name_cn="贵州茅台",
        adjustment="none",
        pit=date(2026, 12, 31),
        rows=rows,
        truncated=False,
    ).window_returns


def test_window_return_spans_n_trading_days() -> None:
    closes = ["100"] * 7 + ["80"] + ["90"] * 21 + ["100"]
    returns = _page(_bars(closes))
    assert returns["22"] == "0.25"
    assert "66" not in returns


def test_short_history_omits_longer_windows_instead_of_substituting() -> None:
    assert _page(_bars(["100"] * 22)) == {}
    assert _page(_bars(["100"] * 22 + ["110"])) == {"22": "0.1"}
    assert _page([]) == {}
