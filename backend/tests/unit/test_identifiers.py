from datetime import date

import pytest
from app.catalog.reference_data import reference_calendar, reference_instruments
from app.utils.calendar import calendar_status
from app.utils.identifiers import normalize_cik, parse_listed_code, parse_search_key


def test_a_share_suffix_maps_to_one_exchange() -> None:
    listed = parse_listed_code("600519.sh")
    assert listed is not None
    assert listed.canonical_code == "SSE:600519"
    assert listed.display_symbol == "600519.SH"
    assert parse_listed_code("SZ000001") is not None
    assert parse_listed_code("SZ000001").canonical_code == "SZSE:000001"  # type: ignore[union-attr]
    assert parse_listed_code("000001.SH") is not None
    assert parse_listed_code("000001.SH").canonical_code == "SSE:000001"  # type: ignore[union-attr]


def test_bare_six_digit_code_does_not_guess_exchange() -> None:
    key = parse_search_key("000001")
    assert key.exchange is None
    assert key.symbol == "000001"


def test_cik_is_zero_padded_to_ten_digits() -> None:
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik("0000320193") == "0000320193"
    with pytest.raises(ValueError):
        normalize_cik("32A193")
    assert parse_search_key("320193").cik == "0000320193"


def test_reference_universe_keeps_index_and_stock_apart() -> None:
    instruments = reference_instruments()
    codes = [item.canonical for item in instruments]
    assert "SSE:000001" in codes
    assert "SZSE:000001" in codes
    index = next(item for item in instruments if item.canonical == "SSE:000001")
    stock = next(item for item in instruments if item.canonical == "SZSE:000001")
    assert index.instrument_type == "INDEX"
    assert stock.instrument_type == "STOCK"
    cn_stocks = [
        item for item in instruments if item.instrument_type == "STOCK" and item.market == "CN"
    ]
    assert len(cn_stocks) == 20
    apple = next(item for item in instruments if item.symbol == "AAPL")
    assert apple.cik == "0000320193"
    assert all(
        item.cik is None or (len(item.cik) == 10 and item.cik.isdigit()) for item in instruments
    )


def test_calendar_does_not_use_weekdays() -> None:
    rows = {(item.exchange, item.session_date): item.is_open for item in reference_calendar()}
    assert rows[("SSE", date(2024, 10, 1))] is False
    assert date(2024, 10, 1).weekday() == 1
    assert rows[("SSE", date(2024, 9, 29))] is True
    assert date(2024, 9, 29).weekday() == 6
    assert rows[("SZSE", date(2024, 10, 8))] is True
    assert calendar_status(None) == "unknown"
    assert calendar_status(False) == "closed"
    assert calendar_status(True) == "open"
