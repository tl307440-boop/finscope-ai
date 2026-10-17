"""总览驾驶舱（/api/v1/dashboard/*）的响应结构。

百分数字段单位 %，金额单位人民币元（外盘资产为各自报价单位），利差与利率变化单位 bp。
每个模块自带 Section：某个上游失败只让该模块 unavailable / stale，不影响其他模块。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.industry_market import Section, SourceRef


class FactorItem(BaseModel):
    code: str
    label: str
    weight: Decimal
    score: Decimal
    raw: str
    formula: str
    value: Decimal | None = None
    contribution: Decimal | None = None


class ScoreBlock(BaseModel):
    method: str
    score: Decimal | None = None
    label: str
    coverage: Decimal
    factors: list[FactorItem]
    missing: list[str]
    previous: Decimal | None = None
    previous_date: date | None = None
    change: Decimal | None = None


class SeriesPoint(BaseModel):
    day: date
    value: Decimal


class IndexQuote(BaseModel):
    symbol: str
    name: str
    last: Decimal | None = None
    prev_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    amplitude_pct: Decimal | None = None
    amount: Decimal | None = None
    quote_time: str | None = None


class BucketItem(BaseModel):
    bucket: int
    label: str
    count: int


class BreadthItem(BaseModel):
    up: int
    down: int
    flat: int
    total: int
    strong: int
    weak: int
    limit_up: int
    limit_down: int
    buckets: list[BucketItem]


class LimitStats(BaseModel):
    limit_up: int
    broken: int | None = None
    limit_down: int | None = None
    seal_rate: Decimal | None = None
    max_streak: int


class AmountPoint(BaseModel):
    day: date
    amount: Decimal
    ma20: Decimal | None = None


class MarketOverview(BaseModel):
    trade_date: date | None = None
    temperature: ScoreBlock
    quotes_section: Section
    breadth_section: Section
    limits_section: Section
    history_section: Section
    indices: list[IndexQuote]
    breadth: BreadthItem | None = None
    limits: LimitStats | None = None
    market_amount: Decimal | None = None
    amount_base: Decimal | None = None
    amount_vs_base_pct: Decimal | None = None
    amounts: list[AmountPoint]
    rules: list[str]
    sources: list[SourceRef]


class BarItem(BaseModel):
    day: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    amount: Decimal | None = None
    ma5: Decimal | None = None
    ma10: Decimal | None = None
    ma20: Decimal | None = None


class MinuteItem(BaseModel):
    clock: str
    price: Decimal
    amount: Decimal


class IndexBars(BaseModel):
    symbol: str
    name: str
    period: str
    section: Section
    prev_close: Decimal | None = None
    amount_vs_5d_pct: Decimal | None = None
    bars: list[BarItem]
    minutes: list[MinuteItem]
    sources: list[SourceRef]


class MacroTile(BaseModel):
    code: str
    label: str
    value: Decimal | None = None
    unit: str
    change: Decimal | None = None
    change_unit: str
    change_label: str
    period: str | None = None


class GlobalAsset(BaseModel):
    code: str
    name: str
    last: Decimal | None = None
    change_pct: Decimal | None = None
    quote_time: str | None = None


class MacroOverview(BaseModel):
    liquidity: ScoreBlock
    growth: ScoreBlock
    regime: str
    cpi_yoy: Decimal | None = None
    inflation_momentum: Decimal | None = None
    us_section: Section
    china_section: Section
    assets_section: Section
    tiles: list[MacroTile]
    assets: list[GlobalAsset]
    net_liquidity: list[SeriesPoint]
    dollar: list[SeriesPoint]
    real_yield: list[SeriesPoint]
    shibor_on: list[SeriesPoint]
    cn_us_spread: list[SeriesPoint]
    rules: list[str]
    sources: list[SourceRef]


class HotBoardItem(BaseModel):
    code: str
    name: str
    kind: str
    heat: Decimal | None = None
    change_pct: Decimal | None = None
    limit_ups: int
    heat_score: Decimal
    change_score: Decimal
    limit_score: Decimal
    index: int
    hot: bool
    tag: str | None = None
    hot_tag: str | None = None
    leader_code: str | None = None
    leader_name: str | None = None
    leader_change_pct: Decimal | None = None


class GainBoardItem(BaseModel):
    code: str
    name: str
    kind: str
    members: int
    change_pct: Decimal | None = None
    amount: Decimal | None = None
    leader_code: str | None = None
    leader_name: str | None = None
    leader_change_pct: Decimal | None = None


class BoardsOverview(BaseModel):
    trade_date: date | None = None
    heat_section: Section
    gain_section: Section
    hot_count: int | None = None
    previous_hot_count: int | None = None
    hot_count_change: int | None = None
    previous_date: date | None = None
    boards: list[HotBoardItem]
    gainers: list[GainBoardItem]
    rules: list[str]
    sources: list[SourceRef]


class HotStockItem(BaseModel):
    code: str
    name: str
    rank: int | None = None
    last: Decimal | None = None
    change_pct: Decimal | None = None
    pe_ttm: Decimal | None = None
    amount: Decimal | None = None
    turnover_pct: Decimal | None = None
    tag: str | None = None
    concepts: list[str] = []


class StocksOverview(BaseModel):
    hot_section: Section
    gain_section: Section
    hot: list[HotStockItem]
    gainers: list[HotStockItem]
    rules: list[str]
    sources: list[SourceRef]


class AnnouncementItem(BaseModel):
    id: str
    code: str
    name: str
    title: str
    url: str
    published_at: datetime
    kind: str
    level: int
    tone: str
    heat_level: int | None = None
    heat_reason: str | None = None


class CountItem(BaseModel):
    name: str
    count: int


class AnnouncementsOverview(BaseModel):
    section: Section
    window_start: date
    window_end: date
    scanned: int
    important: list[AnnouncementItem]
    hot: list[AnnouncementItem]
    counts: list[CountItem]
    rules: list[str]
    sources: list[SourceRef]


class SignalItem(BaseModel):
    tone: str
    text: str


class WatchRow(BaseModel):
    code: str
    name: str
    last: Decimal | None = None
    change_pct: Decimal | None = None
    change_5d_pct: Decimal | None = None
    state: str | None = None
    closes: list[Decimal]
    signals: list[SignalItem]


class WatchGroupRef(BaseModel):
    id: UUID
    name: str
    count: int


class WatchOverview(BaseModel):
    group_id: UUID | None = None
    group_name: str | None = None
    groups: list[WatchGroupRef]
    quote_section: Section
    spark_section: Section
    total: int
    strong: int
    neutral: int
    weak: int
    unknown: int
    rows: list[WatchRow]
    rules: list[str]
    sources: list[SourceRef]
