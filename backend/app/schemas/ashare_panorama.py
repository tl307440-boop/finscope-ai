"""A 股全景看板响应模型。

字段名直接用 camelCase，和前端 `api/aSharePanorama.ts` 的类型一一对应。
所有「抓不到」的字段都是 None，并在 `meta.unavailable` 里写明原因；任何地方都不做模拟兜底。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Session = Literal["intraday", "close", "unknown"]


class PanoramaMeta(BaseModel):
    tradeDate: str | None = None
    session: Session = "unknown"
    fetchedAt: datetime | None = None
    stale: bool = False
    sources: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    unavailable: dict[str, str] = Field(default_factory=dict)


# ───────────── 全球核心指数 ─────────────


class GlobalQuote(BaseModel):
    code: str
    symbol: str
    name: str
    category: Literal["A股宽基", "海外指数", "大宗商品"]
    price: float | None = None
    change: float | None = None
    changePct: float | None = None
    amountYuan: float | None = None
    spark: list[float] = Field(default_factory=list)


class GlobalQuotesPayload(BaseModel):
    quotes: list[GlobalQuote]
    meta: PanoramaMeta


class IndexIntradayPoint(BaseModel):
    """指数 / 商品分时点：均价、成交量上游没给时为 None（不倒推）。"""

    time: str
    price: float
    avgPrice: float | None = None
    volume: float | None = None


class IndexDailyBar(BaseModel):
    """指数 / 商品日 K：成交量上游没给时为 None；涨跌幅优先取上游，否则用相邻收盘价算。"""

    date: str
    open: float
    close: float
    low: float
    high: float
    volume: float | None = None
    changePct: float | None = None


class IndexDetailPayload(BaseModel):
    code: str
    name: str
    category: Literal["A股宽基", "海外指数", "大宗商品"]
    price: float | None = None
    change: float | None = None
    changePct: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    preClose: float | None = None
    amountYuan: float | None = None
    intraday: list[IndexIntradayPoint] = Field(default_factory=list)
    dailyBars: list[IndexDailyBar] = Field(default_factory=list)
    meta: PanoramaMeta


# ───────────── 短线情绪 ─────────────


class SwingStock(BaseModel):
    code: str
    name: str
    price: float | None = None
    changePct: float
    swingPct: float
    peakOrTroughPct: float
    sector: str = ""
    reason: str = ""


class StreakLeader(BaseModel):
    code: str
    name: str
    changePct: float | None = None
    sector: str = ""
    reason: str = ""


class PremiumHistory(BaseModel):
    dates: list[str] = Field(default_factory=list)
    limitUpPremium: list[float] = Field(default_factory=list)
    streakPremium: list[float] = Field(default_factory=list)


class SentimentPayload(BaseModel):
    tradeDate: str | None = None
    upCount: int | None = None
    downCount: int | None = None
    flatCount: int | None = None
    medianChangePct: float | None = None
    limitUpTotal: int | None = None
    limitDownTotal: int | None = None
    naturalLimitUp: int | None = None
    naturalLimitDown: int | None = None
    brokenCount: int | None = None
    brokenRatePct: float | None = None
    streakCount: int | None = None
    promotionRatePct: float | None = None
    maxStreakHeight: int | None = None
    maxStreakLeader: StreakLeader | None = None
    pullbackOver10Count: int | None = None
    reboundOver10Count: int | None = None
    pullbackStocks: list[SwingStock] = Field(default_factory=list)
    reboundStocks: list[SwingStock] = Field(default_factory=list)
    yesterdayLimitUpPremiumPct: float | None = None
    yesterdayStreakPremiumPct: float | None = None
    yesterdayBrokenPremiumPct: float | None = None
    premiumHistory: PremiumHistory = Field(default_factory=PremiumHistory)
    meta: PanoramaMeta


# ───────────── 成交量能与两融 ─────────────


class VolumeSeries(BaseModel):
    key: Literal["all", "sh", "sz", "bj", "chinext", "star"]
    label: str
    todayAmountYi: float | None = None
    noonAmountYi: float | None = None
    noonDeltaYi: float | None = None
    noonDeltaPct: float | None = None
    historyDates: list[str] = Field(default_factory=list)
    historyAmountYi: list[float] = Field(default_factory=list)


class MarginSeries(BaseModel):
    key: Literal["total", "sh", "sz", "bj"]
    label: str
    tradeDate: str | None = None
    totalBalanceYi: float | None = None
    dailyChangeYi: float | None = None
    rzyeYi: float | None = None
    rqyeYi: float | None = None
    rzmreYi: float | None = None
    historyDates: list[str] = Field(default_factory=list)
    historyTotalYi: list[float] = Field(default_factory=list)
    historyChangeYi: list[float] = Field(default_factory=list)


class VolumeMarginPayload(BaseModel):
    volumes: list[VolumeSeries]
    margins: list[MarginSeries]
    meta: PanoramaMeta


# ───────────── 股指期货机构持仓 ─────────────


class FuturesRow(BaseModel):
    contractType: Literal["ALL", "IF", "IC", "IM", "IH"]
    contractName: str
    institution: str
    isKeyInstitution: bool
    longPos: int
    longChange: int
    shortPos: int
    shortChange: int
    netPos: int
    netChange: int


class FuturesPayload(BaseModel):
    rows: list[FuturesRow]
    meta: PanoramaMeta


# ───────────── 领涨板块与人气股 ─────────────


class LeadingSector(BaseModel):
    code: str
    name: str
    changePct: float | None = None
    limitUpCount: int | None = None
    netInflowYi: float | None = None
    leaderCode: str = ""
    leaderName: str = ""
    leaderChangePct: float | None = None
    leaderStreak: str = ""


class PopularStock(BaseModel):
    rank: int
    code: str
    name: str
    price: float | None = None
    changePct: float | None = None
    turnoverPct: float | None = None
    floatCapYi: float | None = None
    sealAmountWan: float | None = None
    streakText: str = ""
    sector: str = ""
    catalyst: str = ""


class LeadingPayload(BaseModel):
    leadingSectors: list[LeadingSector]
    popularStocks: list[PopularStock]
    meta: PanoramaMeta


# ───────────── 龙虎榜 ─────────────


class LhbStock(BaseModel):
    code: str
    name: str
    closePrice: float | None = None
    changePct: float | None = None
    turnoverPct: float | None = None
    floatCapYi: float | None = None
    buyWan: float
    sellWan: float
    netWan: float
    reason: str = ""


class LhbSeat(BaseModel):
    code: str
    name: str
    branchName: str
    side: Literal["buy", "sell"]
    buyWan: float
    sellWan: float
    netWan: float
    changePct: float | None = None
    reason: str = ""


class DragonTigerPayload(BaseModel):
    tradeDate: str | None = None
    stocks: list[LhbStock]
    seats: list[LhbSeat]
    meta: PanoramaMeta


# ───────────── 连板梯队 ─────────────


class LadderStock(BaseModel):
    code: str
    name: str
    streak: int
    boardLabel: str
    price: float | None = None
    changePct: float | None = None
    reason: str = ""
    sector: str = ""
    firstSealTime: str = ""
    lastSealTime: str = ""
    breakCount: int = 0
    sealAmountWan: float | None = None
    turnoverPct: float | None = None
    floatCapYi: float | None = None
    actionSector: str = ""


class LadderTier(BaseModel):
    streak: int
    label: str
    stocks: list[LadderStock]


class BrokenStreak(BaseModel):
    code: str
    name: str
    yesterdayStreak: int
    price: float | None = None
    changePct: float | None = None
    drawdownFromHighPct: float | None = None
    turnoverPct: float | None = None
    sector: str = ""
    reason: str = ""


class LadderPayload(BaseModel):
    tradeDate: str | None = None
    overallPromotionRatePct: float | None = None
    overallBrokenRatePct: float | None = None
    rate1To2Pct: float | None = None
    count1To2Text: str = ""
    rate2To3Pct: float | None = None
    count2To3Text: str = ""
    rate3To4PlusPct: float | None = None
    count3To4PlusText: str = ""
    tiers: list[LadderTier]
    brokenStreaks: list[BrokenStreak]
    meta: PanoramaMeta


# ───────────── 热点消息 ─────────────


class NewsStock(BaseModel):
    code: str
    name: str
    changePct: float | None = None


class NewsItem(BaseModel):
    id: str
    time: str
    date: str
    timestamp: int
    source: Literal["财联社", "东方财富", "同花顺", "金十数据"]
    region: Literal["domestic", "overseas"]
    impactLevel: Literal["S", "A", "B"]
    sentiment: Literal["利好", "利空", "催化", "中性"]
    title: str
    summary: str
    isHighSnr: bool
    sectors: list[str] = Field(default_factory=list)
    stocks: list[NewsStock] = Field(default_factory=list)


class NewsPayload(BaseModel):
    items: list[NewsItem]
    sourceStatus: dict[str, str]
    meta: PanoramaMeta


# ───────────── 个股详情 ─────────────


class IntradayPoint(BaseModel):
    time: str
    price: float
    avgPrice: float
    volume: float


class DailyBar(BaseModel):
    date: str
    open: float
    close: float
    low: float
    high: float
    volume: float
    changePct: float


class LhbSeatLine(BaseModel):
    branchName: str
    buyWan: float
    sellWan: float
    netWan: float


class StockDetailPayload(BaseModel):
    code: str
    name: str
    price: float | None = None
    preClose: float | None = None
    changePct: float | None = None
    turnoverPct: float | None = None
    floatCapYi: float | None = None
    sealAmountWan: float | None = None
    sealFloatRatioPct: float | None = None
    streakLabel: str = ""
    firstSealTime: str = ""
    # 所属板块：sectors = 行业在前、概念在后（兼容旧前端）；industries / concepts 是分开的两类。
    sectors: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    catalystReason: str = ""
    actionSector: str = ""
    actionSectorReason: str = ""
    actionExplain: str = ""
    intraday: list[IntradayPoint] = Field(default_factory=list)
    dailyBars: list[DailyBar] = Field(default_factory=list)
    lhbDate: str | None = None
    lhbReason: str = ""
    buySeats: list[LhbSeatLine] = Field(default_factory=list)
    sellSeats: list[LhbSeatLine] = Field(default_factory=list)
    meta: PanoramaMeta


class JygsStock(BaseModel):
    """韭研公社异动解析里的一只股票（价格 / 涨幅已换算成元 / 百分比）。"""

    code: str
    name: str
    boardLabel: str = ""
    price: float | None = None
    changePct: float | None = None
    firstSealTime: str = ""
    explain: str = ""


class JygsSector(BaseModel):
    name: str
    reason: str = ""
    stocks: list[JygsStock] = Field(default_factory=list)


class JygsActionsPayload(BaseModel):
    tradeDate: str | None = None
    totalStocks: int = 0
    sectors: list[JygsSector] = Field(default_factory=list)
    meta: PanoramaMeta


JygsState = Literal["not_configured", "ok", "expiring", "expired", "unknown"]


class JygsStatusPayload(BaseModel):
    """韭研公社登录会话状态（不含会话值）。"""

    state: JygsState
    message: str
    expiresAt: str | None = None
    daysLeft: int | None = None
