"""一次性灌入真实数据，供本地演示与答辩。所有数据都来自公开上游，不生成任何数字。

    python -m app.scripts.bootstrap_data [--since 2025-01-02] [--only bars,fred,sec,disclosures]

- A 股日线：东方财富 → 腾讯行情（akshare_sync 的两条源），24 个标的。
- 交易日历：从上证指数实际有成交的日子推出开市日（只写开市，不推断休市）。
- 美国宏观：FRED（有密钥走 observations，否则走公开 CSV）。
- 美股财报：SEC EDGAR companyfacts，只保留核心科目与 10-K/10-Q。
- 公告：巨潮资讯（A 股）与 SEC submissions（美股），只存标题、时间和官方链接。

每一步失败都只记录，不中断后面的步骤；重复运行是幂等的。
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from functools import partial

import httpx
from sqlalchemy import Engine

from app.catalog.industry_reference import SW_LEVEL1
from app.catalog.macro_series import catalog_codes
from app.catalog.reference_data import CalendarSeed, reference_calendar, reference_instruments
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine
from app.providers.disclosures import (
    USAGE_RIGHTS,
    fetch_cninfo_announcements,
    fetch_cninfo_stock_list,
    fetch_sec_submissions,
)
from app.providers.errors import ProviderDisconnected, ProviderError
from app.providers.fred import FredVintage
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.providers.sec_edgar import (
    SEC_MIN_INTERVAL_SECONDS,
    ParsedFact,
    fetch_companyfacts,
    parse_companyfacts,
    payload_sha256,
)
from app.repositories.filings import FilingRepository
from app.repositories.industry_reference import upsert_industry_reference
from app.repositories.instruments import InstrumentRepository
from app.repositories.macro import MacroRepository
from app.repositories.news_events import NewsEventRepository
from app.services.akshare_sync import apply_bars, fetch_bars
from app.services.disclosure_events import DisclosureDraft, NewsEventInput
from app.services.fred_sync import fetch_from_fred, sync_fred_series
from app.utils.time import utc_now

STEPS = ("bars", "fred", "sec", "disclosures")
SEC_CONCEPTS = frozenset(
    {
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:GrossProfit",
        "us-gaap:OperatingIncomeLoss",
        "us-gaap:NetIncomeLoss",
        "us-gaap:EarningsPerShareDiluted",
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:Assets",
        "us-gaap:AssetsCurrent",
        "us-gaap:Liabilities",
        "us-gaap:LiabilitiesCurrent",
        "us-gaap:StockholdersEquity",
        "us-gaap:AccountsReceivableNetCurrent",
        "us-gaap:InventoryNet",
        "us-gaap:ResearchAndDevelopmentExpense",
    }
)
SEC_FORMS = frozenset({"10-K", "10-Q"})
SEC_FIRST_FISCAL_YEAR = 2021
DISCLOSURE_DAYS = 120


def main() -> int:
    parser = argparse.ArgumentParser(description="灌入真实演示数据")
    parser.add_argument("--since", type=date.fromisoformat, default=date(2025, 1, 2))
    parser.add_argument("--only", default=",".join(STEPS))
    args = parser.parse_args()
    steps = {item.strip() for item in str(args.only).split(",") if item.strip()}
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    try:
        _seed_reference(engine)
        today = utc_now().date()
        if "bars" in steps:
            _sync_bars(engine, args.since, today)
        if "fred" in steps:
            asyncio.run(_sync_fred(engine, today))
        if "sec" in steps:
            asyncio.run(_sync_sec(engine, settings.sec_user_agent))
        if "disclosures" in steps:
            asyncio.run(_sync_disclosures(engine, settings.sec_user_agent, today))
    finally:
        engine.dispose()
    return 0


def _seed_reference(engine: Engine) -> None:
    repository = InstrumentRepository(engine)
    repository.upsert_reference(reference_instruments())
    repository.upsert_calendar(reference_calendar())
    MacroRepository(engine).ensure_catalog()
    industries, memberships = upsert_industry_reference(engine, SW_LEVEL1)
    print(
        "证券元数据、交易日历样例与宏观序列目录已就绪；"
        f"申万一级行业新增 {industries} 个、成分归属新增 {memberships} 条"
    )


def _sync_bars(engine: Engine, since: date, today: date) -> None:
    targets = [seed.display_symbol for seed in reference_instruments() if seed.market == "CN"]
    print(f"\n[A 股日线] {len(targets)} 个标的，{since} ~ {today}")
    open_days: set[date] = set()
    for index, symbol in enumerate(targets, start=1):
        try:
            bars = asyncio.run(_aretry(partial(fetch_bars, symbol, since, today, "none")))
            outcome = apply_bars(engine, bars, display_symbol=symbol, adjustment="none")
        except (ProviderError, ProviderDisconnected) as exc:
            print(f"  [{index:2}/{len(targets)}] {symbol:<10} 失败：{_reason(exc)}")
            continue
        if symbol == "000001.SH":
            open_days = {bar.trade_date for bar in bars}
        print(
            f"  [{index:2}/{len(targets)}] {symbol:<10} 写入 {outcome.rows_written:4} "
            f"拒绝 {outcome.rows_rejected} 待复核 {outcome.rows_for_review}"
        )
    if open_days:
        seeds = tuple(
            CalendarSeed(exchange, day, True, "derived-from-sse-composite-bars")
            for exchange in ("SSE", "SZSE")
            for day in sorted(open_days)
        )
        written = InstrumentRepository(engine).upsert_calendar(seeds)
        print(f"  交易日历：按上证指数有成交的日子写入开市日 {written} 条")


async def _sync_fred(engine: Engine, today: date) -> None:
    settings = get_settings()
    repository = MacroRepository(engine)
    start = today - timedelta(days=365 * 6)
    mode = "observations 接口" if settings.fred_api_key.strip() else "公开 CSV"
    print(f"\n[FRED] {len(catalog_codes())} 条序列，{start} ~ {today}，{mode}")

    async def fetch(code: str, first: date, last: date) -> tuple[FredVintage, ...]:
        return await fetch_from_fred(settings.fred_api_key, code, first, last)

    for code in sorted(catalog_codes()):
        try:
            written = await _aretry(
                partial(
                    sync_fred_series,
                    repository,
                    series_code=code,
                    observation_start=start,
                    observation_end=today,
                    fetch=fetch,
                    fetched_at=utc_now(),
                )
            )
        except (ProviderError, ProviderDisconnected) as exc:
            print(f"  {code:<9} 失败：{_reason(exc)}")
            continue
        print(f"  {code:<9} 写入 {written} 条")


async def _sync_sec(engine: Engine, user_agent: str) -> None:
    companies = [seed for seed in reference_instruments() if seed.cik]
    print(f"\n[SEC EDGAR] {len(companies)} 家公司 companyfacts（FY{SEC_FIRST_FISCAL_YEAR} 起）")
    repository = FilingRepository(engine)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as raw:
        client = ResilientHttpClient(
            HttpxTransport(raw),
            sleeper=asyncio.sleep,
            min_interval_seconds=SEC_MIN_INTERVAL_SECONDS,
        )
        for seed in companies:
            assert seed.cik is not None
            try:
                payload = await fetch_companyfacts(client, seed.cik, user_agent)
                facts = [fact for fact in parse_companyfacts(payload) if _wanted(fact)]
                written = repository.upsert_facts(facts, payload_sha256(payload), status="edgar")
            except (ProviderError, ProviderDisconnected) as exc:
                print(f"  {seed.symbol:<6} 失败：{_reason(exc)}")
                continue
            print(f"  {seed.symbol:<6} 核心科目 {len(facts):5} 条，新写入 {written}")


def _wanted(fact: ParsedFact) -> bool:
    return (
        fact.concept in SEC_CONCEPTS
        and fact.form in SEC_FORMS
        and fact.fiscal_year >= SEC_FIRST_FISCAL_YEAR
    )


async def _sync_disclosures(engine: Engine, user_agent: str, today: date) -> None:
    start = today - timedelta(days=DISCLOSURE_DAYS)
    repository = NewsEventRepository(engine)
    drafts: list[DisclosureDraft] = []
    print(f"\n[公告] {start} ~ {today}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), trust_env=False) as raw:
        client = ResilientHttpClient(
            HttpxTransport(raw), sleeper=asyncio.sleep, min_interval_seconds=0.4
        )
        try:
            stocks = await fetch_cninfo_stock_list(client)
        except (ProviderError, ProviderDisconnected) as exc:
            print(f"  巨潮证券列表失败：{_reason(exc)}")
            stocks = {}
        for seed in reference_instruments():
            if seed.market != "CN" or seed.instrument_type != "STOCK":
                continue
            stock = stocks.get(seed.symbol)
            if stock is None:
                continue
            try:
                found = await fetch_cninfo_announcements(
                    client,
                    stock=stock,
                    exchange="SH" if seed.exchange == "SSE" else "SZ",
                    start=start,
                    end=today,
                )
            except (ProviderError, ProviderDisconnected) as exc:
                print(f"  {seed.name_cn} 失败：{_reason(exc)}")
                continue
            drafts.extend(found)
            print(f"  巨潮 {seed.name_cn:<6} {len(found):3} 条")
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as raw:
        client = ResilientHttpClient(
            HttpxTransport(raw),
            sleeper=asyncio.sleep,
            min_interval_seconds=SEC_MIN_INTERVAL_SECONDS,
        )
        for seed in reference_instruments():
            if not seed.cik:
                continue
            try:
                found = await fetch_sec_submissions(
                    client,
                    cik=seed.cik,
                    display_name=seed.name_cn,
                    user_agent=user_agent,
                    start=start,
                )
            except (ProviderError, ProviderDisconnected) as exc:
                print(f"  SEC {seed.symbol} 失败：{_reason(exc)}")
                continue
            drafts.extend(found)
            print(f"  SEC  {seed.symbol:<6} {len(found):3} 条")
    items = [
        NewsEventInput(index=index, draft=draft, usage_rights=USAGE_RIGHTS)
        for index, draft in enumerate(drafts, start=1)
    ]
    outcome = repository.import_events(items)
    print(
        f"  入库 {outcome.accepted} 条，重复 {len(outcome.duplicates)} 条，"
        f"不合规 {len(outcome.rejected)} 条"
    )


async def _aretry[T](action: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return await action()
        except (ProviderError, ProviderDisconnected):
            if attempt == attempts:
                raise
            await asyncio.sleep(1.5 * attempt)
    raise AssertionError("unreachable")


def _reason(exc: ProviderError | ProviderDisconnected) -> str:
    return exc.message if isinstance(exc, ProviderError) else "连接中断"


if __name__ == "__main__":
    raise SystemExit(main())
