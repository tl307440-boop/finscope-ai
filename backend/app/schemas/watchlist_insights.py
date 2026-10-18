"""自选股看板与批量导入的响应结构。百分数单位 %，金额单位人民币元。"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.schemas.industry_market import Section, SourceRef


class WatchStock(BaseModel):
    instrument_id: UUID
    display_symbol: str
    code: str
    name: str
    exchange: str
    origin: str
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
    industry: str | None = None
    board_code: str | None = None
    limit_status: str | None = None
    streak: int | None = None
    surveys: int = 0
    surveyed_institutions: int = 0
    ths_hot_rank: int | None = None
    em_hot_rank: int | None = None


class WatchOther(BaseModel):
    """非沪深京 A 股个股的自选标的（板块、指数、美股、基金、国债收益率……）。

    只有价格与涨跌：没有市值、行业、资金与涨停信号，也不进入 A 股汇总。
    """

    item_id: UUID | None = None
    instrument_id: UUID | None = None
    display_symbol: str
    secid: str | None = None
    code: str
    name: str
    kind: str
    kind_label: str
    last: Decimal | None = None
    change_pct: Decimal | None = None
    change_5d_pct: Decimal | None = None
    change_60d_pct: Decimal | None = None
    change_ytd_pct: Decimal | None = None
    amount: Decimal | None = None


class ExposureItem(BaseModel):
    name: str
    board_code: str | None = None
    count: int
    market_cap: Decimal
    weight_pct: Decimal | None = None
    weighted_change_pct: Decimal | None = None


class WatchSummary(BaseModel):
    count: int
    quoted: int
    up: int
    down: int
    flat: int
    average_change_pct: Decimal | None = None
    weighted_change_pct: Decimal | None = None
    total_main_net_inflow: Decimal | None = None
    limit_up: int
    surveyed: int
    hot: int


class WatchlistInsights(BaseModel):
    group_id: UUID
    group_name: str
    quote_section: Section
    signal_section: Section
    summary: WatchSummary
    stocks: list[WatchStock]
    industries: list[ExposureItem]
    others: list[WatchOther] = []
    others_section: Section = Section(status="ok")
    rules: list[str]
    sources: list[SourceRef]


class ImportResult(BaseModel):
    added: list[str]
    existing: list[str]
    # added 里本系统没有收录、按东财代码单独保存的那部分（板块、指数、美股、基金……）。
    external: list[str] = []
    # 已保存，但东财没有返回名称与行情的标的（可能已退市或代码有误）。
    unnamed: list[str] = []
    invalid: list[str]
