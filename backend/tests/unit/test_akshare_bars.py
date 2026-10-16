import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from app.providers.akshare_bars import dedupe_bars, kline_url, parse_klines, secid_for, target_for
from app.providers.errors import ProviderError
from app.providers.quality import CheckedBar
from app.services.ingest import ingest_bars
from app.utils.market_time import bar_visible, shanghai_pit


class MemoryWriter:
    def __init__(self) -> None:
        self.closes: list[Decimal] = []

    def upsert(
        self,
        bars: list[CheckedBar],
        *,
        instrument_id: object,
        provider_id: object,
    ) -> int:
        del instrument_id, provider_id
        self.closes.extend(bar.close for bar in bars)
        return len(bars)


def test_secid_uses_the_exchange_not_a_guess() -> None:
    assert secid_for("SSE", "600519") == "1.600519"
    assert secid_for("SZSE", "000858") == "0.000858"
    assert secid_for("SSE", "000001") == "1.000001"
    assert secid_for("SZSE", "000001") == "0.000001"


def test_target_for_reads_the_exchange_from_the_suffix() -> None:
    # 同一串数字分属两个市场：000001.SH 是上证指数，000001.SZ 是平安银行。
    # 交易所只能由后缀决定，不能靠前缀猜。
    shanghai = target_for("600519.sh")
    assert shanghai is not None
    assert (shanghai.display_symbol, shanghai.exchange, shanghai.symbol) == (
        "600519.SH",
        "SSE",
        "600519",
    )
    index = target_for("000001.SH")
    assert index is not None and index.exchange == "SSE"
    bank = target_for("000001.SZ")
    assert bank is not None and bank.exchange == "SZSE"


def test_target_for_accepts_any_well_formed_code() -> None:
    # 不再是固定几只的白名单：数据库里任意 A 股代码都应该能解析出抓取目标。
    for code in ("600519.SH", "601318.SH", "000858.SZ", "300750.SZ", "688981.SH"):
        assert target_for(code) is not None, code


def test_target_for_rejects_malformed_codes() -> None:
    for code in ("", "600519", "600519.XX", "60051.SH", "6005190.SH", "ABC519.SH", "600519.US"):
        assert target_for(code) is None, code


def test_kline_url_requests_daily_unadjusted_bars() -> None:
    url = kline_url(
        exchange="SSE",
        symbol="600519",
        start=date(1999, 1, 4),
        end=date(1999, 1, 5),
        adjustment="none",
    )
    assert "secid=1.600519" in url
    assert "klt=101" in url
    assert "fqt=0" in url
    assert "beg=19990104" in url


def test_parser_keeps_open_close_high_low_order_and_drops_duplicate_dates() -> None:
    payload = json.dumps(
        {
            "data": {
                "klines": [
                    "1999-01-04,1.10,1.20,1.30,1.00,10,100.00,0,0,0,0",
                    "1999-01-04,9.10,9.20,9.30,9.00,11,101.00,0,0,0,0",
                    "1999-01-05,2.10,2.20,2.30,2.00,12,102.00,0,0,0,0",
                ]
            }
        }
    ).encode()
    parsed = parse_klines(payload, display_symbol="600519.SH", adjustment="none")
    kept, skipped = dedupe_bars(parsed)
    assert skipped == 1
    assert [(bar.trade_date, bar.open, bar.close, bar.high, bar.low) for bar in kept] == [
        (date(1999, 1, 4), Decimal("1.10"), Decimal("1.20"), Decimal("1.30"), Decimal("1.00")),
        (date(1999, 1, 5), Decimal("2.10"), Decimal("2.20"), Decimal("2.30"), Decimal("2.00")),
    ]
    assert kept[0].volume == Decimal("10")
    assert kept[0].currency == "CNY"


def test_broken_ohlc_is_not_written() -> None:
    payload = json.dumps(
        {
            "data": {
                "klines": [
                    "1999-01-06,1.00,1.20,1.10,1.30,10,100.00,0,0,0,0",
                    "1999-01-07,1.10,1.20,1.30,1.00,10,100.00,0,0,0,0",
                ]
            }
        }
    ).encode()
    parsed = parse_klines(payload, display_symbol="601318.SH", adjustment="none")
    writer = MemoryWriter()
    outcome = ingest_bars(list(parsed), writer, instrument_id=uuid4(), provider_id=uuid4())
    assert outcome.rows_rejected == 1
    assert writer.closes == [Decimal("1.20")]


def test_bad_payload_does_not_echo_the_body() -> None:
    try:
        parse_klines(b"super-secret-body", display_symbol="600519.SH", adjustment="none")
    except ProviderError as error:
        assert error.code == "UPSTREAM_PAYLOAD"
        assert "super-secret-body" not in error.message
    else:
        raise AssertionError("应当拒绝无法解析的响应")


def test_future_bar_is_hidden_and_shanghai_date_is_used() -> None:
    assert bar_visible(date(1999, 1, 5), date(1999, 1, 4)) is False
    assert bar_visible(date(1999, 1, 4), date(1999, 1, 4)) is True
    assert shanghai_pit(datetime(1999, 1, 4, 16, 0, tzinfo=UTC)) == date(1999, 1, 5)


def test_ingest_error_does_not_reach_the_writer() -> None:
    writer = MemoryWriter()
    outcome = ingest_bars(
        ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504),
        writer,
        instrument_id=uuid4(),
        provider_id=uuid4(),
    )
    assert outcome.rows_written == 0
    assert writer.closes == []
