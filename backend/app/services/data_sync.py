"""各数据集的抓取与入库：worker 执行器、调度器任务与按需抓取共用这一份实现。

每个函数只做「抓 → 校验 → 幂等写入」，返回写入行数；上游异常统一抛 ProviderError，
由队列决定是否重试。限速、超时、User-Agent 都在这里集中设定，调用方不用再操心。
"""

import asyncio
from datetime import date, timedelta
from uuid import UUID

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.market import Instrument
from app.providers import (
    china_macro_sources,
    eastmoney_statements,
    nbs_macro,
    tiingo_bars,
    universe,
)
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.providers.sec_edgar import (
    SEC_MIN_INTERVAL_SECONDS,
    ParsedFact,
    fetch_companyfacts,
    parse_companyfacts,
    payload_sha256,
)
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.filings import FilingRepository
from app.repositories.macro_china import MacroChinaRepository
from app.repositories.market_bars import MarketBarRepository
from app.repositories.universe import UniverseRepository
from app.services.china_macro import CsvObservation
from app.services.ifrs import IFRS_CONCEPTS
from app.services.ingest import ingest_bars

# 只保留研究页用得到的核心科目与 10-K / 10-Q，不把整份 companyfacts 全灌进库。
SEC_CONCEPTS = frozenset(
    {
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:SalesRevenueNet",
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
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",
    }
)
# 20-F / 40-F / 6-K 是外国发行人的年报与中报，披露口径多为 IFRS，读取时由 services/ifrs.py 归一。
SEC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "6-K"})
SEC_FIRST_FISCAL_YEAR = 2021
# 首次抓美股日线回看的自然日数（约两年），之后只补增量。
FIRST_BARS_LOOKBACK_DAYS = 730


def wanted_sec_fact(fact: ParsedFact) -> bool:
    return (
        (fact.concept in SEC_CONCEPTS or fact.concept in IFRS_CONCEPTS)
        and fact.form in SEC_FORMS
        and fact.fiscal_year >= SEC_FIRST_FISCAL_YEAR
    )


def _client(
    *, trust_env: bool, timeout: float, min_interval: float, attempts: int = 3
) -> tuple[httpx.AsyncClient, ResilientHttpClient]:
    raw = httpx.AsyncClient(timeout=httpx.Timeout(timeout), trust_env=trust_env)
    return raw, ResilientHttpClient(
        HttpxTransport(raw),
        sleeper=asyncio.sleep,
        min_interval_seconds=min_interval,
        max_attempts=attempts,
    )


def _missing_upstream(exc: ProviderError, message: str) -> ProviderError:
    """上游对「没有这个代码」回 404：这是终局性的缺数据，不该重试。"""

    if exc.status_code == 404:
        return ProviderError(code="NOT_FOUND", message=message, status_code=404)
    return exc


async def sync_sec_facts(engine: Engine, *, cik: str, user_agent: str) -> int:
    raw, client = _client(trust_env=True, timeout=60.0, min_interval=SEC_MIN_INTERVAL_SECONDS)
    async with raw:
        try:
            payload = await fetch_companyfacts(client, cik, user_agent)
        except ProviderError as exc:
            raise _missing_upstream(exc, "SEC 没有这家公司的 XBRL 财报") from exc
    facts = [fact for fact in parse_companyfacts(payload) if wanted_sec_fact(fact)]
    if not facts:
        raise ProviderError(
            code="NOT_FOUND",
            message="SEC 有这家公司，但没有 10-K / 10-Q 的核心财务科目",
            status_code=404,
        )
    return FilingRepository(engine).upsert_facts(facts, payload_sha256(payload), status="edgar")


async def sync_cn_statements(engine: Engine, *, display_symbol: str) -> int:
    instrument_id = _instrument_id(engine, "CN", display_symbol)
    raw, client = _client(trust_env=False, timeout=15.0, min_interval=0.3, attempts=4)
    async with raw:
        facts = await eastmoney_statements.fetch_statements(client, display_symbol)
    if not facts:
        raise ProviderError(code="NOT_FOUND", message="东方财富没有这只证券的财报", status_code=404)
    return CnFinancialRepository(engine).upsert_facts(instrument_id, facts)


async def sync_china_macro(engine: Engine, *, today: date | None = None) -> int:
    """抓取中国宏观序列（东方财富、商务部、国家统计局），按「序列 + 观察期 + 公布日上界」幂等写入。

    先把抓到的写进去，再对失败的序列统一报错：一个源挂了不影响其它序列入库，任务会按重试策略重来。
    国家统计局站点有访问频率保护，请求间隔放宽到 1.5 秒，且每个目录只请求一次。
    """

    day = today or date.today()
    raw, client = _client(trust_env=False, timeout=20.0, min_interval=0.5, attempts=3)
    nbs_raw, nbs_client = _client(trust_env=False, timeout=30.0, min_interval=1.5, attempts=3)
    collected: list[CsvObservation] = []
    failures: list[str] = []
    async with raw, nbs_raw:
        for spec in china_macro_sources.EASTMONEY_SPECS:
            try:
                collected.extend(await china_macro_sources.fetch_eastmoney(client, spec))
            except ProviderError as exc:
                failures.append(f"{spec.series_code}: {exc.message}")
        try:
            collected.extend(await china_macro_sources.fetch_social_financing(client))
        except ProviderError as exc:
            failures.append(f"SOCIAL_FINANCING: {exc.message}")
        for request in nbs_macro.NBS_REQUESTS:
            try:
                collected.extend(await nbs_macro.fetch_nbs(nbs_client, request, day))
            except ProviderError as exc:
                names = "/".join(item.series_code for item in request.indicators)
                failures.append(f"{names}: {exc.message}")
    written = 0
    if collected:
        written, _duplicates = MacroChinaRepository(engine).import_observations(collected)
    if failures:
        raise ProviderError(
            code="UPSTREAM_PARTIAL",
            message=f"已写入 {written} 条；以下序列抓取失败：" + "；".join(failures),
            status_code=502,
        )
    return written


async def sync_us_bars(
    engine: Engine, *, display_symbol: str, start: date, end: date, token: str
) -> int:
    instrument_id = _instrument_id(engine, "US", display_symbol)
    raw, client = _client(
        trust_env=True,
        timeout=20.0,
        min_interval=tiingo_bars.TIINGO_MIN_INTERVAL_SECONDS,
        attempts=2,
    )
    async with raw:
        try:
            bars = await tiingo_bars.fetch_daily(client, display_symbol, start, end, token)
        except ProviderError as exc:
            raise _missing_upstream(exc, "Tiingo 没有这个代码的日线") from exc
    repository = MarketBarRepository(engine)
    outcome = ingest_bars(
        list(bars),
        repository,
        instrument_id=instrument_id,
        provider_id=repository.ensure_tiingo_provider(),
    )
    if outcome.error is not None:
        raise outcome.error
    return outcome.rows_written


async def sync_us_universe(engine: Engine, *, user_agent: str) -> int:
    raw, client = _client(trust_env=True, timeout=60.0, min_interval=SEC_MIN_INTERVAL_SECONDS)
    async with raw:
        listings = await universe.fetch_us_listings(client, user_agent)
    return UniverseRepository(engine).upsert_us(listings)


async def sync_cn_universe(engine: Engine) -> int:
    raw, client = _client(trust_env=False, timeout=30.0, min_interval=0.3, attempts=4)
    async with raw:
        listings = await universe.fetch_cn_listings(client)
    return UniverseRepository(engine).upsert_cn(listings)


def bars_window(last_bar: date | None, today: date) -> tuple[date, date] | None:
    """增量窗口：从最后一根日线的次日到今天；已是最新则不需要抓。"""

    if last_bar is None:
        return today - timedelta(days=FIRST_BARS_LOOKBACK_DAYS), today
    start = last_bar + timedelta(days=1)
    return (start, today) if start <= today else None


def _instrument_id(engine: Engine, market: str, display_symbol: str) -> UUID:
    with Session(engine) as session:
        found = session.scalars(
            select(Instrument.id).where(
                Instrument.market == market, Instrument.display_symbol == display_symbol
            )
        ).first()
    if found is None:
        raise ProviderError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    return found
