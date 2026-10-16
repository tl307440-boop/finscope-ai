"""全市场清单、A 股报表、Tiingo 日线的解析，以及新鲜度与按需抓取的判定。"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.providers.eastmoney_statements import BALANCE, CASHFLOW, INCOME, parse_statement
from app.providers.errors import ProviderError
from app.providers.tiingo_bars import parse_prices
from app.providers.universe import exchange_for_cn_code, parse_cn_listings, parse_us_listings
from app.repositories.sync_jobs import JobSnapshot
from app.services import freshness
from app.services.company_data import NOT_FOUND_COOLDOWN, _Spec, evaluate

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=UTC)


def _us_payload(rows: list[list[object]]) -> bytes:
    return json.dumps(
        {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}, ensure_ascii=False
    ).encode()


def test_us_listings_keep_one_ticker_per_cik_and_only_listed_exchanges() -> None:
    found = parse_us_listings(
        _us_payload(
            [
                [1652044, "Alphabet Inc.", "GOOGL", "Nasdaq"],
                [1652044, "Alphabet Inc.", "GOOG", "Nasdaq"],
                [320193, "Apple Inc.", "AAPL", "Nasdaq"],
                [1234567, "Some OTC Corp", "OTCX", "OTC"],
                [7654321, "", "NONAME", "NYSE"],
            ]
        )
    )
    assert [(item.ticker, item.cik, item.exchange) for item in found] == [
        ("GOOGL", "0001652044", "NASDAQ"),
        ("AAPL", "0000320193", "NASDAQ"),
    ]


def test_us_listings_reject_unknown_structure() -> None:
    with pytest.raises(ProviderError):
        parse_us_listings(b'{"unexpected": 1}')
    with pytest.raises(ProviderError):
        parse_us_listings(b"not json")


@pytest.mark.parametrize(
    ("code", "exchange"),
    [
        ("600519", "SSE"),
        ("688981", "SSE"),
        ("000001", "SZSE"),
        ("300750", "SZSE"),
        ("900901", None),  # B 股
        ("430047", None),  # 北交所
        ("60051", None),
    ],
)
def test_cn_code_maps_to_exchange(code: str, exchange: str | None) -> None:
    assert exchange_for_cn_code(code) == exchange


def test_cn_listings_use_short_name_and_skip_unsupported_codes() -> None:
    payload = json.dumps(
        {
            "stockList": [
                {"code": "600519", "zwjc": "贵州茅台"},
                {"code": "002594", "zwjc": "比亚迪"},
                {"code": "900901", "zwjc": "B股"},
            ]
        },
        ensure_ascii=False,
    ).encode()
    found = parse_cn_listings(payload)
    assert [(item.display_symbol, item.name) for item in found] == [
        ("600519.SH", "贵州茅台"),
        ("002594.SZ", "比亚迪"),
    ]


def _statement(rows: list[dict[str, object]]) -> bytes:
    return json.dumps({"result": {"data": rows}}).encode()


def test_eastmoney_income_statement_maps_cumulative_periods_and_derives_gross_profit() -> None:
    facts = parse_statement(
        _statement(
            [
                {
                    "REPORT_DATE": "2026-06-30 00:00:00",
                    "NOTICE_DATE": "2026-08-29 00:00:00",
                    "CURRENCY": "CNY",
                    "OPERATE_INCOME": 1000,
                    "OPERATE_COST": 600,
                    "PARENT_NETPROFIT": 80,
                    "DILUTED_EPS": 1.35,
                },
                {  # 非标准报告期（月日不在一季/中报/三季/年报内）直接跳过
                    "REPORT_DATE": "2026-05-31 00:00:00",
                    "NOTICE_DATE": "2026-06-30 00:00:00",
                    "OPERATE_INCOME": 1,
                },
                {  # 缺公告日：无法做时间旅行，不入库
                    "REPORT_DATE": "2026-03-31 00:00:00",
                    "OPERATE_INCOME": 1,
                },
            ]
        ),
        INCOME,
        "002594.SZ",
    )
    by_concept = {fact.concept: fact for fact in facts}
    assert by_concept["cn-gaap:Revenues"].value == Decimal("1000")
    assert by_concept["cn-gaap:GrossProfit"].value == Decimal("400")
    assert by_concept["cn-gaap:GrossProfit"].source_field == "OPERATE_INCOME - OPERATE_COST"
    eps = by_concept["cn-gaap:EarningsPerShareDiluted"]
    assert eps.unit == "CNY/shares"
    assert eps.value == Decimal("1.35")
    for fact in facts:
        assert fact.period_start == date(2026, 1, 1)
        assert fact.period_end == date(2026, 6, 30)
        assert fact.filed == date(2026, 8, 29)
        assert fact.fiscal_period == "H1"
        assert fact.source_url.endswith("code=002594sz")


def test_eastmoney_balance_sheet_is_instant_and_cashflow_is_cumulative() -> None:
    row = {
        "REPORT_DATE": "2025-12-31 00:00:00",
        "NOTICE_DATE": "2026-03-28 00:00:00",
        "TOTAL_ASSETS": 900,
        "TOTAL_LIABILITIES": 600,
        "NETCASH_OPERATE": 50,
        "ACCOUNTS_RECE": None,
        "NOTE_ACCOUNTS_RECE": 7,
    }
    balance = {fact.concept: fact for fact in parse_statement(_statement([row]), BALANCE, "x.SZ")}
    assert balance["cn-gaap:Assets"].period_start is None
    assert balance["cn-gaap:AccountsReceivableNetCurrent"].source_field == "NOTE_ACCOUNTS_RECE"
    assert balance["cn-gaap:Assets"].fiscal_period == "FY"
    cash = parse_statement(_statement([row]), CASHFLOW, "x.SZ")
    assert [fact.period_start for fact in cash] == [date(2025, 1, 1)]


def test_eastmoney_empty_result_is_not_an_error() -> None:
    assert parse_statement(b'{"result": null}', INCOME, "x.SZ") == ()
    with pytest.raises(ProviderError):
        parse_statement(b"[]", INCOME, "x.SZ")


def test_tiingo_prices_are_unadjusted_usd_and_unknown_symbol_is_not_found() -> None:
    payload = json.dumps(
        [
            {
                "date": "2026-10-15T00:00:00.000Z",
                "open": 10,
                "high": 12,
                "low": 9,
                "close": 11,
                "volume": 500,
            },
            {
                "date": "2026-10-16T00:00:00.000Z",
                "open": None,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    ).encode()
    bars = parse_prices(payload, symbol="crwd")
    assert len(bars) == 1
    assert bars[0].trade_date == date(2026, 10, 15)
    assert bars[0].currency == "USD"
    assert bars[0].adjustment == "none"
    assert bars[0].amount is None
    assert bars[0].source_record_key == "tiingo|CRWD|2026-10-15|none"
    with pytest.raises(ProviderError) as missing:
        parse_prices(b'{"detail": "Error: Ticker not found"}', symbol="ZZZZ")
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(ProviderError) as limited:
        parse_prices(b'{"detail": "hourly request allocation exceeded"}', symbol="CRWD")
    assert limited.value.code == "UPSTREAM_UNAVAILABLE"


def test_freshness_thresholds_follow_two_level_policy() -> None:
    today = date(2026, 10, 16)
    assert freshness.day_state(None, today, freshness.BARS) == "missing"
    assert freshness.day_state(date(2026, 10, 14), today, freshness.BARS) == "fresh"
    assert freshness.day_state(date(2026, 10, 10), today, freshness.BARS) == "warn"
    assert freshness.day_state(date(2026, 10, 1), today, freshness.BARS) == "stale"
    # 日期在未来按 0 天算，不报过期
    assert freshness.day_state(date(2026, 10, 20), today, freshness.BARS) == "fresh"
    assert freshness.sync_state(NOW - timedelta(hours=49), NOW, freshness.FINANCIALS) == "warn"
    assert freshness.sync_state(NOW - timedelta(days=8), NOW, freshness.FINANCIALS) == "stale"
    assert freshness.macro_threshold("Quarterly") is freshness.MACRO["quarterly"]
    assert freshness.macro_threshold("unknown") is freshness.MACRO["monthly"]
    assert freshness.worst(["fresh", "stale", "warn"]) == "stale"
    assert freshness.worst([]) == "missing"


def _spec(
    *,
    available: bool = True,
    latest: date | None = None,
    synced: datetime | None = None,
    by_data_date: bool = False,
) -> _Spec:
    return _Spec(
        dataset="financials",
        label="财务报表",
        source="SEC EDGAR",
        job_type="sec_filings",
        threshold=freshness.BARS if by_data_date else freshness.FINANCIALS,
        by_data_date=by_data_date,
        available=available,
        unavailable_reason="未配置",
        latest_data=latest,
        last_synced=synced,
    )


def _job(
    status: str,
    *,
    error_code: str | None = None,
    finished_at: datetime | None = None,
) -> JobSnapshot:
    return JobSnapshot(
        id=uuid4(),
        job_type="sec_filings",
        provider_id=uuid4(),
        scope={},
        dedupe_key="k",
        status=status,
        attempts=1,
        max_attempts=3,
        rows_written=0,
        error_code=error_code,
        error_summary="上游报错",
        progress_stage="",
        requested_at=NOW,
        finished_at=finished_at,
    )


def test_missing_data_requests_a_fetch_and_fresh_data_does_not() -> None:
    cooldown = timedelta(minutes=15)
    missing = evaluate(spec=_spec(), job=None, now=NOW, cooldown=cooldown)
    assert (missing.state, missing.enqueue) == ("missing", True)
    fresh = evaluate(
        spec=_spec(synced=NOW - timedelta(hours=1)), job=None, now=NOW, cooldown=cooldown
    )
    assert (fresh.state, fresh.enqueue) == ("fresh", False)


def test_active_job_blocks_duplicate_request_and_reports_progress() -> None:
    result = evaluate(spec=_spec(), job=_job("running"), now=NOW, cooldown=timedelta(minutes=15))
    assert (result.sync, result.enqueue) == ("running", False)


def test_failed_job_is_cooled_down_then_retried() -> None:
    cooldown = timedelta(minutes=15)
    recent = _job(
        "failed", error_code="UPSTREAM_UNAVAILABLE", finished_at=NOW - timedelta(minutes=5)
    )
    blocked = evaluate(spec=_spec(), job=recent, now=NOW, cooldown=cooldown)
    assert (blocked.sync, blocked.enqueue) == ("failed", False)
    assert "上游报错" in blocked.message
    old = _job("failed", error_code="UPSTREAM_UNAVAILABLE", finished_at=NOW - timedelta(minutes=30))
    assert evaluate(spec=_spec(), job=old, now=NOW, cooldown=cooldown).enqueue is True


def test_not_found_is_unavailable_for_a_day_instead_of_retrying() -> None:
    cooldown = timedelta(minutes=15)
    job = _job("failed", error_code="NOT_FOUND", finished_at=NOW - timedelta(hours=2))
    result = evaluate(spec=_spec(), job=job, now=NOW, cooldown=cooldown)
    assert (result.state, result.enqueue) == ("unavailable", False)
    later = _job(
        "failed",
        error_code="NOT_FOUND",
        finished_at=NOW - NOT_FOUND_COOLDOWN - timedelta(minutes=1),
    )
    assert evaluate(spec=_spec(), job=later, now=NOW, cooldown=cooldown).enqueue is True


def test_unconfigured_source_is_unavailable_not_fabricated() -> None:
    result = evaluate(
        spec=_spec(available=False), job=None, now=NOW, cooldown=timedelta(minutes=15)
    )
    assert (result.state, result.enqueue, result.message) == ("unavailable", False, "未配置")
