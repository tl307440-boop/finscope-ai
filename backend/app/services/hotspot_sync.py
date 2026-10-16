"""热点事件定时刷新：只收巨潮资讯 / SEC EDGAR 的官方披露，不碰新闻站点。

热度只是「近期披露条数」，不含任何情绪或评分；单次最多处理一批被关注的公司，
免费数据源按固定间隔限速，抓不到的公司明确报失败，不拿旧数据冒充。
"""

import asyncio
from datetime import date, timedelta

import httpx
from sqlalchemy import Engine

from app.providers.disclosures import (
    USAGE_RIGHTS,
    fetch_cninfo_announcements,
    fetch_cninfo_stock_list,
    fetch_sec_submissions,
)
from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.providers.sec_edgar import SEC_MIN_INTERVAL_SECONDS
from app.repositories.news_events import NewsEventRepository
from app.repositories.universe import TrackedCompany, UniverseRepository
from app.services.disclosure_events import DisclosureDraft, NewsEventInput

HOTSPOT_SOURCE = "disclosures"
LOOKBACK_DAYS = 14
MAX_COMPANIES_PER_MARKET = 60


def _client(
    *, trust_env: bool, min_interval: float
) -> tuple[httpx.AsyncClient, ResilientHttpClient]:
    raw = httpx.AsyncClient(timeout=httpx.Timeout(20.0), trust_env=trust_env)
    return raw, ResilientHttpClient(
        HttpxTransport(raw),
        sleeper=asyncio.sleep,
        min_interval_seconds=min_interval,
        max_attempts=3,
    )


async def sync_hotspot_events(
    engine: Engine, *, source: str, observed_on: date, user_agent: str
) -> int:
    if source != HOTSPOT_SOURCE:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message=f"未登记的热点来源 {source!r}；目前只支持官方披露（{HOTSPOT_SOURCE}）",
            status_code=422,
        )
    start = observed_on - timedelta(days=LOOKBACK_DAYS)
    universe = UniverseRepository(engine)
    cn_companies = universe.tracked_cn_stocks()[:MAX_COMPANIES_PER_MARKET]
    us_companies = universe.tracked_us()[:MAX_COMPANIES_PER_MARKET]
    drafts: list[DisclosureDraft] = []
    failures: list[str] = []
    drafts.extend(await _cn_disclosures(cn_companies, start, observed_on, failures))
    drafts.extend(await _us_disclosures(us_companies, start, user_agent, failures))
    accepted = 0
    if drafts:
        items = [
            NewsEventInput(index=index, draft=draft, usage_rights=USAGE_RIGHTS)
            for index, draft in enumerate(drafts, start=1)
        ]
        accepted = NewsEventRepository(engine).import_events(items).accepted
    if failures:
        shown = "；".join(failures[:5])
        more = f" 等 {len(failures)} 项" if len(failures) > 5 else ""
        raise ProviderError(
            code="UPSTREAM_PARTIAL",
            message=f"已新增 {accepted} 条披露；以下抓取失败：{shown}{more}",
            status_code=502,
        )
    return accepted


async def _cn_disclosures(
    companies: list[TrackedCompany], start: date, end: date, failures: list[str]
) -> list[DisclosureDraft]:
    if not companies:
        return []
    found: list[DisclosureDraft] = []
    raw, client = _client(trust_env=False, min_interval=0.4)
    async with raw:
        try:
            stocks = await fetch_cninfo_stock_list(client)
        except ProviderError as exc:
            failures.append(f"巨潮证券列表：{exc.message}")
            return []
        for company in companies:
            code, _, suffix = company.display_symbol.partition(".")
            stock = stocks.get(code)
            if stock is None:
                continue
            try:
                found.extend(
                    await fetch_cninfo_announcements(
                        client,
                        stock=stock,
                        exchange="SH" if suffix.upper() == "SH" else "SZ",
                        start=start,
                        end=end,
                    )
                )
            except ProviderError as exc:
                failures.append(f"{company.display_symbol}：{exc.message}")
    return found


async def _us_disclosures(
    companies: list[TrackedCompany], start: date, user_agent: str, failures: list[str]
) -> list[DisclosureDraft]:
    targets = [company for company in companies if company.cik]
    if not targets:
        return []
    if not user_agent.strip():
        failures.append("SEC：未配置 User-Agent，已跳过美股披露")
        return []
    found: list[DisclosureDraft] = []
    raw, client = _client(trust_env=True, min_interval=SEC_MIN_INTERVAL_SECONDS)
    async with raw:
        for company in targets:
            try:
                found.extend(
                    await fetch_sec_submissions(
                        client,
                        cik=str(company.cik),
                        display_name=company.display_symbol,
                        user_agent=user_agent,
                        start=start,
                    )
                )
            except ProviderError as exc:
                failures.append(f"{company.display_symbol}：{exc.message}")
    return found
