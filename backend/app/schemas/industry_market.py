"""产业研究 · 板块行情接口的响应结构。百分数字段单位都是 %，金额单位都是人民币元。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

SectionStatus = Literal["ok", "stale", "unavailable"]


class SourceRef(BaseModel):
    name: str
    url: str
    fetched_at: datetime | None = None


class Section(BaseModel):
    status: SectionStatus
    reason: str | None = None
    fetched_at: datetime | None = None
    origin: Literal["live", "stored"] = "live"
    data_date: date | None = None


class BoardQuoteItem(BaseModel):
    code: str
    name: str
    kind: str
    level: int
    last: Decimal | None = None
    change_pct: Decimal | None = None
    change_5d_pct: Decimal | None = None
    change_10d_pct: Decimal | None = None
    change_60d_pct: Decimal | None = None
    change_ytd_pct: Decimal | None = None
    amount: Decimal | None = None
    turnover_pct: Decimal | None = None
    pe_ttm: Decimal | None = None
    market_cap: Decimal | None = None
    main_net_inflow: Decimal | None = None
    main_net_inflow_5d: Decimal | None = None
    advancers: int | None = None
    decliners: int | None = None
    unchanged: int | None = None
    leader_code: str | None = None
    leader_name: str | None = None
    leader_change_pct: Decimal | None = None


class BreadthItem(BaseModel):
    boards: int
    boards_up: int
    boards_down: int
    stocks_up: int
    stocks_down: int
    stocks_flat: int
    total_main_net_inflow: Decimal


class BoardCatalogItem(BaseModel):
    code: str
    name: str
    kind: str
    level: int


class BoardCatalog(BaseModel):
    boards: list[BoardCatalogItem]
    counts: dict[str, int]
    sources: list[SourceRef]


class BoardOverview(BaseModel):
    level: str
    section: Section
    breadth: BreadthItem | None = None
    boards: list[BoardQuoteItem]
    sources: list[SourceRef]
    rules: list[str]


class BarItem(BaseModel):
    trade_date: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    amount: Decimal
    ma20: Decimal | None = None
    ma60: Decimal | None = None


class CurveItem(BaseModel):
    trade_date: date
    board_index: Decimal
    benchmark_index: Decimal
    excess_pct: Decimal


class WindowReturns(BaseModel):
    d5: Decimal | None = None
    d20: Decimal | None = None
    d60: Decimal | None = None
    d120: Decimal | None = None
    d250: Decimal | None = None
    excess_d20: Decimal | None = None
    excess_d60: Decimal | None = None
    excess_d250: Decimal | None = None


class TrendBlock(BaseModel):
    section: Section
    bars: list[BarItem]
    relative: list[CurveItem]
    returns: WindowReturns
    benchmark: str


class MemberItem(BaseModel):
    code: str
    exchange: str | None = None
    name: str
    last: Decimal | None = None
    change_pct: Decimal | None = None
    change_5d_pct: Decimal | None = None
    change_10d_pct: Decimal | None = None
    change_60d_pct: Decimal | None = None
    change_ytd_pct: Decimal | None = None
    amount: Decimal | None = None
    turnover_pct: Decimal | None = None
    pe_dynamic: Decimal | None = None
    pb: Decimal | None = None
    market_cap: Decimal | None = None
    main_net_inflow: Decimal | None = None
    sub_industry: str | None = None


class BucketItem(BaseModel):
    label: str
    count: int
    tone: str


class SubIndustryItem(BaseModel):
    name: str
    code: str | None = None
    members: int
    market_cap: Decimal
    weighted_change_pct: Decimal | None = None
    main_net_inflow: Decimal


class MembersBlock(BaseModel):
    section: Section
    total: int
    members: list[MemberItem]
    distribution: list[BucketItem]
    suspended: int
    sub_industries: list[SubIndustryItem]


class PercentileItem(BaseModel):
    current: Decimal
    percentile: Decimal
    minimum: Decimal
    p20: Decimal
    median: Decimal
    p80: Decimal
    maximum: Decimal
    samples: int
    start: date
    end: date


class ValuationPointItem(BaseModel):
    trade_date: date
    pe_ttm: Decimal | None = None
    pb_mrq: Decimal | None = None


class ValuationBlock(BaseModel):
    section: Section
    pe_ttm: PercentileItem | None = None
    pb_mrq: PercentileItem | None = None
    loss_members: int | None = None
    members: int | None = None
    series: list[ValuationPointItem]
    window_years: int


class BoardDetail(BaseModel):
    board: BoardQuoteItem
    quote_section: Section
    trend: TrendBlock
    members: MembersBlock
    valuation: ValuationBlock
    sources: list[SourceRef]
    rules: list[str]
    analytics_version: str


class ReportItem(BaseModel):
    info_code: str
    title: str
    org_name: str
    published_on: date
    rating: str | None = None
    researcher: str | None = None
    url: str


class ReportList(BaseModel):
    section: Section
    reports: list[ReportItem]
    sources: list[SourceRef]


class HotStockItem(BaseModel):
    rank: int
    code: str
    name: str
    exchange: str | None = None
    change_pct: Decimal | None = None
    last: Decimal | None = None
    heat: Decimal | None = None
    rank_change: int | None = None
    industry: str | None = None
    tags: list[str]
    note: str | None = None


class HotPlateItem(BaseModel):
    rank: int
    code: str
    name: str
    change_pct: Decimal | None = None
    heat: Decimal | None = None
    rank_change: int | None = None
    tag: str | None = None


class LinkedThesis(BaseModel):
    id: str
    title: str
    hypothesis: str
    status: str
    rationale: str
    support: int
    challenge: int


class LinkedIndustry(BaseModel):
    id: str
    name: str
    match: str
    theses: list[LinkedThesis]


class BoardTheses(BaseModel):
    board_code: str
    board_name: str
    industries: list[LinkedIndustry]
    rules: list[str]


class SnapshotDates(BaseModel):
    dates: list[date]


class RotationPoint(BaseModel):
    trade_date: date
    rs_ratio: Decimal
    rs_momentum: Decimal


class RotationSeries(BaseModel):
    code: str
    name: str
    quadrant: str
    change_pct: Decimal | None = None
    tail: list[RotationPoint]


class RotationView(BaseModel):
    level: str
    section: Section
    benchmark: str
    window: int
    step: int
    series: list[RotationSeries]
    rules: list[str]
    sources: list[SourceRef]


class HotList(BaseModel):
    eastmoney: list[HotStockItem]
    eastmoney_section: Section
    ths_stocks: list[HotStockItem]
    ths_section: Section
    ths_industries: list[HotPlateItem]
    ths_concepts: list[HotPlateItem]
    plates_section: Section
    sources: list[SourceRef]
