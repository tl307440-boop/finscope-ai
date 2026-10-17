"""市场情绪：涨停 / 炸板 / 跌停股池与机构调研，统计全部在后端完成。

- 股池默认取最近一个有数据的交易日（往回最多找 7 天）；当天交易时段内缓存 60 秒，历史日缓存 6 小时。
- 炸板池、跌停池失败只让对应数字为空，不当成 0。
- 机构调研按公告日统计；行业来自东财个股行情的所属行业，拿不到时行业分布单独标不可用。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeVar

from app.providers import eastmoney_boards as em
from app.providers import eastmoney_sentiment as es
from app.providers.errors import ProviderError
from app.schemas.industry_market import Section, SourceRef
from app.schemas.market_sentiment import (
    CountItem,
    LadderStep,
    LimitUpView,
    PoolStockItem,
    SurveyEventItem,
    SurveyStockItem,
    SurveyView,
)
from app.services.industry_market import Gateway
from app.services.market_cache import Cached, TtlCache, shared_gateway
from app.utils.market_time import SHANGHAI
from app.utils.time import utc_now

T = TypeVar("T")

_TTL_POOL_LIVE = 60
_TTL_POOL_PAST = 6 * 3600
_TTL_SURVEYS = 1800
_LOOKBACK_DAYS = 7
_MAX_SURVEY_PAGES = 6
_INDUSTRY_LOOKUP = 80
_AUCTION_DONE = time(9, 25)
_HUNDRED = Decimal(100)

SEAL_BUCKETS: tuple[tuple[str, str, str], ...] = (
    ("竞价封板", "00:00:00", "09:30:00"),
    ("09:30-10:00", "09:30:00", "10:00:00"),
    ("10:00-11:30", "10:00:00", "11:30:01"),
    ("13:00-14:00", "11:30:01", "14:00:00"),
    ("14:00-15:00", "14:00:00", "15:00:01"),
)

LIMIT_UP_RULES = [
    "股池来自东方财富「涨停板行情」公开接口：涨停池、炸板池（盘中触及涨停后打开）、跌停池。",
    "封板率 = 涨停家数 /（涨停家数 + 炸板家数）；连板梯队按连板数分组。",
    "首次封板时间按东财给出的 HH:MM:SS 分档；ST 股涨跌幅限制 5%，北交所 30%，均按东财口径计入。",
    "情绪数据只描述当天盘面，不构成买卖建议。",
]
SURVEY_RULES = [
    "数据来自上市公司公告披露的机构接待记录（东方财富数据中心 RPT_ORG_SURVEYNEW），按公告日统计。",
    "一条记录是一次接待活动；参与机构数为公告列明的机构家数，业绩说明会等面向投资者的活动也计入。",
    "行业为东财个股所属行业，只对接待次数最多的前 80 只股票查询。",
]


@dataclass
class MarketSentimentService:
    gateway: Gateway
    cache: TtlCache = field(default_factory=TtlCache)
    now: Callable[[], datetime] = field(default=utc_now)

    async def limit_up(self, day: date | None = None) -> LimitUpView:
        local = self.now().astimezone(SHANGHAI)
        target, page = await self._pool_for(day, local)
        source = SourceRef(
            name="东方财富 涨停板行情",
            url="https://quote.eastmoney.com/ztb/detail#type=ztgc",
            fetched_at=page.fetched_at,
        )
        if page.value.trade_date is None:
            return _empty_pool(
                Section(
                    status="unavailable",
                    reason=f"{target.isoformat()} 不是交易日，或东财没有该日股池",
                ),
                source,
            )
        broken, broken_reason = await self._optional_pool("broken", target, local)
        limit_down, down_reason = await self._optional_pool("limit_down", target, local)
        reasons = [reason for reason in (broken_reason, down_reason) if reason]
        stocks = list(page.value.stocks)
        zt, zb = len(stocks), None if broken is None else len(broken)
        section = Section(
            status="stale" if page.stale else "ok",
            reason="；".join(reasons) or None,
            fetched_at=page.fetched_at,
            data_date=page.value.trade_date,
        )
        return LimitUpView(
            section=section,
            trade_date=page.value.trade_date,
            limit_up=zt,
            broken=zb,
            limit_down=None if limit_down is None else len(limit_down),
            seal_rate=seal_rate(zt, zb),
            max_streak=max((stock.streak or 1 for stock in stocks), default=0),
            ladder=streak_ladder(stocks),
            industries=top_counts((stock.industry for stock in stocks), 12),
            seal_times=seal_time_buckets(stocks),
            limit_up_stocks=[_pool_item(stock) for stock in stocks],
            broken_stocks=[_pool_item(stock) for stock in broken or []],
            limit_down_stocks=[_pool_item(stock) for stock in limit_down or []],
            rules=LIMIT_UP_RULES,
            sources=[source],
        )

    async def survey_counts(self, days: int) -> dict[str, tuple[int, int]]:
        """股票代码 →（接待次数，参与机构家数）。"""

        _, cached = await self._survey_rows(days)
        return {
            stock.code: (stock.events, stock.institutions)
            for stock in aggregate_surveys(cached.value)
        }

    async def _survey_rows(self, days: int) -> tuple[date, Cached[tuple[es.OrgSurvey, ...]]]:
        since = self.now().astimezone(SHANGHAI).date() - timedelta(days=days)
        cached = await self.cache.get(
            f"surveys:{since.isoformat()}", _TTL_SURVEYS, lambda: self._load_surveys(since)
        )
        return since, cached

    async def surveys(self, days: int) -> SurveyView:
        source = SourceRef(
            name="东方财富 机构调研（上市公司公告）",
            url="https://data.eastmoney.com/jgdy/",
        )
        since, cached = await self._survey_rows(days)
        rows = cached.value
        stocks = aggregate_surveys(rows)
        industries: dict[str, str] = {}
        industry_section = Section(status="unavailable", reason="没有调研记录")
        if stocks:
            try:
                quotes = await self.cache.get(
                    f"survey-industries:{since.isoformat()}",
                    _TTL_SURVEYS,
                    lambda: self._industries([stock.code for stock in stocks[:_INDUSTRY_LOOKUP]]),
                )
                industries = quotes.value
                industry_section = _section(quotes)
            except ProviderError as error:
                industry_section = Section(status="unavailable", reason=error.message)
        return SurveyView(
            section=_section(cached),
            days=days,
            since=since,
            events=len(rows),
            stocks=len(stocks),
            institutions=sum(row.institutions for row in rows),
            daily=[
                CountItem(label=day.isoformat(), count=count)
                for day, count in sorted(Counter(row.notice_date for row in rows).items())
            ],
            ways=top_counts(
                (way.strip() for row in rows for way in (row.receive_way or "").split(",")), 8
            ),
            industries=top_counts(
                (industries.get(stock.code) for stock in stocks[:_INDUSTRY_LOOKUP]), 12
            ),
            industry_section=industry_section,
            top_stocks=[
                stock.model_copy(update={"industry": industries.get(stock.code)})
                for stock in stocks[:30]
            ],
            recent=[
                SurveyEventItem(
                    code=row.code,
                    name=row.name,
                    notice_date=row.notice_date,
                    receive_date=row.receive_date,
                    institutions=row.institutions,
                    receive_way=row.receive_way,
                )
                for row in rows[:40]
            ],
            rules=SURVEY_RULES,
            sources=[source.model_copy(update={"fetched_at": cached.fetched_at})],
        )

    async def _pool_for(
        self, day: date | None, local: datetime
    ) -> tuple[date, Cached[es.PoolPage]]:
        if day is not None:
            return day, await self._pool("limit_up", day, local)
        candidate = local.date()
        if local.time() < _AUCTION_DONE:
            candidate -= timedelta(days=1)
        last: tuple[date, Cached[es.PoolPage]] | None = None
        for _ in range(_LOOKBACK_DAYS):
            if candidate.weekday() < 5:
                page = await self._pool("limit_up", candidate, local)
                last = (candidate, page)
                if page.value.trade_date is not None:
                    return last
            candidate -= timedelta(days=1)
        if last is None:
            raise ProviderError(code="NO_ROWS", message="最近一周没有可用的股池", status_code=503)
        return last

    async def _pool(self, kind: es.PoolKind, day: date, local: datetime) -> Cached[es.PoolPage]:
        ttl = _TTL_POOL_LIVE if day >= local.date() else _TTL_POOL_PAST
        return await self.cache.get(
            f"pool:{kind}:{day.isoformat()}",
            ttl,
            lambda: self._fetch(es.pool_url(kind, day), es.parse_pool),
        )

    async def _optional_pool(
        self, kind: es.PoolKind, day: date, local: datetime
    ) -> tuple[list[es.PoolStock] | None, str | None]:
        label = "炸板池" if kind == "broken" else "跌停池"
        try:
            page = await self._pool(kind, day, local)
        except ProviderError as error:
            return None, f"{label}暂不可用：{error.message}"
        return list(page.value.stocks), None

    async def _load_surveys(self, since: date) -> tuple[es.OrgSurvey, ...]:
        rows: list[es.OrgSurvey] = []
        for page in range(1, _MAX_SURVEY_PAGES + 1):
            batch = await self._fetch(es.survey_url(since, page), es.parse_surveys)
            rows.extend(batch.rows)
            if page >= batch.pages or not batch.rows:
                break
        return tuple(rows)

    async def _industries(self, codes: list[str]) -> dict[str, str]:
        secids = [secid for code in codes if (secid := stock_secid(code)) is not None]
        if not secids:
            return {}
        quotes = await self._fetch(em.stock_quote_url(secids), em.parse_stock_quotes)
        return {code: quote.industry for code, quote in quotes.items() if quote.industry}

    async def _fetch(self, url: str, parse: Callable[[bytes], T]) -> T:
        return parse(await self.gateway.get(url, _HEADERS))


_HEADERS: Mapping[str, str] = em.EASTMONEY_HEADERS


def stock_secid(code: str) -> str | None:
    """沪市 6 开头 → 1.，深市 0 / 3 开头与北交所 4 / 8 / 92 开头 → 0.；其余不认。"""

    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith("6"):
        return f"1.{code}"
    if code[0] in "0348" or code.startswith("92"):
        return f"0.{code}"
    return None


def seal_rate(limit_up: int, broken: int | None) -> Decimal | None:
    if broken is None or limit_up + broken == 0:
        return None
    return (Decimal(limit_up) / Decimal(limit_up + broken) * _HUNDRED).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def streak_ladder(stocks: list[es.PoolStock]) -> list[LadderStep]:
    groups: dict[int, list[es.PoolStock]] = {}
    for stock in stocks:
        groups.setdefault(stock.streak or 1, []).append(stock)
    return [
        LadderStep(
            streak=streak,
            stocks=[
                _pool_item(stock)
                for stock in sorted(rows, key=lambda item: item.first_seal or "99")
            ],
        )
        for streak, rows in sorted(groups.items(), reverse=True)
    ]


def seal_time_buckets(stocks: list[es.PoolStock]) -> list[CountItem]:
    counts = [0 for _ in SEAL_BUCKETS]
    for stock in stocks:
        if stock.first_seal is None:
            continue
        for index, (_, low, high) in enumerate(SEAL_BUCKETS):
            if low <= stock.first_seal < high:
                counts[index] += 1
                break
    return [CountItem(label=label, count=counts[i]) for i, (label, _, _) in enumerate(SEAL_BUCKETS)]


def top_counts(values: Iterable[str | None], limit: int) -> list[CountItem]:
    counter: Counter[str] = Counter()
    for value in values:
        if value:
            counter[value] += 1
    return [CountItem(label=label, count=count) for label, count in counter.most_common(limit)]


def aggregate_surveys(rows: tuple[es.OrgSurvey, ...]) -> list[SurveyStockItem]:
    grouped: dict[str, list[es.OrgSurvey]] = {}
    for row in rows:
        grouped.setdefault(row.code, []).append(row)
    stocks = [
        SurveyStockItem(
            code=code,
            name=items[0].name,
            events=len(items),
            institutions=sum(item.institutions for item in items),
            latest=max(item.notice_date for item in items),
            ways=sorted(
                {
                    way.strip()
                    for item in items
                    for way in (item.receive_way or "").split(",")
                    if way.strip()
                }
            ),
        )
        for code, items in grouped.items()
    ]
    return sorted(stocks, key=lambda item: (item.institutions, item.events), reverse=True)


def _pool_item(stock: es.PoolStock) -> PoolStockItem:
    return PoolStockItem(**vars(stock))


def _empty_pool(section: Section, source: SourceRef) -> LimitUpView:
    return LimitUpView(
        section=section,
        limit_up=0,
        max_streak=0,
        ladder=[],
        industries=[],
        seal_times=[],
        limit_up_stocks=[],
        broken_stocks=[],
        limit_down_stocks=[],
        rules=LIMIT_UP_RULES,
        sources=[source],
    )


def _section(cached: Cached[Any]) -> Section:
    if cached.stale:
        return Section(
            status="stale",
            reason=f"上游刷新失败，展示上次成功抓取的数据：{cached.stale_reason}",
            fetched_at=cached.fetched_at,
        )
    return Section(status="ok", fetched_at=cached.fetched_at)


_service: MarketSentimentService | None = None


def get_sentiment_service() -> MarketSentimentService:
    global _service
    if _service is None:
        _service = MarketSentimentService(gateway=shared_gateway())
    return _service
