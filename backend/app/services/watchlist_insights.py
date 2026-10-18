"""自选股看板：自选里只存证券，行情、行业、资金、涨停、调研、热度全部从公开数据现查。

- 实时行情来自东财个股行情；东财不可用时改用腾讯个股行情（多日涨跌按入库日线 + 腾讯最新价计算），
  两者都拿不到时用本系统已入库的日线收盘价计算涨跌幅，并标 stored / stale。
- 涨停状态、机构调研、热榜排名各自独立获取，某一项失败只在 signal_section 里说明，不影响其他列。
- 汇总与行业暴露都在后端算：等权平均涨跌、市值加权涨跌、主力净流入合计、各行业市值占比。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.providers import eastmoney_boards as em
from app.providers import tencent_market as tx
from app.providers.errors import ProviderError
from app.repositories.watchlists import RecentClose, WatchGroup, WatchItem
from app.schemas.industry_market import Section, SectionStatus, SourceRef
from app.schemas.watchlist_insights import (
    ExposureItem,
    WatchlistInsights,
    WatchOther,
    WatchStock,
    WatchSummary,
)
from app.services.industry_market import Gateway, IndustryMarketService, get_service
from app.services.market_cache import TtlCache, shared_gateway
from app.services.market_sentiment import MarketSentimentService, get_sentiment_service
from app.utils.market_time import SHANGHAI
from app.utils.time import utc_now
from app.utils.watch_secid import (
    fund_market,
    kind_label,
    kind_of_secid,
    make_secid,
    parse_secid,
)

MAX_IMPORT = 300
_TTL_QUOTES = 60
_TENCENT_BATCH = 60
_EM_SOURCE = ("东方财富 个股行情", "https://quote.eastmoney.com/")
_TENCENT_SOURCE = ("腾讯行情 个股快照", "https://gu.qq.com/")
_STORED_SOURCE = ("本系统入库日线", "https://quote.eastmoney.com/")
_TWO = Decimal("0.01")
_HUNDRED = Decimal(100)
_EXCHANGES = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
_TOKEN = re.compile(r"(?i)^(?:(sh|sz|bj)|([01])\.)?(\d{6})(?:\.(sh|sz|bj))?$")
_SPLIT = re.compile(r"[\s,，;；、|/]+")

RULES = [
    "自选里只保存证券；行情、所属行业、主力资金、涨停状态、机构调研与热榜排名"
    "都从东财 / 同花顺公开数据现查。",
    "5 日 / 10 日 / 60 日 / 年初至今涨跌幅为东财口径；"
    "实时不可用时改用本系统入库日线按交易日数计算。",
    "市值加权涨跌 = Σ(总市值 × 当日涨跌幅) / Σ总市值，只统计有市值和涨跌幅的股票。",
    "机构调研为近 30 天公告披露的接待次数与机构家数；热榜为同花顺 / 东财人气榜前 100 内的名次。",
    "板块、指数、美股、基金、国债收益率等不是本系统收录的沪深京个股，单独列在「其他标的」，"
    "只显示东财的价格与涨跌，不进入涨跌家数、等权 / 市值加权涨跌、行业暴露和信号统计。",
]


@dataclass(frozen=True)
class ParsedTargets:
    """cn：A 股候选 (交易所, 6 位代码)；external：东财 secid；invalid：认不出的写法。"""

    cn: list[tuple[str, str]]
    external: list[str]
    invalid: list[str]


def parse_targets(text: str) -> ParsedTargets:
    """把粘贴的文本拆成 A 股候选与外部标的。

    A 股个股：600519、SH600519、600519.SH、1.600519（1. 沪、0. 深京）。
    其他标的：东财 secid，如 90.BK1630（板块）、105.NVDA（美股）、171.US10Y（国债收益率）；
    6 位基金 / 债券代码没写前缀时按号段推断沪深（513120 -> 1.513120）。
    """

    cn: list[tuple[str, str]] = []
    external: list[str] = []
    invalid: list[str] = []
    for token in _SPLIT.split(text.strip()):
        if not token:
            continue
        match = _TOKEN.match(token)
        if match is None:
            parsed = parse_secid(token)
            if parsed is None:
                invalid.append(token)
            else:
                external.append(make_secid(*parsed))
            continue
        prefix, digit, code, suffix = match.groups()
        marker = (prefix or suffix or "").upper()
        exchange: str | None
        if marker:
            exchange = _EXCHANGES[marker]
        elif digit == "1":
            exchange = "SSE"
        elif digit == "0":
            exchange = "BSE" if code.startswith(("4", "8", "92")) else "SZSE"
        else:
            exchange = infer_exchange(code)
        if exchange is not None:
            cn.append((exchange, code))
            continue
        market = fund_market(code)
        if market is None:
            invalid.append(token)
        else:
            external.append(make_secid(market, code))
    return ParsedTargets(
        cn=list(dict.fromkeys(cn)), external=list(dict.fromkeys(external)), invalid=invalid
    )


def infer_exchange(code: str) -> str | None:
    if code.startswith("6"):
        return "SSE"
    if code[0] in "03":
        return "SZSE"
    if code[0] in "48" or code.startswith("92"):
        return "BSE"
    return None


def secid_for(exchange: str, code: str) -> str:
    return f"{'1' if exchange == 'SSE' else '0'}.{code}"


def window_change(closes: Sequence[RecentClose], sessions: int) -> Decimal | None:
    if len(closes) <= sessions or closes[-1 - sessions].close == 0:
        return None
    return _round((closes[-1].close / closes[-1 - sessions].close - 1) * _HUNDRED)


def ytd_change(closes: Sequence[RecentClose], year: int) -> Decimal | None:
    base = next((row.close for row in reversed(closes) if row.trade_date.year < year), None)
    if base is None or base == 0 or closes[-1].trade_date.year != year:
        return None
    return _round((closes[-1].close / base - 1) * _HUNDRED)


@dataclass
class WatchlistInsightsService:
    gateway: Gateway
    industry: IndustryMarketService
    sentiment: MarketSentimentService
    cache: TtlCache = field(default_factory=TtlCache)
    now: Callable[[], datetime] = field(default=utc_now)

    async def build(
        self,
        group: WatchGroup,
        recent: Callable[[Sequence[UUID], date], dict[UUID, list[RecentClose]]],
    ) -> WatchlistInsights:
        supported = [item for item in group.items if _supported(item)]
        today = self.now().astimezone(SHANGHAI).date()
        stocks, quote_section, quote_source = await self._quotes(supported, recent, today)
        others, others_section = await self._others(
            [item for item in group.items if not _supported(item)]
        )
        names, signals, signal_section = await self._signals()
        for index, stock in enumerate(stocks):
            stocks[index] = stock.model_copy(
                update={
                    "board_code": names.get(_plain(stock.industry)) if stock.industry else None,
                    **signals.get(stock.code, {}),
                }
            )
        return WatchlistInsights(
            group_id=group.id,
            group_name=group.name,
            quote_section=quote_section,
            signal_section=signal_section,
            summary=summarise(stocks),
            stocks=stocks,
            industries=exposure(stocks),
            others=others,
            others_section=others_section,
            rules=RULES,
            sources=[
                SourceRef(
                    name=quote_source[0], url=quote_source[1], fetched_at=quote_section.fetched_at
                ),
                SourceRef(name="东方财富 涨停板行情", url="https://quote.eastmoney.com/ztb/"),
                SourceRef(name="东方财富 机构调研", url="https://data.eastmoney.com/jgdy/"),
                SourceRef(
                    name="同花顺 / 东方财富 热榜",
                    url="https://eq.10jqka.com.cn/frontend/thsTopRank/index.html",
                ),
            ],
        )

    async def _quotes(
        self,
        items: list[WatchItem],
        recent: Callable[[Sequence[UUID], date], dict[UUID, list[RecentClose]]],
        today: date,
    ) -> tuple[list[WatchStock], Section, tuple[str, str]]:
        if not items:
            return [], Section(status="unavailable", reason="这个分组里没有 A 股"), _EM_SOURCE
        secids = sorted(secid_for(item.exchange, item.symbol) for item in items)
        try:
            cached = await self.cache.get(
                "watch:" + ",".join(secids), _TTL_QUOTES, lambda: self._load_quotes(secids)
            )
        except ProviderError as error:
            since = min(today - timedelta(days=130), date(today.year - 1, 12, 15))
            closes = await asyncio.to_thread(recent, [_iid(item) for item in items], since)
            symbols = sorted(tx.a_share_symbol(item.symbol) for item in items)
            try:
                tencent = await self.cache.get(
                    "watch-tx:" + ",".join(symbols),
                    _TTL_QUOTES,
                    lambda: self._load_tencent(symbols),
                )
            except ProviderError:
                tencent = None
            if tencent is not None and tencent.value:
                stocks = [
                    _tencent_stock(
                        item,
                        tencent.value.get(tx.a_share_symbol(item.symbol)),
                        closes.get(_iid(item), []),
                        today,
                    )
                    for item in items
                ]
                return (
                    stocks,
                    Section(
                        status="stale" if tencent.stale else "ok",
                        reason=f"东财行情暂不可用（{error.message}），已改用腾讯行情；"
                        "5 / 10 / 60 日与年初至今涨跌按入库日线 + 腾讯最新价计算",
                        fetched_at=tencent.fetched_at,
                    ),
                    _TENCENT_SOURCE,
                )
            stocks = [_stored_stock(item, closes.get(_iid(item), []), today) for item in items]
            dates = [row.trade_date for rows in closes.values() for row in rows[-1:]]
            section = Section(
                status="stale" if dates else "unavailable",
                reason=(
                    f"实时行情暂不可用（{error.message}），涨跌幅按入库日线计算"
                    if dates
                    else f"实时行情暂不可用（{error.message}），本系统也还没有这些股票的日线"
                ),
                origin="stored",
                data_date=max(dates) if dates else None,
            )
            return stocks, section, _STORED_SOURCE
        quotes = cached.value
        stocks = [_live_stock(item, quotes.get(item.symbol)) for item in items]
        section = Section(
            status="stale" if cached.stale else "ok",
            reason=cached.stale_reason,
            fetched_at=cached.fetched_at,
        )
        return stocks, section, _EM_SOURCE

    async def _others(self, items: list[WatchItem]) -> tuple[list[WatchOther], Section]:
        """板块、指数、美股、基金等：只取价格与涨跌，不进入 A 股汇总。"""

        if not items:
            return [], Section(status="ok")
        secids = sorted({item.secid for item in items if item.secid})
        quotes: dict[str, em.MemberQuote] = {}
        fetched_at: datetime | None = None
        stale_reason: str | None = None
        status: SectionStatus = "ok"
        if secids:
            try:
                cached = await self.cache.get(
                    "watch-other:" + ",".join(secids),
                    _TTL_QUOTES,
                    lambda: self._load_secid_quotes(secids),
                )
            except ProviderError as error:
                status, stale_reason = "unavailable", f"东财行情暂不可用（{error.message}）"
            else:
                quotes, fetched_at = cached.value, cached.fetched_at
                if cached.stale:
                    status, stale_reason = "stale", cached.stale_reason
        rows = [_other(item, quotes.get(item.secid or "")) for item in items]
        silent = [row.display_symbol for row in rows if row.last is None]
        if status == "ok" and silent:
            status = "stale"
            stale_reason = "东财没有返回这些标的的行情：" + "、".join(silent[:12])
            if len(silent) > 12:
                stale_reason += f" 等 {len(silent)} 个"
        return rows, Section(status=status, reason=stale_reason, fetched_at=fetched_at)

    async def resolve_names(self, secids: Sequence[str]) -> dict[str, str]:
        """导入时顺带取标的名称；东财不可用就返回空，标的照样保存，名称之后看板里现查。"""

        unique = sorted(set(secids))
        names: dict[str, str] = {}
        pending = unique
        # 东财偶尔会少回几行；缺的补查一次（真不存在的代码也只多一次小请求）。
        for _ in range(2):
            if not pending:
                break
            try:
                quotes = await self._load_secid_quotes(pending)
            except ProviderError:
                break
            names.update({secid: quote.name for secid, quote in quotes.items() if quote.name})
            pending = [secid for secid in unique if secid not in names]
        return names

    async def _load_secid_quotes(self, secids: list[str]) -> dict[str, em.MemberQuote]:
        quotes: dict[str, em.MemberQuote] = {}
        for start in range(0, len(secids), em.PAGE_SIZE):
            payload = await self.gateway.get(
                em.watch_quote_url(secids[start : start + em.PAGE_SIZE]), em.EASTMONEY_HEADERS
            )
            quotes.update(em.parse_secid_quotes(payload))
        return quotes

    async def _load_tencent(self, symbols: list[str]) -> dict[str, tx.Quote]:
        quotes: dict[str, tx.Quote] = {}
        for start in range(0, len(symbols), _TENCENT_BATCH):
            payload = await self.gateway.get(
                tx.quote_url(symbols[start : start + _TENCENT_BATCH]), tx.TENCENT_HEADERS
            )
            quotes.update(tx.parse_quotes(payload))
        return quotes

    async def _load_quotes(self, secids: list[str]) -> dict[str, em.MemberQuote]:
        quotes: dict[str, em.MemberQuote] = {}
        for start in range(0, len(secids), em.PAGE_SIZE):
            payload = await self.gateway.get(
                em.watch_quote_url(secids[start : start + em.PAGE_SIZE]), em.EASTMONEY_HEADERS
            )
            quotes.update(em.parse_watch_quotes(payload))
        return quotes

    async def _signals(
        self,
    ) -> tuple[dict[str, str], dict[str, dict[str, object]], Section]:
        signals: dict[str, dict[str, object]] = {}
        problems: list[str] = []
        names: dict[str, str] = {}
        try:
            names = await self.industry.board_names()
        except ProviderError as error:
            problems.append(f"行业板块目录：{error.message}")
        try:
            pool = await self.sentiment.limit_up()
            for status, rows in (
                ("涨停", pool.limit_up_stocks),
                ("炸板", pool.broken_stocks),
                ("跌停", pool.limit_down_stocks),
            ):
                for row in rows:
                    signals.setdefault(row.code, {}).update(
                        {"limit_status": status, "streak": row.streak if status == "涨停" else None}
                    )
            if pool.section.status == "unavailable":
                problems.append(f"涨停池：{pool.section.reason}")
        except ProviderError as error:
            problems.append(f"涨停池：{error.message}")
        try:
            for code, (events, institutions) in (await self.sentiment.survey_counts(30)).items():
                signals.setdefault(code, {}).update(
                    {"surveys": events, "surveyed_institutions": institutions}
                )
        except ProviderError as error:
            problems.append(f"机构调研：{error.message}")
        hot = await self.industry.hot()
        for ths in hot.ths_stocks:
            signals.setdefault(ths.code, {})["ths_hot_rank"] = ths.rank
        for popular in hot.eastmoney:
            signals.setdefault(popular.code, {})["em_hot_rank"] = popular.rank
        if hot.ths_section.status == "unavailable":
            problems.append(f"同花顺热榜：{hot.ths_section.reason}")
        if hot.eastmoney_section.status == "unavailable":
            problems.append(f"东财人气榜：{hot.eastmoney_section.reason}")
        section = Section(
            status="stale" if problems else "ok",
            reason="；".join(problems) or None,
        )
        return names, signals, section


def summarise(stocks: list[WatchStock]) -> WatchSummary:
    quoted = [stock for stock in stocks if stock.change_pct is not None]
    changes = [stock.change_pct for stock in quoted if stock.change_pct is not None]
    weighted = [
        (stock.market_cap, stock.change_pct)
        for stock in quoted
        if stock.market_cap and stock.change_pct is not None
    ]
    weight = sum((cap for cap, _ in weighted), Decimal(0))
    inflows = [stock.main_net_inflow for stock in stocks if stock.main_net_inflow is not None]
    return WatchSummary(
        count=len(stocks),
        quoted=len(quoted),
        up=sum(1 for value in changes if value > 0),
        down=sum(1 for value in changes if value < 0),
        flat=sum(1 for value in changes if value == 0),
        average_change_pct=_round(sum(changes, Decimal(0)) / len(changes)) if changes else None,
        weighted_change_pct=(
            _round(sum((cap * pct for cap, pct in weighted), Decimal(0)) / weight)
            if weight
            else None
        ),
        total_main_net_inflow=sum(inflows, Decimal(0)) if inflows else None,
        limit_up=sum(1 for stock in stocks if stock.limit_status == "涨停"),
        surveyed=sum(1 for stock in stocks if stock.surveys),
        hot=sum(1 for stock in stocks if stock.ths_hot_rank or stock.em_hot_rank),
    )


def exposure(stocks: list[WatchStock]) -> list[ExposureItem]:
    groups: dict[str, list[WatchStock]] = {}
    for stock in stocks:
        if stock.industry:
            groups.setdefault(stock.industry, []).append(stock)
    total = sum((stock.market_cap or Decimal(0) for stock in stocks if stock.industry), Decimal(0))
    items: list[ExposureItem] = []
    for name, rows in groups.items():
        cap = sum((row.market_cap or Decimal(0) for row in rows), Decimal(0))
        weighted = [
            (row.market_cap, row.change_pct)
            for row in rows
            if row.market_cap and row.change_pct is not None
        ]
        weight = sum((value for value, _ in weighted), Decimal(0))
        items.append(
            ExposureItem(
                name=name,
                board_code=rows[0].board_code,
                count=len(rows),
                market_cap=cap,
                weight_pct=_round(cap / total * _HUNDRED) if total else None,
                weighted_change_pct=(
                    _round(sum((value * pct for value, pct in weighted), Decimal(0)) / weight)
                    if weight
                    else None
                ),
            )
        )
    return sorted(items, key=lambda item: (item.market_cap, item.count), reverse=True)


def _supported(item: WatchItem) -> bool:
    """能进入 A 股汇总（涨跌家数、行业暴露、各类信号）的只有本系统收录的沪深京个股。"""

    return (
        item.instrument_id is not None
        and item.market == "CN"
        and item.exchange in ("SSE", "SZSE", "BSE")
        and len(item.symbol) == 6
    )


def _other(item: WatchItem, quote: em.MemberQuote | None) -> WatchOther:
    kind = kind_of_secid(item.secid) if item.secid else "other"
    base = WatchOther(
        item_id=item.item_id,
        instrument_id=item.instrument_id,
        display_symbol=item.display_symbol,
        secid=item.secid,
        code=item.symbol,
        name=item.name_cn,
        kind=kind,
        kind_label=kind_label(kind),
    )
    if quote is None:
        return base
    return base.model_copy(
        update={
            "name": quote.name or item.name_cn,
            "last": quote.last,
            "change_pct": quote.change_pct,
            "change_5d_pct": quote.change_5d_pct,
            "change_60d_pct": quote.change_60d_pct,
            "change_ytd_pct": quote.change_ytd_pct,
            "amount": quote.amount,
        }
    )


def _live_stock(item: WatchItem, quote: em.MemberQuote | None) -> WatchStock:
    base = _base(item, "live")
    if quote is None:
        return base
    return base.model_copy(
        update={
            "name": quote.name or item.name_cn,
            "last": quote.last,
            "change_pct": quote.change_pct,
            "change_5d_pct": quote.change_5d_pct,
            "change_10d_pct": quote.change_10d_pct,
            "change_60d_pct": quote.change_60d_pct,
            "change_ytd_pct": quote.change_ytd_pct,
            "amount": quote.amount,
            "turnover_pct": quote.turnover_pct,
            "pe_dynamic": quote.pe_dynamic,
            "pb": quote.pb,
            "market_cap": quote.market_cap,
            "main_net_inflow": quote.main_net_inflow,
            "industry": quote.sub_industry,
        }
    )


def _tencent_stock(
    item: WatchItem, quote: tx.Quote | None, closes: list[RecentClose], today: date
) -> WatchStock:
    day = None if quote is None else quote.quote_date
    if quote is None or quote.last is None or day is None:
        return _stored_stock(item, closes, today)
    series = [row for row in closes if row.trade_date < day]
    series.append(RecentClose(trade_date=day, close=quote.last))
    return _base(item, "live").model_copy(
        update={
            "name": quote.name or item.name_cn,
            "last": quote.last,
            "change_pct": quote.change_pct,
            "change_5d_pct": window_change(series, 5),
            "change_10d_pct": window_change(series, 10),
            "change_60d_pct": window_change(series, 60),
            "change_ytd_pct": ytd_change(series, today.year),
            "amount": quote.amount,
            "turnover_pct": quote.turnover_pct,
            "pe_dynamic": quote.pe_dynamic,
            "pb": quote.pb,
            "market_cap": quote.market_cap,
        }
    )


def _stored_stock(item: WatchItem, closes: list[RecentClose], today: date) -> WatchStock:
    base = _base(item, "stored")
    if not closes:
        return base
    return base.model_copy(
        update={
            "last": closes[-1].close,
            "change_pct": window_change(closes, 1),
            "change_5d_pct": window_change(closes, 5),
            "change_10d_pct": window_change(closes, 10),
            "change_60d_pct": window_change(closes, 60),
            "change_ytd_pct": ytd_change(closes, today.year),
        }
    )


def _iid(item: WatchItem) -> UUID:
    if item.instrument_id is None:
        raise ValueError("只有本系统收录的个股才能进入 A 股看板")
    return item.instrument_id


def _base(item: WatchItem, origin: str) -> WatchStock:
    return WatchStock(
        instrument_id=_iid(item),
        display_symbol=item.display_symbol,
        code=item.symbol,
        name=item.name_cn,
        exchange=item.exchange,
        origin=origin,
    )


def _plain(name: str | None) -> str:
    return (name or "").rstrip("ⅡⅢ").strip()


def _round(value: Decimal) -> Decimal:
    return value.quantize(_TWO, rounding=ROUND_HALF_UP)


_service: WatchlistInsightsService | None = None


def get_insights_service() -> WatchlistInsightsService:
    global _service
    if _service is None:
        _service = WatchlistInsightsService(
            gateway=shared_gateway(), industry=get_service(), sentiment=get_sentiment_service()
        )
    return _service
