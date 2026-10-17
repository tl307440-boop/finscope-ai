"""产业研究 · 板块行情服务：东财行业/概念板块、成分、日线、估值、研报与热度榜。

每个区块单独标注 ok / stale / unavailable 以及数据来自实时接口（live）还是收盘入库（stored），
某个上游失败只让对应区块降级，不连累整页。
- 板块快照：默认实时；实时不可用时退回最近一次收盘入库快照；
  带 as_of 时只读入库快照（trade_date ≤ as_of）。
- 板块日线：非交易时段且库里已有最近交易日时优先读库，否则实时，实时失败再退回库。
- 轮动图只用入库日线计算。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError
from app.repositories.board_market import StoredBars, StoredSnapshot
from app.schemas.industry_market import (
    BarItem,
    BoardCatalog,
    BoardCatalogItem,
    BoardDetail,
    BoardOverview,
    BoardQuoteItem,
    BreadthItem,
    BucketItem,
    CurveItem,
    HotList,
    HotPlateItem,
    HotStockItem,
    MemberItem,
    MembersBlock,
    PercentileItem,
    ReportItem,
    ReportList,
    RotationPoint,
    RotationSeries,
    RotationView,
    Section,
    SnapshotDates,
    SourceRef,
    SubIndustryItem,
    TrendBlock,
    ValuationBlock,
    ValuationPointItem,
    WindowReturns,
)
from app.services import board_analytics as rules
from app.services.market_cache import Cached, TtlCache, shared_gateway
from app.utils.market_time import SHANGHAI, shanghai_pit
from app.utils.time import utc_now

T = TypeVar("T")

BENCHMARK_SECID = "1.000300"
BENCHMARK_NAME = "沪深300"
VALUATION_YEARS = 5
_MAX_MEMBER_PAGES = 10
_LEVEL_KEYS = {"1": 1, "2": 2, "3": 3}

_TTL_TREE = 12 * 3600
_TTL_QUOTES = 60
_TTL_MEMBERS = 300
_TTL_KLINE = 1800
_TTL_VALUATION = 6 * 3600
_TTL_REPORTS = 3600
_TTL_HOT = 300
_TTL_ROTATION = 600

ROTATION_WINDOW = 20
ROTATION_STEP = 5
ROTATION_POINTS = 8
_ROTATION_HISTORY_DAYS = 200
_MIN_STORED_BARS = 60
_SESSION_OPEN = time(9, 15)
_SESSION_SETTLED = time(15, 30)
STORED_SOURCE = "本系统收盘入库快照（东方财富板块行情）"

OVERVIEW_RULES = [
    "实时数据为东方财富公开行情快照（交易时段约 15 秒延迟），本系统缓存 60 秒；"
    "每个交易日收盘后另存一份快照，可按日期回看。",
    "5 日 / 10 日 / 60 日 / 年初至今涨跌幅为东财口径；主力净流入 = 超大单 + 大单净额（元）。",
    "行业分级采用 2026 年起的东财一 / 二 / 三级行业，"
    "与申万 2021 版一级行业名称基本对应但成分不完全一致。",
]
DETAIL_RULES = [
    "均线、区间涨幅、相对沪深300超额与估值分位由本系统按日线和估值序列计算，"
    "口径版本见 analytics_version。",
    "超额 = 板块指数（首日=100）- 沪深300（首日=100），单位百分点；不是收益承诺。",
    "估值分位 = 近 5 年中 PE(TTM) 不高于当前值的交易日占比；"
    "PE 为负（行业整体亏损）的日子不参与。东财估值库只覆盖二级行业。",
    "子行业由成分股自带的所属行业聚合，涨跌幅按总市值加权。",
]
ROTATION_RULES = [
    "RS = 板块指数收盘 / 沪深300 收盘；RS-Ratio = 100 + RS 相对近 20 个交易日均值的 z 分数；"
    "RS-Momentum = 100 + RS-Ratio 日变化率相对近 20 个交易日均值的 z 分数。",
    "这是相对轮动图（RRG）的常用开源近似，不是 JdK 原版专有公式；尾迹每 5 个交易日一个点。",
    "象限：领先（强且在变强）→ 转弱 → 落后 → 改善，顺时针轮动是经验规律，不是预测。",
    "只用本系统收盘入库的板块日线计算，交易时段内不含当天。",
]


class BoardReader(Protocol):
    def latest_snapshot_date(self, on_or_before: date | None = None) -> date | None: ...

    def snapshot_dates(self, limit: int) -> list[date]: ...

    def snapshots(self, trade_date: date, kind: str, level: int | None) -> list[StoredSnapshot]: ...

    def latest_snapshot(self, board_code: str) -> StoredSnapshot | None: ...

    def bars(self, secid: str, since: date, until: date | None = None) -> StoredBars: ...

    def bars_many(
        self, secids: list[str], since: date, until: date | None = None
    ) -> dict[str, StoredBars]: ...


class Gateway(Protocol):
    async def get(self, url: str, headers: Mapping[str, str]) -> bytes: ...

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes: ...


@dataclass
class IndustryMarketService:
    gateway: Gateway
    cache: TtlCache = field(default_factory=TtlCache)
    today: Callable[[], date] = field(default=lambda: shanghai_pit(utc_now()))
    now: Callable[[], datetime] = field(default=utc_now)
    store: BoardReader | None = None

    async def catalog(self) -> BoardCatalog:
        tree = await self._tree()
        counts: dict[str, int] = {}
        for node in tree.value:
            key = _level_key(node)
            counts[key] = counts.get(key, 0) + 1
        return BoardCatalog(
            boards=[
                BoardCatalogItem(code=node.code, name=node.name, kind=node.kind, level=node.level)
                for node in tree.value
                if node.kind in ("industry", "concept")
            ],
            counts=counts,
            sources=[_source("东方财富 行业板块目录", em.BOARD_TREE_URL, tree)],
        )

    async def overview(self, level: str, as_of: date | None = None) -> BoardOverview:
        kind, level_no = _level_filter(level)
        if as_of is not None:
            return await self._stored_overview(level, kind, level_no, as_of, live_failure=None)
        try:
            tree = await self._tree()
            nodes = _nodes_for_level(tree.value, level)
            cached = await self.cache.get(
                f"quotes:{level}", _TTL_QUOTES, lambda: self._load_quotes([n.code for n in nodes])
            )
        except ProviderError as error:
            if self.store is None:
                raise
            return await self._stored_overview(
                level, kind, level_no, None, live_failure=error.message
            )
        by_code = {node.code: node for node in nodes}
        items = [_quote_item(quote, by_code.get(quote.code)) for quote in cached.value]
        return BoardOverview(
            level=level,
            section=_section(cached),
            breadth=_breadth(rules.breadth(cached.value)),
            boards=items,
            sources=[
                _source(
                    "东方财富 板块行情",
                    "https://quote.eastmoney.com/center/gridlist.html#industry_board",
                    cached,
                ),
                _source("东方财富 行业板块目录", em.BOARD_TREE_URL, tree),
            ],
            rules=OVERVIEW_RULES,
        )

    async def detail(self, code: str) -> BoardDetail:
        board_code = em.normalize_board_code(code)
        tree_nodes: tuple[em.BoardNode, ...] = ()
        try:
            tree_nodes = (await self._tree()).value
            node = next((item for item in tree_nodes if item.code == board_code), None)
            quote_cached = await self.cache.get(
                f"quote:{board_code}", _TTL_QUOTES, lambda: self._load_quotes([board_code])
            )
            if not quote_cached.value:
                raise ProviderError(code="NOT_FOUND", message="找不到这个板块", status_code=404)
            board = _quote_item(quote_cached.value[0], node)
            quote_section = _section(quote_cached)
        except ProviderError as error:
            stored = await self._stored_board(board_code) if error.code != "NOT_FOUND" else None
            if stored is None:
                raise
            node = stored.node
            board = _quote_item(stored.quote, stored.node)
            quote_section = _stored_section(stored.trade_date, stored.fetched_at, error.message)

        trend, members, valuation = await asyncio.gather(
            self._trend(board_code),
            self._members(board_code, tree_nodes),
            self._valuation(board_code, node),
        )
        sources = [
            SourceRef(
                name="东方财富 板块行情" if quote_section.origin == "live" else STORED_SOURCE,
                url=f"https://quote.eastmoney.com/bk/90.{board_code}.html",
                fetched_at=quote_section.fetched_at,
            ),
            SourceRef(
                name="东方财富 板块日线 / 沪深300 日线",
                url=f"https://quote.eastmoney.com/bk/90.{board_code}.html",
                fetched_at=trend.section.fetched_at,
            ),
            SourceRef(
                name="东方财富 板块成分",
                url=f"https://data.eastmoney.com/bkzj/{board_code}.html",
                fetched_at=members.section.fetched_at,
            ),
        ]
        if valuation.section.status != "unavailable":
            sources.append(
                SourceRef(
                    name="东方财富 行业估值",
                    url="https://data.eastmoney.com/gzfx/hylist.html",
                    fetched_at=valuation.section.fetched_at,
                )
            )
        return BoardDetail(
            board=board,
            quote_section=quote_section,
            trend=trend,
            members=members,
            valuation=valuation,
            sources=sources,
            rules=DETAIL_RULES,
            analytics_version=rules.ANALYTICS_VERSION,
        )

    async def reports(self, code: str) -> ReportList:
        board_code = em.normalize_board_code(code)
        end = self.today()
        start = end - timedelta(days=180)
        url = em.reports_url(board_code, start, end)
        source_url = "https://data.eastmoney.com/report/industry.jshtml"
        try:
            cached = await self.cache.get(
                f"reports:{board_code}",
                _TTL_REPORTS,
                lambda: self._fetch(url, em.EASTMONEY_HEADERS, em.parse_reports),
            )
        except ProviderError as error:
            return ReportList(
                section=Section(status="unavailable", reason=error.message),
                reports=[],
                sources=[SourceRef(name="东方财富 行业研报", url=source_url)],
            )
        section = _section(cached)
        if not cached.value:
            section = Section(
                status="unavailable",
                reason=(
                    "近 180 天没有归到这个板块的行业研报"
                    "（东财研报按二级行业归档，一级行业和概念板块通常查不到）"
                ),
                fetched_at=cached.fetched_at,
            )
        return ReportList(
            section=section,
            reports=[ReportItem(**vars(report)) for report in cached.value],
            sources=[_source("东方财富 行业研报", source_url, cached)],
        )

    async def hot(self) -> HotList:
        em_rows, em_section = await self._guard(
            self.cache.get("hot:em", _TTL_HOT, self._load_em_hot)
        )
        ths_rows, ths_section = await self._guard(self.ths_stocks())
        plates, plates_section = await self._guard(self.ths_plates())
        industries, concepts = plates if plates is not None else ((), ())
        return HotList(
            eastmoney=list(em_rows or []),
            eastmoney_section=em_section,
            ths_stocks=[
                HotStockItem(
                    rank=row.rank,
                    code=row.code,
                    name=row.name,
                    change_pct=row.change_pct,
                    heat=row.heat,
                    rank_change=row.rank_change,
                    tags=[*([row.popularity_tag] if row.popularity_tag else []), *row.concept_tags],
                    note=row.analysis,
                )
                for row in (ths_rows or ())
            ],
            ths_section=ths_section,
            ths_industries=[_plate_item(row) for row in industries],
            ths_concepts=[_plate_item(row) for row in concepts],
            plates_section=plates_section,
            sources=[
                SourceRef(
                    name="东方财富 股吧人气榜",
                    url="https://guba.eastmoney.com/rank/",
                    fetched_at=em_section.fetched_at,
                ),
                SourceRef(
                    name="同花顺 热榜",
                    url="https://eq.10jqka.com.cn/frontend/thsTopRank/index.html",
                    fetched_at=ths_section.fetched_at,
                ),
            ],
        )

    async def _tree(self) -> Cached[tuple[em.BoardNode, ...]]:
        return await self.cache.get(
            "tree",
            _TTL_TREE,
            lambda: self._fetch(em.BOARD_TREE_URL, em.EASTMONEY_HEADERS, em.parse_board_tree),
        )

    async def _load_quotes(self, codes: list[str]) -> tuple[em.BoardQuote, ...]:
        rows: list[em.BoardQuote] = []
        for start in range(0, len(codes), em.PAGE_SIZE):
            chunk = codes[start : start + em.PAGE_SIZE]
            rows.extend(
                await self._fetch(em.quote_url(chunk), em.EASTMONEY_HEADERS, em.parse_board_quotes)
            )
        return tuple(rows)

    async def board_name(self, code: str) -> str:
        board_code = em.normalize_board_code(code)
        try:
            tree = await self._tree()
            node = next((item for item in tree.value if item.code == board_code), None)
            if node is not None:
                return node.name
        except ProviderError:
            pass
        stored = await self._stored_board(board_code)
        if stored is None:
            raise ProviderError(code="NOT_FOUND", message="找不到这个板块", status_code=404)
        return stored.node.name

    async def board_names(self) -> dict[str, str]:
        """行业名（去掉 Ⅱ/Ⅲ 后缀）→ 板块代码；同名时二级行业优先。"""

        tree = await self._tree()
        names: dict[str, str] = {}
        for level in (3, 1, 2):
            for node in tree.value:
                if node.kind == "industry" and node.level == level:
                    names[_plain_name(node.name)] = node.code
        return names

    async def snapshot_dates(self, limit: int = 120) -> SnapshotDates:
        if self.store is None:
            return SnapshotDates(dates=[])
        return SnapshotDates(dates=await asyncio.to_thread(self.store.snapshot_dates, limit))

    async def rotation(self, level: str) -> RotationView:
        kind, level_no = _level_filter(level)
        if kind != "industry" or level_no not in (1, 2):
            raise ProviderError(
                code="VALIDATION_ERROR", message="轮动图只支持一级、二级行业", status_code=422
            )
        cached = await self.cache.get(
            f"rotation:{level}", _TTL_ROTATION, lambda: self._load_rotation(level, level_no)
        )
        return cached.value

    async def _load_rotation(self, level: str, level_no: int) -> RotationView:
        source = SourceRef(
            name="本系统收盘入库的东财板块日线 / 沪深300 日线", url=em.BOARD_TREE_URL
        )
        empty = RotationView(
            level=level,
            section=Section(
                status="unavailable",
                reason="板块日线还没有入库：收盘后的 sync_board_market 任务跑过一次后才有轮动图",
                origin="stored",
            ),
            benchmark=BENCHMARK_NAME,
            window=ROTATION_WINDOW,
            step=ROTATION_STEP,
            series=[],
            rules=ROTATION_RULES,
            sources=[source],
        )
        if self.store is None:
            return empty
        store = self.store
        day = await asyncio.to_thread(store.latest_snapshot_date, None)
        names = (
            {
                row.node.code: row.node.name
                for row in await asyncio.to_thread(store.snapshots, day, "industry", level_no)
            }
            if day is not None
            else {}
        )
        if not names:
            try:
                tree = await self._tree()
            except ProviderError:
                return empty
            names = {
                node.code: node.name
                for node in tree.value
                if node.kind == "industry" and node.level == level_no
            }
        since = self.today() - timedelta(days=_ROTATION_HISTORY_DAYS)
        secids = {em.board_secid(code): code for code in names}
        stored = await asyncio.to_thread(store.bars_many, [*secids, BENCHMARK_SECID], since)
        bench = stored.get(BENCHMARK_SECID)
        if bench is None or not bench.bars:
            return empty
        series: list[RotationSeries] = []
        for secid, code in secids.items():
            board = stored.get(secid)
            if board is None:
                continue
            tail = rules.rotation_tail(
                board.bars,
                bench.bars,
                window=ROTATION_WINDOW,
                step=ROTATION_STEP,
                points=ROTATION_POINTS,
            )
            if not tail:
                continue
            series.append(
                RotationSeries(
                    code=code,
                    name=names[code],
                    quadrant=rules.rotation_quadrant(tail[-1].rs_ratio, tail[-1].rs_momentum),
                    change_pct=rules.window_return_pct(board.bars, ROTATION_WINDOW),
                    tail=[RotationPoint(**vars(point)) for point in tail],
                )
            )
        if not series:
            return empty
        return empty.model_copy(
            update={
                "section": Section(
                    status="ok",
                    origin="stored",
                    data_date=bench.bars[-1].trade_date,
                    fetched_at=bench.fetched_at,
                ),
                "series": series,
                "sources": [source.model_copy(update={"fetched_at": bench.fetched_at})],
            }
        )

    async def _stored_overview(
        self,
        level: str,
        kind: str,
        level_no: int | None,
        as_of: date | None,
        *,
        live_failure: str | None,
    ) -> BoardOverview:
        if self.store is None:
            raise ProviderError(code="UNAVAILABLE", message="收盘快照库不可用", status_code=503)
        store = self.store
        day = await asyncio.to_thread(store.latest_snapshot_date, as_of)
        rows = await asyncio.to_thread(store.snapshots, day, kind, level_no) if day else []
        if not rows:
            if live_failure is not None:
                message = f"实时行情暂不可用（{live_failure}），库里也还没有收盘快照"
                raise ProviderError(code="UPSTREAM_UNAVAILABLE", message=message, status_code=503)
            raise ProviderError(
                code="NOT_FOUND", message=f"{as_of} 及之前没有入库的收盘快照", status_code=404
            )
        fetched_at = max(row.fetched_at for row in rows)
        section = (
            _stored_section(rows[0].trade_date, fetched_at, live_failure)
            if live_failure is not None
            else Section(
                status="ok", origin="stored", data_date=rows[0].trade_date, fetched_at=fetched_at
            )
        )
        return BoardOverview(
            level=level,
            section=section,
            breadth=_breadth(rules.breadth(row.quote for row in rows)),
            boards=[_quote_item(row.quote, row.node) for row in rows],
            sources=[
                SourceRef(
                    name=STORED_SOURCE,
                    url="https://quote.eastmoney.com/center/gridlist.html#industry_board",
                    fetched_at=fetched_at,
                )
            ],
            rules=OVERVIEW_RULES,
        )

    async def _stored_board(self, code: str) -> StoredSnapshot | None:
        if self.store is None:
            return None
        return await asyncio.to_thread(self.store.latest_snapshot, code)

    async def _stored_bars(self, secid: str, since: date) -> StoredBars:
        if self.store is None:
            return StoredBars(bars=(), fetched_at=None)
        return await asyncio.to_thread(self.store.bars, secid, since)

    def _prefer_stored(self, bars: StoredBars) -> bool:
        if len(bars.bars) < _MIN_STORED_BARS:
            return False
        local = self.now().astimezone(SHANGHAI)
        if local.weekday() < 5 and _SESSION_OPEN <= local.time() < _SESSION_SETTLED:
            return False
        return bars.bars[-1].trade_date >= expected_session(local)

    async def _trend(self, code: str) -> TrendBlock:
        end = self.today()
        start = end - timedelta(days=420)
        secid = em.board_secid(code)
        stored = await self._stored_bars(secid, start)
        stored_bench = await self._stored_bars(BENCHMARK_SECID, start)
        if self._prefer_stored(stored):
            return self._trend_block(
                list(stored.bars),
                list(stored_bench.bars),
                Section(
                    status="ok",
                    origin="stored",
                    data_date=stored.bars[-1].trade_date,
                    fetched_at=stored.fetched_at,
                ),
            )
        try:
            board = await self.cache.get(
                f"kline:{code}",
                _TTL_KLINE,
                lambda: self._fetch(
                    em.kline_url(secid, start, end), em.EASTMONEY_HEADERS, em.parse_klines
                ),
            )
        except ProviderError as error:
            if stored.bars:
                return self._trend_block(
                    list(stored.bars),
                    list(stored_bench.bars),
                    _stored_section(stored.bars[-1].trade_date, stored.fetched_at, error.message),
                )
            return TrendBlock(
                section=Section(status="unavailable", reason=error.message),
                bars=[],
                relative=[],
                returns=WindowReturns(),
                benchmark=BENCHMARK_NAME,
            )
        try:
            bench = await self.cache.get(
                f"kline:{BENCHMARK_SECID}",
                _TTL_KLINE,
                lambda: self._fetch(
                    em.kline_url(BENCHMARK_SECID, start, end), em.EASTMONEY_HEADERS, em.parse_klines
                ),
            )
            bench_bars: list[em.BoardBar] = list(bench.value)
        except ProviderError:
            bench_bars = list(stored_bench.bars)
        section = _section(board)
        if not board.value:
            section = Section(
                status="unavailable",
                reason="上游没有返回这个板块的日线",
                fetched_at=board.fetched_at,
            )
        return self._trend_block(list(board.value), bench_bars, section)

    def _trend_block(
        self, bars: list[em.BoardBar], bench_list: list[em.BoardBar], section: Section
    ) -> TrendBlock:
        closes = [bar.close for bar in bars]
        ma20 = rules.moving_average(closes, 20)
        ma60 = rules.moving_average(closes, 60)
        curve = rules.relative_curve(bars[-250:], bench_list)
        returns = WindowReturns(
            d5=rules.window_return_pct(bars, 5),
            d20=rules.window_return_pct(bars, 20),
            d60=rules.window_return_pct(bars, 60),
            d120=rules.window_return_pct(bars, 120),
            d250=rules.window_return_pct(bars, 250),
            excess_d20=_excess(bars, bench_list, 20),
            excess_d60=_excess(bars, bench_list, 60),
            excess_d250=_excess(bars, bench_list, 250),
        )
        return TrendBlock(
            section=section,
            bars=[
                BarItem(
                    trade_date=bar.trade_date,
                    open=bar.open,
                    close=bar.close,
                    high=bar.high,
                    low=bar.low,
                    volume=bar.volume,
                    amount=bar.amount,
                    ma20=ma20[i],
                    ma60=ma60[i],
                )
                for i, bar in enumerate(bars)
            ],
            relative=[CurveItem(**vars(point)) for point in curve],
            returns=returns,
            benchmark=BENCHMARK_NAME,
        )

    async def _members(self, code: str, tree: tuple[em.BoardNode, ...]) -> MembersBlock:
        try:
            cached = await self.cache.get(
                f"members:{code}", _TTL_MEMBERS, lambda: self._load_members(code)
            )
        except ProviderError as error:
            return MembersBlock(
                section=Section(status="unavailable", reason=error.message),
                total=0,
                members=[],
                distribution=[],
                suspended=0,
                sub_industries=[],
            )
        page = cached.value
        buckets, suspended = rules.change_distribution(page.rows)
        level2 = {
            _plain_name(node.name): node.code
            for node in tree
            if node.kind == "industry" and node.level == 2
        }
        groups = rules.sub_industries(page.rows)
        return MembersBlock(
            section=_section(cached),
            total=page.total,
            members=[MemberItem(**vars(row)) for row in page.rows],
            distribution=[BucketItem(**vars(bucket)) for bucket in buckets],
            suspended=suspended,
            sub_industries=(
                [
                    SubIndustryItem(**vars(group), code=level2.get(_plain_name(group.name)))
                    for group in groups
                ]
                if len(groups) > 1
                else []
            ),
        )

    async def _load_members(self, code: str) -> em.MemberPage:
        first = await self._fetch(em.members_url(code, 1), em.EASTMONEY_HEADERS, em.parse_members)
        rows = list(first.rows)
        pages = min(_MAX_MEMBER_PAGES, -(-first.total // em.PAGE_SIZE))
        for page in range(2, pages + 1):
            more = await self._fetch(
                em.members_url(code, page), em.EASTMONEY_HEADERS, em.parse_members
            )
            if not more.rows:
                break
            rows.extend(more.rows)
        return em.MemberPage(total=first.total, rows=tuple(rows))

    async def _valuation(self, code: str, node: em.BoardNode | None) -> ValuationBlock:
        if node is not None and not (node.kind == "industry" and node.level == 2):
            return _unavailable_valuation(
                "东财行业估值库只覆盖二级行业；可在子行业里进入二级行业查看估值分位"
            )
        try:
            cached = await self.cache.get(
                f"valuation:{code}", _TTL_VALUATION, lambda: self._load_valuation(code)
            )
        except ProviderError as error:
            return _unavailable_valuation(error.message)
        points = cached.value
        if not points:
            return _unavailable_valuation("东财估值库没有这个板块的估值序列", cached.fetched_at)
        since = self.today() - timedelta(days=365 * VALUATION_YEARS)
        latest = max(points, key=lambda point: point.trade_date)
        pe = rules.percentile_summary(points, field="pe_ttm", since=since)
        pb = rules.percentile_summary(points, field="pb_mrq", since=since)
        return ValuationBlock(
            section=_section(cached),
            pe_ttm=PercentileItem(**vars(pe)) if pe else None,
            pb_mrq=PercentileItem(**vars(pb)) if pb else None,
            loss_members=latest.loss_members,
            members=latest.members,
            series=[
                ValuationPointItem(
                    trade_date=point.trade_date, pe_ttm=point.pe_ttm, pb_mrq=point.pb_mrq
                )
                for point in sorted(points, key=lambda item: item.trade_date)
                if point.trade_date >= since
            ],
            window_years=VALUATION_YEARS,
        )

    async def _load_valuation(self, code: str) -> tuple[em.ValuationPoint, ...]:
        rows: list[em.ValuationPoint] = []
        since = self.today() - timedelta(days=365 * VALUATION_YEARS)
        for page in range(1, 4):
            batch = await self._fetch(
                em.valuation_url(code, page), em.EASTMONEY_HEADERS, em.parse_valuation
            )
            rows.extend(batch.rows)
            if page >= batch.pages or not batch.rows or batch.rows[-1].trade_date < since:
                break
        return tuple(rows)

    async def _load_em_hot(self) -> tuple[HotStockItem, ...]:
        payload = await self.gateway.post(
            em.EM_HOT_RANK_URL, em.hot_rank_body(30), em.EASTMONEY_HEADERS
        )
        entries = em.parse_hot_rank(payload)
        if not entries:
            return ()
        quotes = await self._fetch(
            em.stock_quote_url([em.stock_secid(entry.exchange, entry.code) for entry in entries]),
            em.EASTMONEY_HEADERS,
            em.parse_stock_quotes,
        )
        items: list[HotStockItem] = []
        for entry in entries:
            quote = quotes.get(entry.code)
            items.append(
                HotStockItem(
                    rank=entry.rank,
                    code=entry.code,
                    exchange=entry.exchange,
                    name=quote.name if quote else entry.code,
                    change_pct=quote.change_pct if quote else None,
                    last=quote.last if quote else None,
                    rank_change=entry.rank_change,
                    industry=quote.industry if quote else None,
                    tags=[],
                )
            )
        return tuple(items)

    async def ths_plates(
        self,
    ) -> Cached[tuple[tuple[em.ThsHotPlate, ...], tuple[em.ThsHotPlate, ...]]]:
        """同花顺热门板块原始行（行业, 概念），与 hot() 共用缓存。"""

        return await self.cache.get("hot:ths-plates", _TTL_HOT, self._load_ths_plates)

    async def ths_stocks(self) -> Cached[tuple[em.ThsHotStock, ...]]:
        return await self.cache.get(
            "hot:ths",
            _TTL_HOT,
            lambda: self._fetch(em.THS_HOT_STOCK_URL, em.THS_HEADERS, em.parse_ths_hot_stocks),
        )

    async def _load_ths_plates(
        self,
    ) -> tuple[tuple[em.ThsHotPlate, ...], tuple[em.ThsHotPlate, ...]]:
        industries = await self._fetch(
            em.THS_HOT_PLATE_URL.format(kind="industry"), em.THS_HEADERS, em.parse_ths_hot_plates
        )
        concepts = await self._fetch(
            em.THS_HOT_PLATE_URL.format(kind="concept"), em.THS_HEADERS, em.parse_ths_hot_plates
        )
        return industries, concepts

    async def _fetch(self, url: str, headers: Mapping[str, str], parse: Callable[[bytes], T]) -> T:
        return parse(await self.gateway.get(url, headers))

    @staticmethod
    async def _guard(pending: Awaitable[Cached[T]]) -> tuple[T | None, Section]:
        try:
            cached = await pending
        except ProviderError as error:
            return None, Section(status="unavailable", reason=error.message)
        return cached.value, _section(cached)


def expected_session(local: datetime) -> date:
    """按工作日推算「最近一个已收盘的交易日」；节假日会推早，此时读库判定为不新鲜，转去实时。"""

    day = local.date()
    if local.weekday() < 5 and local.time() >= _SESSION_SETTLED:
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _level_filter(level: str) -> tuple[str, int | None]:
    if level == "concept":
        return "concept", None
    if level in _LEVEL_KEYS:
        return "industry", _LEVEL_KEYS[level]
    raise ProviderError(code="VALIDATION_ERROR", message="不支持的板块层级", status_code=422)


def _stored_section(day: date, fetched_at: datetime | None, live_failure: str | None) -> Section:
    return Section(
        status="stale",
        reason=f"实时行情暂不可用（{live_failure}），展示 {day.isoformat()} 收盘入库数据",
        origin="stored",
        data_date=day,
        fetched_at=fetched_at,
    )


def _level_key(node: em.BoardNode) -> str:
    if node.kind == "industry":
        return f"industry_l{node.level}"
    return node.kind


def _nodes_for_level(tree: tuple[em.BoardNode, ...], level: str) -> list[em.BoardNode]:
    if level == "concept":
        return [node for node in tree if node.kind == "concept"]
    target = _LEVEL_KEYS.get(level)
    return [node for node in tree if node.kind == "industry" and node.level == target]


def _quote_item(quote: em.BoardQuote, node: em.BoardNode | None) -> BoardQuoteItem:
    return BoardQuoteItem(
        **vars(quote) | {"name": node.name if node else quote.name},
        kind=node.kind if node else "industry",
        level=node.level if node else 0,
    )


def _plate_item(row: em.ThsHotPlate) -> HotPlateItem:
    return HotPlateItem(
        rank=row.rank,
        code=row.code,
        name=row.name,
        change_pct=row.change_pct,
        heat=row.heat,
        rank_change=row.rank_change,
        tag=row.hot_tag or row.tag,
    )


def _breadth(value: rules.Breadth) -> BreadthItem:
    return BreadthItem(**vars(value))


def _excess(board: list[em.BoardBar], bench: list[em.BoardBar], sessions: int) -> Decimal | None:
    board_return = rules.window_return_pct(board, sessions)
    if board_return is None or not bench or bench[-1].trade_date != board[-1].trade_date:
        return None
    bench_return = rules.window_return_pct(bench, sessions)
    return None if bench_return is None else board_return - bench_return


def _plain_name(name: str) -> str:
    return name.rstrip("ⅡⅢ").strip()


def _section(cached: Cached[Any]) -> Section:
    if cached.stale:
        return Section(
            status="stale",
            reason=f"上游刷新失败，展示上次成功抓取的数据：{cached.stale_reason}",
            fetched_at=cached.fetched_at,
        )
    return Section(status="ok", fetched_at=cached.fetched_at)


def _unavailable_valuation(reason: str, fetched_at: datetime | None = None) -> ValuationBlock:
    return ValuationBlock(
        section=Section(status="unavailable", reason=reason, fetched_at=fetched_at),
        series=[],
        window_years=VALUATION_YEARS,
    )


def _source(name: str, url: str, cached: Cached[Any]) -> SourceRef:
    return SourceRef(name=name, url=url, fetched_at=cached.fetched_at)


_service: IndustryMarketService | None = None


def get_service() -> IndustryMarketService:
    global _service
    if _service is None:
        _service = IndustryMarketService(gateway=shared_gateway())
    return _service
