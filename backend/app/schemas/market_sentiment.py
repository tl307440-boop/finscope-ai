"""市场情绪（涨停股池、机构调研）接口的响应结构。百分数单位 %，金额单位人民币元。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.industry_market import Section, SourceRef


class PoolStockItem(BaseModel):
    code: str
    exchange: str | None = None
    name: str
    price: Decimal | None = None
    change_pct: Decimal | None = None
    amount: Decimal | None = None
    float_cap: Decimal | None = None
    turnover_pct: Decimal | None = None
    streak: int | None = None
    first_seal: str | None = None
    last_seal: str | None = None
    seal_fund: Decimal | None = None
    breaks: int | None = None
    industry: str | None = None
    limit_days: int | None = None
    limit_times: int | None = None


class LadderStep(BaseModel):
    streak: int
    stocks: list[PoolStockItem]


class CountItem(BaseModel):
    label: str
    count: int


class LimitUpView(BaseModel):
    section: Section
    trade_date: date | None = None
    limit_up: int
    broken: int | None = None
    limit_down: int | None = None
    seal_rate: Decimal | None = None
    max_streak: int
    ladder: list[LadderStep]
    industries: list[CountItem]
    seal_times: list[CountItem]
    limit_up_stocks: list[PoolStockItem]
    broken_stocks: list[PoolStockItem]
    limit_down_stocks: list[PoolStockItem]
    rules: list[str]
    sources: list[SourceRef]


class SurveyStockItem(BaseModel):
    code: str
    name: str
    events: int
    institutions: int
    latest: date
    industry: str | None = None
    ways: list[str]


class SurveyEventItem(BaseModel):
    code: str
    name: str
    notice_date: date
    receive_date: date | None = None
    institutions: int
    receive_way: str | None = None


class SurveyView(BaseModel):
    section: Section
    days: int
    since: date
    events: int
    stocks: int
    institutions: int
    daily: list[CountItem]
    ways: list[CountItem]
    industries: list[CountItem]
    industry_section: Section
    top_stocks: list[SurveyStockItem]
    recent: list[SurveyEventItem]
    rules: list[str]
    sources: list[SourceRef]
