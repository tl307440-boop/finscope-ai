"""总览驾驶舱：把指数、全市场涨跌分布、涨停池、宏观序列、热榜、公告与自选聚合成每日市场大况。

- 只聚合公开数据并在后端计算；评分公式全部在 dashboard_rules，本模块只负责取数、对齐与降级。
- 每个模块独立取数：某个上游失败只让对应 Section 变成 unavailable / stale，并写明原因，不补值。
- 指数与成交额用腾讯行情，板块涨幅与个股涨幅榜用新浪行情（东财 push2 常被频控）；
  页面按实际来源标注。
- 「较上一交易日」来自 dashboard_daily_metrics：每次计算覆盖当日值，对比取更早的最近一个交易日。
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from typing import Any, Protocol, TypeVar
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import AppError
from app.providers import eastmoney_boards as em
from app.providers import eastmoney_rates as rates
from app.providers import eastmoney_sentiment as es
from app.providers import sina_market as sina
from app.providers import tencent_market as tx
from app.providers.disclosures import (
    CNINFO_QUERY,
    MarketAnnouncement,
    cninfo_market_form,
    parse_cninfo_market,
)
from app.providers.errors import ProviderError
from app.providers.fred_public import graph_csv_url, parse_graph_csv, public_realtime_start
from app.repositories.dashboard_metrics import DashboardMetricRepository, StoredMetric
from app.repositories.dashboard_snapshots import DashboardSnapshotRepository
from app.repositories.watchlists import RecentClose, WatchGroup
from app.schemas.cockpit import (
    AmountPoint,
    AnnouncementItem,
    AnnouncementsOverview,
    BarItem,
    BoardsOverview,
    BreadthItem,
    BucketItem,
    CountItem,
    FactorItem,
    GainBoardItem,
    GlobalAsset,
    HotBoardItem,
    HotStockItem,
    IndexBars,
    IndexQuote,
    LimitStats,
    MacroOverview,
    MacroTile,
    MarketOverview,
    MinuteItem,
    ScoreBlock,
    SeriesPoint,
    SignalItem,
    StocksOverview,
    WatchGroupRef,
    WatchOverview,
    WatchRow,
)
from app.schemas.industry_market import Section, SourceRef
from app.schemas.market_sentiment import LimitUpView
from app.services import dashboard_rules as rules
from app.services.industry_market import IndustryMarketService, get_service
from app.services.market_cache import Cached, PacedGateway, TtlCache, shared_gateway
from app.services.market_sentiment import MarketSentimentService, get_sentiment_service
from app.services.watchlist_insights import WatchlistInsightsService, get_insights_service
from app.utils.market_time import SHANGHAI
from app.utils.time import utc_now

T = TypeVar("T")
logger = logging.getLogger(__name__)

_TTL_QUOTES = 30
_TTL_KLINE = 300
_TTL_MINUTE = 30
_TTL_FENBU = 60
_TTL_FRED = 6 * 3600
_TTL_FRED_PUSHED = 36 * 3600  # 外部推送的数据：每天推一次，留足余量
_TTL_CHINA = 3600
_TTL_ASSETS = 60
_TTL_SINA = 120
_TTL_ANNOUNCEMENTS = 600
_TTL_SPARK = 1800
_KLINE_BARS = 260
_PERIOD_BARS = {"day": 260, "week": 160, "month": 120}
_FRED_LOOKBACK_DAYS = 900
_FRED_BUDGET_SECONDS = 8.0  # 单次请求最多等这么久；网关 60s 会 504，所以必须远小于它
_FRED_STALE_BUDGET_SECONDS = 1.5  # 已有旧值时只等这么久，等不到就先展示旧值
_SPARK_LIMIT = 12
_TOP = 10
_GAINER_MIN_AMOUNT = Decimal("3e8")
_BOARD_SUFFIX = re.compile(r"(概念|行业|板块)$")
_TWO = 2

MAIN_INDICES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
    ("sh000688", "科创50"),
    ("sh000905", "中证500"),
    ("bj899050", "北证50"),
)
TREND_INDEX = ("sh000985", "中证全指")
INDEX_NAMES: dict[str, str] = dict((*MAIN_INDICES, TREND_INDEX))
GLOBAL_TENCENT: tuple[tuple[str, str], ...] = (
    ("usINX", "标普500"),
    ("usIXIC", "纳斯达克"),
    ("usDJI", "道琼斯"),
    ("hkHSI", "恒生指数"),
    ("hkHSTECH", "恒生科技"),
)
GLOBAL_SINA: tuple[tuple[str, str], ...] = (
    ("znb_NKY", "日经225"),
    ("hf_GC", "COMEX黄金"),
    ("hf_CL", "NYMEX原油"),
)
FRED_SERIES: tuple[str, ...] = (
    "WALCL",
    "WTREGEN",
    "RRPONTSYD",
    "DTWEXBGS",
    "DFII10",
    "BAMLH0A0HYM2",
    "NFCI",
    "UNRATE",
    "PAYEMS",
    "CPIAUCSL",
    "FEDFUNDS",
)
# 巨潮全市场检索：最新两页 + 重要类别 + 关键词，合并去重；单页 30 条是巨潮上限。
ANNOUNCEMENT_QUERIES: tuple[tuple[str, str, int], ...] = (
    ("", "", 1),
    ("", "", 2),
    ("category_yjygjxz_szsh", "", 1),
    ("category_fxts_szsh", "", 1),
    ("category_gqbd_szsh", "", 1),
    ("", "重组", 1),
    ("", "问询", 1),
    ("", "立案", 1),
    ("", "中标", 1),
)
_FORM_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"
_CNINFO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://www.cninfo.com.cn/",
}
# FRED 只放行「产品/版本」格式的 User-Agent，其他写法会被直接断开。
_FRED_HEADERS = {"User-Agent": "FinScope/1.0 (research; public FRED graph CSV)", "Accept": "*/*"}

TEMPERATURE_RULES = [
    "市场温度 market-temperature-v2 = 趋势 30% + 广度 35% + 活跃度 20% + 波动 15%，"
    "缺失因子按权重重新归一，可用权重不足 50% 时不出分。",
    "广度：东财全市场涨跌分布（沪深京 A 股）；"
    "活跃度：两市成交额 = 上证指数 + 深证成指成交额（腾讯日 K），"
    "对比前 20 个交易日均额，盘中按已交易分钟折算；封板率来自东财涨停 / 炸板池。",
    "趋势与波动：中证全指（000985）日 K 的均线状态、20 日涨跌与 20 日波动分位。",
    "分档：≥80 过热，≥65 偏强，≥55 中性偏强，≥45 中性，≥35 中性偏弱，≥20 偏弱，其余冰点。",
]
MACRO_RULES = [
    "全球流动性 global-liquidity-v1（越高越松）：美联储净流动性 25%、美元指数 15%、"
    "10Y 实际利率 15%、高收益债利差 10%、NFCI 10%、SHIBOR 隔夜 15%、中美 10Y 利差 10%；"
    "≥65 偏松 … <35 偏紧。",
    "全球经济热度 global-growth-v1：美国失业率变化 30%、非农 3 月均值 30%、中国制造业 PMI 40%；"
    "宏观趋势 = 经济热度 × CPI 同比动能（较 3 个月前的变化）。",
    "美国数据来自 FRED 公开 CSV（St. Louis Fed），中国利率与 PMI 来自东方财富数据中心；"
    "月度数据有发布滞后，以各指标标注的期次为准。",
]
BOARD_RULES = [
    "热度指数 board-heat-v1 = 0.4×同花顺热度分 + 0.3×当日涨幅分位 + 0.3×涨停家数分（10 家封顶）。",
    "热点板块 = 热度指数 ≥ 60 且当日涨幅 ≥ 1%；领涨股取新浪同名板块，名称对不上时留空。",
    "「按涨幅」为新浪行业 / 概念板块当日等权涨幅，只列成分 ≥ 5 只的板块。",
]
STOCK_RULES = [
    "人气热股：同花顺 1 小时热榜前 10；价格、涨跌幅、市盈率(TTM) 来自腾讯行情。",
    "涨幅居前：新浪沪深 A 股涨幅榜，剔除 N / C 开头的上市初期新股与成交额 < 3 亿元的个股。",
]
ANNOUNCEMENT_RULES = [
    "公告来自巨潮资讯（证监会指定信息披露平台）全市场检索：最近两页 + "
    "业绩预告 / 风险提示 / 股权变动类别 + 重组 / 问询 / 立案 / 中标关键词。",
    "重要度只看标题关键词：风险、重组、业绩 3 级；定期报告、问询、增减持回购、合同订单 2 级；"
    "质押、分红、股权激励 1 级；中介意见、董事会决议等附属文件不计。"
    "同一股票同一类型只保留标题最短的一条。",
    "热门公告：公告主体在同花顺热榜前 20（3 级）、前 100（2 级），"
    "或当日涨停 / 跌停 / 涨幅榜前 40（1 级）。",
]
WATCH_RULES = [
    "读取你在「自选关注」里的分组；行情、涨停状态与热榜名次同自选看板。",
    "近 5 日涨跌 ≥ +3% 记为强势，≤ −3% 记为弱势，其余震荡；没有 5 日数据的不归类。",
    "走势为最近 10 个交易日收盘价（腾讯日 K，不复权）；只为分组前 12 只画走势。",
]


class CockpitGateway(Protocol):
    async def get(self, url: str, headers: Mapping[str, str]) -> bytes: ...

    async def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        content_type: str = "application/json",
    ) -> bytes: ...


def minutes_traded(moment: time) -> int:
    """连续竞价 09:30–11:30、13:00–15:00，共 240 分钟。"""

    minutes = moment.hour * 60 + moment.minute
    morning = min(max(minutes - (9 * 60 + 30), 0), 120)
    afternoon = min(max(minutes - 13 * 60, 0), 120)
    return morning + afternoon


def announcement_window(today: date) -> tuple[date, date]:
    """上一个工作日到今天；周末取到周五。"""

    back = {0: 3, 6: 2}.get(today.weekday(), 1)
    return today - timedelta(days=back), today


def bucket_label(bucket: int) -> str:
    if bucket == 11:
        return "涨停"
    if bucket == -11:
        return "跌停"
    if bucket == 0:
        return "平盘"
    if bucket == 10:
        return "≥9%"
    if bucket == -10:
        return "≤-9%"
    if bucket > 0:
        return f"{bucket - 1}~{bucket}%"
    return f"{bucket}~{bucket + 1}%"


def moving_average(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    return [
        None if index + 1 < window else _r(rules.mean(values[index + 1 - window : index + 1]))
        for index in range(len(values))
    ]


@dataclass
class DashboardService:
    gateway: CockpitGateway
    overseas: CockpitGateway
    industry: IndustryMarketService
    sentiment: MarketSentimentService
    insights: WatchlistInsightsService
    cache: TtlCache = field(default_factory=TtlCache)
    now: Callable[[], datetime] = field(default=utc_now)
    metrics: DashboardMetricRepository | None = None
    snapshots: DashboardSnapshotRepository | None = None
    _restored: bool = field(default=False, init=False, repr=False)
    _inflight: dict[str, asyncio.Task[Any]] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------ 市场：温度 + 指数 + 两市全景

    async def market(self) -> MarketOverview:
        local = self.now().astimezone(SHANGHAI)
        symbols = [symbol for symbol, _ in MAIN_INDICES] + [TREND_INDEX[0]]
        quotes, quotes_section = await self._guard(self._index_quotes(symbols))
        fenbu, breadth_section = await self._guard(self._fenbu())
        pool, limits_section = await self._pool()
        sh_bars, sh_section = await self._guard(self._klines("sh000001", "day"))
        sz_bars, sz_section = await self._guard(self._klines("sz399001", "day"))
        trend_bars, trend_section = await self._guard(self._klines(TREND_INDEX[0], "day"))
        if fenbu is not None and fenbu.trade_date is None:
            breadth_section = Section(
                status="unavailable", reason="东财今日没有涨跌分布（非交易日）"
            )
            fenbu = None

        trade_date = (fenbu.trade_date if fenbu else None) or _quote_day(quotes, "sh000001")
        amount_days = _market_amounts(sh_bars or (), sz_bars or (), quotes or {})
        fraction = rules.ONE
        if trade_date == local.date():
            traded = minutes_traded(local.time())
            if 0 < traded < 240:
                fraction = rules.session_fraction(traded)
        closes = [bar.close for bar in trend_bars or ()]
        temperature = rules.market_temperature(
            buckets=fenbu.buckets if fenbu else None,
            amounts=[amount for _, amount in amount_days],
            limit_up=pool.limit_up if pool else None,
            broken=pool.broken if pool else None,
            closes=closes,
            index_name=TREND_INDEX[1],
            session_fraction=fraction,
        )
        previous = await self._remember(trade_date, {"temperature": temperature.result.score})
        history_problems = [
            f"{label}：{section.reason}"
            for label, section in (
                ("上证日K", sh_section),
                ("深证日K", sz_section),
                ("中证全指日K", trend_section),
            )
            if section.status == "unavailable"
        ]
        history_section = (
            Section(status="unavailable", reason="；".join(history_problems))
            if history_problems
            else Section(status="ok", fetched_at=trend_section.fetched_at, data_date=trade_date)
        )
        amounts = [amount for _, amount in amount_days]
        ma20 = moving_average(amounts, 20)
        activity = temperature.activity
        return MarketOverview(
            trade_date=trade_date,
            temperature=_score_block(
                "market-temperature-v2", temperature.result, previous.get("temperature")
            ),
            quotes_section=quotes_section,
            breadth_section=breadth_section,
            limits_section=limits_section,
            history_section=history_section,
            indices=[
                _index_quote(symbol, name, (quotes or {}).get(symbol))
                for symbol, name in MAIN_INDICES
            ],
            breadth=_breadth(temperature.breadth, fenbu.buckets if fenbu else {}),
            limits=(
                LimitStats(
                    limit_up=pool.limit_up,
                    broken=pool.broken,
                    limit_down=pool.limit_down,
                    seal_rate=pool.seal_rate,
                    max_streak=pool.max_streak,
                )
                if pool
                else None
            ),
            market_amount=_r(amounts[-1]) if amounts else None,
            amount_base=_r(activity.base) if activity else None,
            amount_vs_base_pct=_r((activity.ratio - 1) * 100) if activity else None,
            amounts=[
                AmountPoint(day=day, amount=_r(amount), ma20=ma20[index])
                for index, (day, amount) in enumerate(amount_days)
            ][-60:],
            rules=TEMPERATURE_RULES,
            sources=[
                SourceRef(
                    name="腾讯行情 指数快照 / 日K",
                    url="https://gu.qq.com/",
                    fetched_at=quotes_section.fetched_at,
                ),
                SourceRef(
                    name="东方财富 涨跌分布 / 涨停板行情",
                    url="https://quote.eastmoney.com/ztb/",
                    fetched_at=breadth_section.fetched_at,
                ),
            ],
        )

    async def index_bars(self, symbol: str, period: str) -> IndexBars:
        if symbol not in INDEX_NAMES:
            raise ProviderError(
                code="VALIDATION_ERROR", message="不支持的指数代码", status_code=422
            )
        name = INDEX_NAMES[symbol]
        source = SourceRef(name="腾讯行情", url=f"https://gu.qq.com/{symbol}")
        if period == "min":
            quotes, _ = await self._guard(self._index_quotes([symbol]))
            minutes, section = await self._guard(
                self.cache.get(
                    f"minute:{symbol}",
                    _TTL_MINUTE,
                    lambda: self._fetch(
                        tx.minute_url(symbol),
                        tx.TENCENT_HEADERS,
                        lambda p: tx.parse_minutes(p, symbol),
                    ),
                )
            )
            points = list(minutes or ())
            if minutes is not None and not points:
                section = Section(status="unavailable", reason="暂无分时数据（未开盘或非交易日）")
            quote = (quotes or {}).get(symbol)
            return IndexBars(
                symbol=symbol,
                name=name,
                period=period,
                section=section,
                prev_close=quote.prev_close if quote else None,
                bars=[],
                minutes=[
                    MinuteItem(
                        clock=point.clock,
                        price=point.price,
                        amount=point.cumulative_amount
                        - (points[index - 1].cumulative_amount if index else 0),
                    )
                    for index, point in enumerate(points)
                ],
                sources=[source],
            )
        if period not in _PERIOD_BARS:
            raise ProviderError(
                code="VALIDATION_ERROR",
                message="周期只能是 min / day / week / month",
                status_code=422,
            )
        bars, section = await self._guard(self._klines(symbol, period))
        rows = list(bars or ())
        closes = [bar.close for bar in rows]
        averages = {window: moving_average(closes, window) for window in (5, 10, 20)}
        amounts = [bar.amount for bar in rows if bar.amount is not None]
        vs5 = (
            _r((amounts[-1] / rules.mean(amounts[-6:-1]) - 1) * 100)
            if period == "day" and len(amounts) > 5 and rules.mean(amounts[-6:-1]) > 0
            else None
        )
        return IndexBars(
            symbol=symbol,
            name=name,
            period=period,
            section=section,
            prev_close=rows[-2].close if len(rows) > 1 else None,
            amount_vs_5d_pct=vs5,
            bars=[
                BarItem(
                    day=bar.trade_date,
                    open=bar.open,
                    close=bar.close,
                    high=bar.high,
                    low=bar.low,
                    amount=bar.amount,
                    ma5=averages[5][index],
                    ma10=averages[10][index],
                    ma20=averages[20][index],
                )
                for index, bar in enumerate(rows)
            ],
            minutes=[],
            sources=[source],
        )

    # ------------------------------------------------------------ 全球宏观

    async def warm_macro(self) -> None:
        """只预热宏观上游缓存（不写指标表），让用户打开页面时直接命中缓存。"""

        await asyncio.gather(self._fred(), self._china_rates(), self._guard(self._assets()))

    async def macro(self) -> MacroOverview:
        # 三组上游互不依赖，并发拉取；总耗时取最慢的一组而不是三组相加。
        (fred, us_section), (china, china_section), (assets, assets_section) = await asyncio.gather(
            self._fred(), self._china_rates(), self._guard(self._assets())
        )
        shibor = china.get("shibor", [])
        pmi = china.get("pmi", [])
        yields = [
            rules.YieldPoint(row.day, row.cn10y, row.us10y)
            for row in china.get("yields", [])
            if isinstance(row, rates.YieldRow) and row.cn10y is not None and row.us10y is not None
        ]
        liquidity = rules.global_liquidity(
            rules.LiquidityInputs(
                walcl=fred.get("WALCL", ()),
                tga=fred.get("WTREGEN", ()),
                rrp=fred.get("RRPONTSYD", ()),
                dollar=fred.get("DTWEXBGS", ()),
                real_yield=fred.get("DFII10", ()),
                hy_spread=fred.get("BAMLH0A0HYM2", ()),
                nfci=fred.get("NFCI", ()),
                shibor_on=[_obs(row) for row in shibor if isinstance(row, rates.DatedValue)],
                yields=yields,
            )
        )
        growth = rules.global_growth(
            unemployment=fred.get("UNRATE", ()),
            payrolls=fred.get("PAYEMS", ()),
            china_pmi=[_obs(row) for row in pmi if isinstance(row, rates.DatedValue)],
            cpi=fred.get("CPIAUCSL", ()),
        )
        previous = await self._remember(
            await self._trade_day(),
            {"liquidity": liquidity.result.score, "growth": growth.result.score},
        )
        return MacroOverview(
            liquidity=_score_block(
                "global-liquidity-v1", liquidity.result, previous.get("liquidity")
            ),
            growth=_score_block("global-growth-v1", growth.result, previous.get("growth")),
            regime=growth.regime,
            cpi_yoy=_r(growth.cpi_yoy) if growth.cpi_yoy is not None else None,
            inflation_momentum=(
                _r(growth.inflation_momentum) if growth.inflation_momentum is not None else None
            ),
            us_section=us_section,
            china_section=china_section,
            assets_section=assets_section,
            tiles=_macro_tiles(growth, fred, yields),
            assets=assets or [],
            net_liquidity=[_point(row) for row in liquidity.net_liquidity[-52:]],
            dollar=[_point(row) for row in fred.get("DTWEXBGS", ())[-250:]],
            real_yield=[_point(row) for row in fred.get("DFII10", ())[-250:]],
            shibor_on=[
                _point(_obs(row)) for row in shibor[-60:] if isinstance(row, rates.DatedValue)
            ],
            cn_us_spread=[
                SeriesPoint(day=row.day, value=_r((row.cn10y - row.us10y) * 100))
                for row in yields[-120:]
            ],
            rules=MACRO_RULES,
            sources=[
                SourceRef(
                    name="FRED 公开 CSV（St. Louis Fed）",
                    url="https://fred.stlouisfed.org/",
                    fetched_at=us_section.fetched_at,
                ),
                SourceRef(
                    name="东方财富 数据中心（SHIBOR / 国债收益率 / PMI）",
                    url="https://data.eastmoney.com/cjsj/",
                    fetched_at=china_section.fetched_at,
                ),
                SourceRef(
                    name="腾讯行情 / 新浪财经 外盘",
                    url="https://gu.qq.com/",
                    fetched_at=assets_section.fetched_at,
                ),
            ],
        )

    # ------------------------------------------------------------ 热点板块

    async def boards(self) -> BoardsOverview:
        plates, heat_section = await self._guard(self.industry.ths_plates())
        sina_boards, gain_section = await self._guard(self._sina_boards())
        industries, concepts = plates if plates is not None else ((), ())
        inputs = [
            rules.PlateInput(
                row.code, row.name, kind, row.heat, row.change_pct, row.tag, row.hot_tag
            )
            for kind, group in (("概念", concepts), ("行业", industries))
            for row in group
        ]
        heat = rules.board_heat(inputs)
        leaders = {_board_key(board.name): board for board in sina_boards or ()}
        hot_count = sum(1 for row in heat if row.hot) if plates is not None else None
        trade_date = await self._trade_day()
        previous = await self._remember(
            trade_date, {"hot_boards": Decimal(hot_count) if hot_count is not None else None}
        )
        before = previous.get("hot_boards")
        previous_hot = int(before.value) if before else None
        gainers = sorted(
            (
                board
                for board in sina_boards or ()
                if board.members >= 5 and board.change_pct is not None
            ),
            key=lambda board: board.change_pct or Decimal(0),
            reverse=True,
        )
        return BoardsOverview(
            trade_date=trade_date,
            heat_section=heat_section,
            gain_section=gain_section,
            hot_count=hot_count,
            previous_hot_count=previous_hot,
            hot_count_change=(
                hot_count - previous_hot
                if hot_count is not None and previous_hot is not None
                else None
            ),
            previous_date=before.trade_date if before else None,
            boards=[_hot_board(row, leaders.get(_board_key(row.plate.name))) for row in heat[:24]],
            gainers=[_gain_board(board) for board in gainers[:_TOP]],
            rules=BOARD_RULES,
            sources=[
                SourceRef(
                    name="同花顺 热门板块",
                    url="https://eq.10jqka.com.cn/frontend/thsTopRank/index.html",
                    fetched_at=heat_section.fetched_at,
                ),
                SourceRef(
                    name="新浪财经 行业 / 概念板块",
                    url="https://vip.stock.finance.sina.com.cn/mkt/",
                    fetched_at=gain_section.fetched_at,
                ),
            ],
        )

    # ------------------------------------------------------------ 热点股票

    async def stocks(self) -> StocksOverview:
        ths, hot_section = await self._guard(self.industry.ths_stocks())
        top = list(ths or ())[:_TOP]
        quotes: dict[str, tx.Quote] = {}
        if top:
            symbols = sorted({tx.a_share_symbol(row.code) for row in top if _is_a_share(row.code)})
            found, quote_section = await self._guard(self._stock_quotes(symbols))
            quotes = found or {}
            if quote_section.status == "unavailable":
                hot_section = Section(
                    status="stale",
                    reason=f"腾讯行情不可用，价格与市盈率留空：{quote_section.reason}",
                    fetched_at=hot_section.fetched_at,
                )
        gainers, gain_section = await self._guard(self._gainers())
        return StocksOverview(
            hot_section=hot_section,
            gain_section=gain_section,
            hot=[_hot_stock(row, quotes) for row in top],
            gainers=[
                HotStockItem(
                    code=row.code,
                    name=row.name,
                    last=row.last,
                    change_pct=_r_opt(row.change_pct),
                    amount=row.amount,
                    turnover_pct=row.turnover_pct,
                )
                for row in _filter_gainers(gainers or ())[:_TOP]
            ],
            rules=STOCK_RULES,
            sources=[
                SourceRef(
                    name="同花顺 热榜",
                    url="https://eq.10jqka.com.cn/frontend/thsTopRank/index.html",
                    fetched_at=hot_section.fetched_at,
                ),
                SourceRef(name="腾讯行情 个股快照", url="https://gu.qq.com/"),
                SourceRef(
                    name="新浪财经 沪深A股涨幅榜",
                    url="https://vip.stock.finance.sina.com.cn/mkt/#hs_a",
                    fetched_at=gain_section.fetched_at,
                ),
            ],
        )

    # ------------------------------------------------------------ 公告

    async def announcements(self) -> AnnouncementsOverview:
        start, end = announcement_window(self.now().astimezone(SHANGHAI).date())
        cached, section = await self._guard(
            self.cache.get(
                f"cninfo:{start.isoformat()}:{end.isoformat()}",
                _TTL_ANNOUNCEMENTS,
                lambda: self._load_announcements(start, end),
            )
        )
        rows, problems = cached if cached is not None else ((), ())
        if cached is not None and problems:
            section = Section(
                status="stale",
                reason="部分检索失败：" + "；".join(problems),
                fetched_at=section.fetched_at,
            )
        heat, heat_note = await self._stock_heat()
        if heat_note and section.status != "unavailable":
            section = Section(
                status="stale",
                reason="；".join(part for part in (section.reason, heat_note) if part),
                fetched_at=section.fetched_at,
            )
        items = _dedupe([_announcement(row, heat.get(row.code)) for row in rows])
        important = sorted(
            (item for item in items if item.level >= 2),
            key=lambda item: (item.level, item.published_at),
            reverse=True,
        )[:_TOP]
        hot = sorted(
            (item for item in items if item.level >= 1 and item.heat_level),
            key=lambda item: (item.heat_level or 0, item.level, item.published_at),
            reverse=True,
        )[:_TOP]
        counts = Counter(item.kind for item in items if item.level >= 1)
        return AnnouncementsOverview(
            section=section,
            window_start=start,
            window_end=end,
            scanned=len(rows),
            important=important,
            hot=hot,
            counts=[CountItem(name=name, count=count) for name, count in counts.most_common()],
            rules=ANNOUNCEMENT_RULES,
            sources=[
                SourceRef(
                    name="巨潮资讯 全市场公告检索",
                    url="https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
                    fetched_at=section.fetched_at,
                )
            ],
        )

    # ------------------------------------------------------------ 我的重点关注

    async def watch(
        self,
        group: WatchGroup | None,
        groups: Sequence[WatchGroup],
        recent: Callable[[Sequence[UUID], date], dict[UUID, list[RecentClose]]],
    ) -> WatchOverview:
        refs = [WatchGroupRef(id=item.id, name=item.name, count=len(item.items)) for item in groups]
        if group is None or not group.items:
            empty = Section(status="unavailable", reason="还没有自选股，请到「自选关注」添加")
            return WatchOverview(
                group_id=group.id if group else None,
                group_name=group.name if group else None,
                groups=refs,
                quote_section=empty,
                spark_section=empty,
                total=0,
                strong=0,
                neutral=0,
                weak=0,
                unknown=0,
                rows=[],
                rules=WATCH_RULES,
                sources=[],
            )
        insights = await self.insights.build(group, recent)
        rows: list[WatchRow] = []
        problems: list[str] = []
        for index, stock in enumerate(insights.stocks):
            closes: list[Decimal] = []
            if index < _SPARK_LIMIT and _is_a_share(stock.code):
                symbol = tx.a_share_symbol(stock.code)
                try:
                    bars = await self.cache.get(
                        f"spark:{symbol}",
                        _TTL_SPARK,
                        partial(
                            self._fetch,
                            tx.kline_url(symbol, 12),
                            tx.TENCENT_HEADERS,
                            partial(tx.parse_klines, symbol=symbol),
                        ),
                    )
                    closes = [bar.close for bar in bars.value][-10:]
                except ProviderError as error:
                    problems.append(f"{stock.name}：{error.message}")
            change_5d = stock.change_5d_pct
            if change_5d is None and len(closes) > 5:
                change_5d = _r(rules.pct_change(closes[-1], closes[-6]) or Decimal(0))
            rows.append(
                WatchRow(
                    code=stock.code,
                    name=stock.name,
                    last=stock.last,
                    change_pct=stock.change_pct,
                    change_5d_pct=change_5d,
                    state=rules.watch_state(change_5d),
                    closes=closes,
                    signals=[
                        SignalItem(tone=tone, text=text)
                        for tone, text in rules.watch_signals(
                            change_pct=stock.change_pct,
                            limit_status=stock.limit_status,
                            streak=stock.streak,
                            ths_hot_rank=stock.ths_hot_rank,
                        )
                    ],
                )
            )
        states = Counter(row.state for row in rows)
        spark_section = (
            Section(status="stale", reason="部分走势不可用：" + "；".join(problems[:3]))
            if problems
            else Section(status="ok")
        )
        return WatchOverview(
            group_id=group.id,
            group_name=group.name,
            groups=refs,
            quote_section=insights.quote_section,
            spark_section=spark_section,
            total=len(rows),
            strong=states.get("强势", 0),
            neutral=states.get("震荡", 0),
            weak=states.get("弱势", 0),
            unknown=states.get(None, 0),
            rows=rows,
            rules=WATCH_RULES,
            sources=[
                *insights.sources[:1],
                SourceRef(name="腾讯行情 日K", url="https://gu.qq.com/"),
            ],
        )

    # ------------------------------------------------------------ 取数

    async def _index_quotes(self, symbols: list[str]) -> Cached[dict[str, tx.Quote]]:
        key = ",".join(sorted(symbols))
        return await self.cache.get(
            f"quotes:{key}",
            _TTL_QUOTES,
            lambda: self._fetch(tx.quote_url(sorted(symbols)), tx.TENCENT_HEADERS, tx.parse_quotes),
        )

    async def _stock_quotes(self, symbols: list[str]) -> Cached[dict[str, tx.Quote]]:
        return await self._index_quotes(symbols)

    async def _klines(self, symbol: str, period: str) -> Cached[tuple[tx.DailyBar, ...]]:
        kind: tx.KlinePeriod = (
            "week" if period == "week" else "month" if period == "month" else "day"
        )
        count = _PERIOD_BARS[kind]
        return await self.cache.get(
            f"kline:{symbol}:{kind}",
            _TTL_KLINE,
            lambda: self._fetch(
                tx.kline_url(symbol, count, kind),
                tx.TENCENT_HEADERS,
                lambda payload: tx.parse_klines(payload, symbol, kind),
            ),
        )

    async def _fenbu(self) -> Cached[es.Distribution]:
        return await self.cache.get(
            "fenbu",
            _TTL_FENBU,
            lambda: self._fetch(es.fenbu_url(), em.EASTMONEY_HEADERS, es.parse_fenbu),
        )

    async def _pool(self) -> tuple[LimitUpView | None, Section]:
        try:
            view = await self.sentiment.limit_up()
        except ProviderError as error:
            return None, Section(status="unavailable", reason=f"涨停池暂不可用：{error.message}")
        if view.section.status == "unavailable":
            return None, view.section
        return view, view.section

    async def _trade_day(self) -> date | None:
        try:
            fenbu = await self._fenbu()
        except ProviderError:
            return None
        return fenbu.value.trade_date

    async def _fred(self) -> tuple[dict[str, tuple[rules.Observation, ...]], Section]:
        found: dict[str, tuple[rules.Observation, ...]] = {}
        failed: list[str] = []
        stale = False
        fetched: datetime | None = None

        await self._restore_snapshots()

        async def one(series_id: str) -> Cached[tuple[rules.Observation, ...]] | str:
            key = f"fred:{series_id}"
            task = self._inflight_task(
                key,
                lambda: self.cache.get(key, _TTL_FRED, partial(self._load_fred, series_id)),
            )
            # 手里已有旧值（内存或库里的快照）就只多等一小会儿，等不到先用旧值。
            budget = _FRED_STALE_BUDGET_SECONDS if self.cache.peek(key) else _FRED_BUDGET_SECONDS
            try:
                # shield：本次请求等不及时放弃等待，但回源继续在后台跑完并写入缓存，
                # 用户刷新或后台预热就能命中，而不是每次都从头再等一遍。
                return await asyncio.wait_for(asyncio.shield(task), timeout=budget)
            except ProviderError as error:
                return f"{series_id}（{error.message}）"
            except TimeoutError:
                old = self.cache.peek(key)
                if old is not None:
                    return old
                return f"{series_id}（正在后台加载，稍后刷新）"

        # 序列互相独立：并发回源（出口限速仍然生效），冷启动不再是 9 次串行往返。
        results = await asyncio.gather(*(one(series_id) for series_id in FRED_SERIES))
        for series_id, cached in zip(FRED_SERIES, results, strict=True):
            if isinstance(cached, str):
                failed.append(cached)
                continue
            found[series_id] = cached.value
            stale = stale or cached.stale
            fetched = cached.fetched_at if fetched is None else min(fetched, cached.fetched_at)
        if not found:
            return found, Section(
                status="unavailable", reason="FRED 暂不可用：" + "；".join(failed[:3])
            )
        reason = "以下序列暂不可用：" + "；".join(failed) if failed else None
        return found, Section(
            status="stale" if failed or stale else "ok", reason=reason, fetched_at=fetched
        )

    def _inflight_task(self, key: str, factory: Callable[[], Awaitable[T]]) -> asyncio.Task[T]:
        """同一 key 的回源只起一个后台任务；调用方放弃等待也不会取消它。"""

        existing = self._inflight.get(key)
        if existing is not None and not existing.done():
            return existing
        task: asyncio.Task[T] = asyncio.ensure_future(factory())
        self._inflight[key] = task

        def finished(done: asyncio.Task[T]) -> None:
            if self._inflight.get(key) is done:
                del self._inflight[key]
            if not done.cancelled():
                done.exception()  # 取走异常，避免 “Task exception was never retrieved”

        task.add_done_callback(finished)
        return task

    async def _load_fred(self, series_id: str) -> tuple[rules.Observation, ...]:
        today = self.now().astimezone(SHANGHAI).date()
        url = graph_csv_url(
            series_id=series_id,
            observation_start=today - timedelta(days=_FRED_LOOKBACK_DAYS),
            observation_end=today,
        )
        payload = await self.overseas.get(url, _FRED_HEADERS)
        rows = parse_graph_csv(
            payload, series_id=series_id, realtime_start=public_realtime_start(self.now())
        )
        if not rows:
            raise ProviderError(code="NO_ROWS", message="FRED 没有返回观测", status_code=502)
        observations = tuple(rules.Observation(row.observation_date, row.value) for row in rows)
        await self._save_snapshot(f"fred:{series_id}", observations)
        return observations

    async def _save_snapshot(self, key: str, observations: Sequence[rules.Observation]) -> None:
        if self.snapshots is None:
            return
        try:
            await asyncio.to_thread(
                self.snapshots.save, key, _snapshot_payload(observations), self.now()
            )
        except SQLAlchemyError:
            logger.warning("dashboard snapshot save failed: %s", key, exc_info=True)

    async def ingest_fred(self, series: Mapping[str, Sequence[tuple[date, Decimal]]]) -> int:
        """接收外部推送的 FRED 序列：先落库（失败直接报错），再放进缓存。

        服务器直连 FRED 不通时用它补数据；缓存有效期更长，避免每次请求都再去碰不通的上游。
        """

        unknown = sorted(set(series) - set(FRED_SERIES))
        if unknown:
            raise AppError(
                code="VALIDATION_ERROR",
                message="不是总览使用的 FRED 序列：" + "、".join(unknown),
                status_code=422,
            )
        if self.snapshots is None:
            raise AppError(code="SERVICE_UNAVAILABLE", message="数据库未就绪", status_code=503)
        fetched_at = self.now()
        total = 0
        for code, points in series.items():
            ordered = sorted(points, key=lambda point: point[0])
            observations = tuple(rules.Observation(day, value) for day, value in ordered)
            key = f"fred:{code}"
            await asyncio.to_thread(
                self.snapshots.save, key, _snapshot_payload(observations), fetched_at
            )
            self.cache.put(key, observations, fetched_at, _TTL_FRED_PUSHED)
            total += len(observations)
        return total

    async def _restore_snapshots(self) -> None:
        """进程首次读取前，把库里上次成功抓取的 FRED 序列放进缓存（视为已过期）。"""

        if self._restored or self.snapshots is None:
            return
        self._restored = True
        try:
            rows = await asyncio.to_thread(self.snapshots.load, "fred:")
        except SQLAlchemyError:
            logger.warning("dashboard snapshot restore failed", exc_info=True)
            return
        for key, (payload, fetched_at) in rows.items():
            try:
                observations = tuple(
                    rules.Observation(date.fromisoformat(day), Decimal(value))
                    for day, value in payload
                )
            except (ValueError, TypeError, ArithmeticError):
                continue
            if observations:
                self.cache.seed(key, observations, fetched_at)

    async def _china_rates(self) -> tuple[dict[str, list[object]], Section]:
        loaders: dict[str, tuple[str, Callable[[bytes], Sequence[object]]]] = {
            "shibor": (rates.shibor_url("ON"), lambda p: rates.parse_dated(p, "IR_RATE")),
            "yields": (rates.treasury_url(), rates.parse_treasury),
            "pmi": (rates.pmi_url(), lambda p: rates.parse_dated(p, "MAKE_INDEX")),
        }
        labels = {"shibor": "SHIBOR", "yields": "中美国债收益率", "pmi": "中国 PMI"}
        found: dict[str, list[object]] = {}
        failed: list[str] = []
        fetched: datetime | None = None
        for key, (url, parse) in loaders.items():
            try:
                cached = await self.cache.get(
                    f"china:{key}",
                    _TTL_CHINA,
                    partial(self._fetch, url, rates.DATACENTER_HEADERS, parse),
                )
            except ProviderError as error:
                failed.append(f"{labels[key]}（{error.message}）")
                continue
            found[key] = list(cached.value)
            fetched = cached.fetched_at if fetched is None else min(fetched, cached.fetched_at)
        if not found:
            return found, Section(
                status="unavailable", reason="东财数据中心暂不可用：" + "；".join(failed)
            )
        return found, Section(
            status="stale" if failed else "ok",
            reason="以下数据暂不可用：" + "；".join(failed) if failed else None,
            fetched_at=fetched,
        )

    async def _assets(self) -> Cached[list[GlobalAsset]]:
        return await self.cache.get("assets", _TTL_ASSETS, self._load_assets)

    async def _load_assets(self) -> list[GlobalAsset]:
        symbols = [code for code, _ in GLOBAL_TENCENT] + ["whUSDCNY"]
        payload = await self.gateway.get(tx.quote_url(symbols), tx.TENCENT_HEADERS)
        quotes = tx.parse_quotes(payload)
        assets = [
            GlobalAsset(
                code=code,
                name=name,
                last=quote.last,
                change_pct=_r_opt(rules.pct_change(quote.last, quote.prev_close)),
                quote_time=quote.quote_time,
            )
            for code, name in GLOBAL_TENCENT
            if (quote := quotes.get(code)) is not None
        ]
        try:
            fx = tx.parse_fx(payload, "whUSDCNY")
            assets.append(
                GlobalAsset(
                    code="USDCNY",
                    name="美元兑人民币",
                    last=fx.last,
                    change_pct=fx.change_pct,
                    quote_time=fx.quote_date,
                )
            )
        except ProviderError:
            pass
        try:
            sina_quotes = sina.parse_hq(
                await self.gateway.get(
                    sina.hq_url([code for code, _ in GLOBAL_SINA]), sina.SINA_HEADERS
                )
            )
        except ProviderError:
            sina_quotes = {}
        for code, name in GLOBAL_SINA:
            future = sina_quotes.get(code)
            if future is not None:
                assets.append(
                    GlobalAsset(
                        code=code,
                        name=name,
                        last=future.last,
                        change_pct=_r_opt(rules.pct_change(future.last, future.prev_close)),
                        quote_time=future.quote_time,
                    )
                )
        if not assets:
            raise ProviderError(code="NO_ROWS", message="外盘行情没有返回数据", status_code=502)
        return assets

    async def _sina_boards(self) -> Cached[tuple[sina.SinaBoard, ...]]:
        async def load() -> tuple[sina.SinaBoard, ...]:
            industries = await self._fetch(sina.INDUSTRY_URL, sina.SINA_HEADERS, sina.parse_boards)
            concepts = await self._fetch(sina.CONCEPT_URL, sina.SINA_HEADERS, sina.parse_boards)
            return (*industries, *concepts)

        return await self.cache.get("sina:boards", _TTL_SINA, load)

    async def _gainers(self) -> Cached[tuple[sina.SinaStock, ...]]:
        return await self.cache.get(
            "sina:gainers",
            _TTL_SINA,
            lambda: self._fetch(
                sina.rank_url("changepercent", 80), sina.SINA_HEADERS, sina.parse_rank
            ),
        )

    async def _load_announcements(
        self, start: date, end: date
    ) -> tuple[tuple[MarketAnnouncement, ...], tuple[str, ...]]:
        found: dict[str, MarketAnnouncement] = {}
        problems: list[str] = []
        for category, keyword, page in ANNOUNCEMENT_QUERIES:
            form = cninfo_market_form(
                start=start, end=end, page=page, category=category, searchkey=keyword
            )
            try:
                payload = await self.gateway.post(
                    CNINFO_QUERY, urlencode(form).encode(), _CNINFO_HEADERS, content_type=_FORM_TYPE
                )
                for row in parse_cninfo_market(payload):
                    found.setdefault(row.announcement_id, row)
            except ProviderError as error:
                problems.append(f"{keyword or category or f'第{page}页'}：{error.message}")
        if not found and problems:
            raise ProviderError(
                code="UPSTREAM_ERROR", message="巨潮检索全部失败：" + problems[0], status_code=502
            )
        rows = sorted(found.values(), key=lambda row: row.published_at, reverse=True)
        return tuple(rows), tuple(problems)

    async def _stock_heat(self) -> tuple[dict[str, rules.StockHeat], str | None]:
        notes: list[str] = []
        ranks: list[tuple[str, int]] = []
        limit_up: list[tuple[str, int | None]] = []
        limit_down: list[str] = []
        gainers: list[str] = []
        try:
            ranks = [(row.code, row.rank) for row in (await self.industry.ths_stocks()).value]
        except ProviderError as error:
            notes.append(f"同花顺热榜：{error.message}")
        pool, pool_section = await self._pool()
        if pool is not None:
            limit_up = [(row.code, row.streak) for row in pool.limit_up_stocks]
            limit_down = [row.code for row in pool.limit_down_stocks]
        else:
            notes.append(f"涨停池：{pool_section.reason}")
        try:
            gainers = [row.code for row in _filter_gainers((await self._gainers()).value)]
        except ProviderError as error:
            notes.append(f"涨幅榜：{error.message}")
        heat = rules.stock_heat(
            ths_ranks=ranks, limit_up=limit_up, limit_down=limit_down, gainers=gainers
        )
        return heat, ("热度判断缺少 " + "、".join(notes)) if notes else None

    async def _remember(
        self, trade_date: date | None, values: dict[str, Decimal | None]
    ) -> dict[str, StoredMetric]:
        if self.metrics is None or trade_date is None:
            return {}
        present = {key: value for key, value in values.items() if value is not None}
        try:
            await asyncio.to_thread(self.metrics.record, trade_date, present, self.now())
            return await asyncio.to_thread(self.metrics.previous, list(values), trade_date)
        except SQLAlchemyError:
            logger.warning("dashboard metrics unavailable", exc_info=True)
            return {}

    async def _fetch(self, url: str, headers: Mapping[str, str], parse: Callable[[bytes], T]) -> T:
        return parse(await self.gateway.get(url, headers))

    async def _guard(self, pending: Awaitable[Cached[T]]) -> tuple[T | None, Section]:
        try:
            cached = await pending
        except ProviderError as error:
            return None, Section(status="unavailable", reason=error.message)
        if cached.stale:
            return cached.value, Section(
                status="stale",
                reason=f"上游刷新失败，展示上次成功抓取的数据：{cached.stale_reason}",
                fetched_at=cached.fetched_at,
            )
        return cached.value, Section(status="ok", fetched_at=cached.fetched_at)


# ---------------------------------------------------------------- 组装


def _score_block(method: str, result: rules.Composite, previous: StoredMetric | None) -> ScoreBlock:
    before = _r_opt(previous.value, 1) if previous else None
    return ScoreBlock(
        method=method,
        score=result.score,
        label=result.label,
        coverage=result.coverage,
        factors=[
            FactorItem(
                code=factor.code,
                label=factor.label,
                weight=factor.weight,
                score=factor.score,
                raw=factor.raw,
                formula=factor.formula,
                value=_r_opt(factor.value, 4),
                contribution=share,
            )
            for factor, share in zip(result.factors, rules.contributions(result), strict=True)
        ],
        missing=list(result.missing),
        previous=before,
        previous_date=previous.trade_date if previous else None,
        change=result.score - before if result.score is not None and before is not None else None,
    )


def _index_quote(symbol: str, name: str, quote: tx.Quote | None) -> IndexQuote:
    if quote is None:
        return IndexQuote(symbol=symbol, name=name)
    change = (
        quote.last - quote.prev_close
        if quote.last is not None and quote.prev_close is not None
        else None
    )
    amplitude = (
        (quote.high - quote.low) / quote.prev_close * 100
        if quote.high is not None and quote.low is not None and quote.prev_close
        else None
    )
    return IndexQuote(
        symbol=symbol,
        name=name,
        last=quote.last,
        prev_close=quote.prev_close,
        open=quote.open,
        high=quote.high,
        low=quote.low,
        change=_r_opt(change),
        change_pct=_r_opt(rules.pct_change(quote.last, quote.prev_close)),
        amplitude_pct=_r_opt(amplitude),
        amount=quote.amount,
        quote_time=quote.quote_time,
    )


def _market_amounts(
    sh: Sequence[tx.DailyBar], sz: Sequence[tx.DailyBar], quotes: Mapping[str, tx.Quote]
) -> list[tuple[date, Decimal]]:
    """两市成交额 = 上证指数 + 深证成指（腾讯口径下深证成指成交额即深市全部）。

    日 K 尚未包含当日时用指数快照的当日成交额补上。
    """

    sz_by_day = {bar.trade_date: bar.amount for bar in sz if bar.amount is not None}
    rows = [
        (bar.trade_date, bar.amount + sz_by_day[bar.trade_date])
        for bar in sh
        if bar.amount is not None and bar.trade_date in sz_by_day
    ]
    sh_quote, sz_quote = quotes.get("sh000001"), quotes.get("sz399001")
    if not rows or sh_quote is None or sz_quote is None:
        return rows
    day = sh_quote.quote_date
    if (
        day is not None
        and day > rows[-1][0]
        and sh_quote.amount is not None
        and sz_quote.amount is not None
    ):
        rows.append((day, sh_quote.amount + sz_quote.amount))
    return rows


def _quote_day(quotes: Mapping[str, tx.Quote] | None, symbol: str) -> date | None:
    quote = (quotes or {}).get(symbol)
    return quote.quote_date if quote else None


def _breadth(stats: rules.Breadth | None, buckets: Mapping[int, int]) -> BreadthItem | None:
    if stats is None:
        return None
    return BreadthItem(
        up=stats.up,
        down=stats.down,
        flat=stats.flat,
        total=stats.total,
        strong=stats.strong,
        weak=stats.weak,
        limit_up=stats.limit_up,
        limit_down=stats.limit_down,
        buckets=[
            BucketItem(bucket=bucket, label=bucket_label(bucket), count=buckets.get(bucket, 0))
            for bucket in range(-11, 12)
        ],
    )


def _macro_tiles(
    growth: rules.Growth,
    fred: Mapping[str, Sequence[rules.Observation]],
    yields: Sequence[rules.YieldPoint],
) -> list[MacroTile]:
    cpi = fred.get("CPIAUCSL", ())
    unemployment = fred.get("UNRATE", ())
    funds = fred.get("FEDFUNDS", ())
    cpi_change = (
        growth.cpi_yoy - growth.cpi_prev_yoy
        if growth.cpi_yoy is not None and growth.cpi_prev_yoy is not None
        else None
    )
    return [
        MacroTile(
            code="US_CPI_YOY",
            label="美国CPI同比",
            value=_r_opt(growth.cpi_yoy),
            unit="%",
            change=_r_opt(cpi_change),
            change_unit="pp",
            change_label="较上月",
            period=f"{cpi[-1].day:%Y-%m}" if cpi else None,
        ),
        MacroTile(
            code="UNRATE",
            label="美国失业率",
            value=unemployment[-1].value if unemployment else None,
            unit="%",
            change=unemployment[-1].value - unemployment[-2].value
            if len(unemployment) > 1
            else None,
            change_unit="pp",
            change_label="较上月",
            period=f"{unemployment[-1].day:%Y-%m}" if unemployment else None,
        ),
        MacroTile(
            code="FEDFUNDS",
            label="联邦基金利率",
            value=funds[-1].value if funds else None,
            unit="%",
            change=_r_opt((funds[-1].value - funds[-2].value) * 100) if len(funds) > 1 else None,
            change_unit="bp",
            change_label="较上月",
            period=f"{funds[-1].day:%Y-%m} 月均" if funds else None,
        ),
        MacroTile(
            code="CN10Y",
            label="中国10年国债",
            value=yields[-1].cn10y if yields else None,
            unit="%",
            change=_r_opt((yields[-1].cn10y - yields[-2].cn10y) * 100) if len(yields) > 1 else None,
            change_unit="bp",
            change_label="较前日",
            period=yields[-1].day.isoformat() if yields else None,
        ),
    ]


def _hot_board(row: rules.PlateHeat, leader: sina.SinaBoard | None) -> HotBoardItem:
    return HotBoardItem(
        code=row.plate.code,
        name=row.plate.name,
        kind=row.plate.kind,
        heat=row.plate.heat,
        change_pct=_r_opt(row.plate.change_pct),
        limit_ups=row.limit_ups,
        heat_score=row.heat_score,
        change_score=row.change_score,
        limit_score=row.limit_score,
        index=row.index,
        hot=row.hot,
        tag=row.plate.tag,
        hot_tag=row.plate.hot_tag,
        leader_code=leader.leader_code if leader else None,
        leader_name=leader.leader_name if leader else None,
        leader_change_pct=leader.leader_change_pct if leader else None,
    )


def _board_key(name: str) -> str:
    """同花顺与新浪板块体系不同，只认去掉「概念 / 行业 / 板块」后缀后的同名，不做模糊匹配。"""

    return _BOARD_SUFFIX.sub("", name.replace(" ", ""))


def _gain_board(board: sina.SinaBoard) -> GainBoardItem:
    return GainBoardItem(
        code=board.code,
        name=board.name,
        kind="行业" if board.code.startswith("new_") else "概念",
        members=board.members,
        change_pct=_r_opt(board.change_pct),
        amount=board.amount,
        leader_code=board.leader_code,
        leader_name=board.leader_name,
        leader_change_pct=_r_opt(board.leader_change_pct),
    )


def _hot_stock(row: em.ThsHotStock, quotes: Mapping[str, tx.Quote]) -> HotStockItem:
    quote = quotes.get(tx.a_share_symbol(row.code)) if _is_a_share(row.code) else None
    return HotStockItem(
        code=row.code,
        name=row.name,
        rank=row.rank,
        last=quote.last if quote else None,
        change_pct=(
            quote.change_pct if quote and quote.change_pct is not None else _r_opt(row.change_pct)
        ),
        pe_ttm=quote.pe_ttm if quote else None,
        amount=quote.amount if quote else None,
        turnover_pct=quote.turnover_pct if quote else None,
        tag=row.popularity_tag or (row.concept_tags[0] if row.concept_tags else None),
        concepts=list(row.concept_tags[:3]),
    )


def _filter_gainers(rows: Sequence[sina.SinaStock]) -> list[sina.SinaStock]:
    return [
        row
        for row in rows
        if not row.name.startswith(("N", "C"))
        and row.amount is not None
        and row.amount >= _GAINER_MIN_AMOUNT
        and row.change_pct is not None
    ]


def _announcement(row: MarketAnnouncement, heat: rules.StockHeat | None) -> AnnouncementItem:
    kind = rules.classify_announcement(row.title)
    return AnnouncementItem(
        id=row.announcement_id,
        code=row.code,
        name=row.name,
        title=rules.clean_title(row.title, row.code, row.name),
        url=row.url,
        published_at=row.published_at,
        kind=kind.kind,
        level=kind.level,
        tone=kind.tone,
        heat_level=heat.level if heat else None,
        heat_reason=heat.reason if heat else None,
    )


def _dedupe(items: Sequence[AnnouncementItem]) -> list[AnnouncementItem]:
    best: dict[tuple[str, str], AnnouncementItem] = {}
    for item in items:
        key = (item.code, item.kind)
        current = best.get(key)
        if current is None or len(item.title) < len(current.title):
            best[key] = item
    return list(best.values())


def _is_a_share(code: str) -> bool:
    return len(code) == 6 and code.isdigit()


def _obs(row: rates.DatedValue) -> rules.Observation:
    return rules.Observation(row.day, row.value)


def _point(row: rules.Observation) -> SeriesPoint:
    return SeriesPoint(day=row.day, value=rules.round_to(row.value, 4))


def _r(value: Decimal, places: int = 2) -> Decimal:
    return rules.round_to(value, places)


def _r_opt(value: Decimal | None, places: int = 2) -> Decimal | None:
    return None if value is None else _r(value, places)


_service: DashboardService | None = None


def _snapshot_payload(observations: Sequence[rules.Observation]) -> list[list[str]]:
    return [[point.day.isoformat(), str(point.value)] for point in observations]


def attach_repositories(service: DashboardService, engine: Engine) -> None:
    if service.metrics is None:
        service.metrics = DashboardMetricRepository(engine)
    if service.snapshots is None:
        service.snapshots = DashboardSnapshotRepository(engine)


async def keep_macro_warm(engine: Engine, interval_seconds: float = 300.0) -> None:
    """启动后立即预热一次，之后定期续命缓存；任何异常只记日志，不影响接口。"""

    attach_repositories(get_dashboard_service(), engine)
    while True:
        try:
            await get_dashboard_service().warm_macro()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("macro warm-up failed", exc_info=True)
        await asyncio.sleep(interval_seconds)


def get_dashboard_service() -> DashboardService:
    global _service
    if _service is None:
        _service = DashboardService(
            gateway=shared_gateway(),
            overseas=PacedGateway(min_interval=0.2, timeout=20.0, concurrency=5),
            industry=get_service(),
            sentiment=get_sentiment_service(),
            insights=get_insights_service(),
        )
    return _service
