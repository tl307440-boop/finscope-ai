"""FinScope 数据桥（规格书 §20.4）：推演角色只能通过这里读数据。

每个方法都按 as_of 截断：日线 trade_date <= as_of，SEC 事实 filed <= as_of，
公告 published_at 不晚于 as_of 当日（上海时间）结束，FRED 只用当时已发布的 vintage。
引用（Citation）带可查验的锚点：交易日、accession、公告 URL 或序列代码 + 观测日。
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.db.models.industry import Industry, IndustryMembership
from app.db.models.market import Instrument
from app.db.models.news import NewsEvent
from app.providers.vintage import VintagePoint, select_visible
from app.repositories.cn_financials import CnFinancialRepository
from app.repositories.filings import FactRecord, FilingRepository
from app.repositories.macro import MacroRepository
from app.repositories.market_bars import MarketBarRepository
from app.services.ifrs import normalize_facts
from app.services.macro_rules import MacroAssessment, MacroPoint, assess_macro
from app.services.market_temperature import (
    PriceBar,
    TemperatureResult,
    market_temperature,
    sector_strength,
)
from app.utils.identifiers import display_symbol_for
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

SHANGHAI = ZoneInfo("Asia/Shanghai")
BENCHMARK_CN = ("SSE", "000300")
MACRO_SERIES = ("GDP", "CPIAUCSL", "FEDFUNDS")
SECTOR_WINDOW = 60


@dataclass(frozen=True)
class Citation:
    source: str
    label: str
    ref: str
    observed_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "label": self.label,
            "ref": self.ref,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class Bar:
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None


@dataclass(frozen=True)
class Subject:
    instrument_id: UUID
    symbol: str
    name: str
    market: str
    cik: str | None
    industry_id: UUID | None
    industry_name: str | None


@dataclass(frozen=True)
class Announcement:
    title: str
    url: str
    source_name: str
    event_type: str
    published_at: datetime


@dataclass
class DataStore:
    """同一批推演共享的只读缓存。回测网格会在几十个 as_of 上反复读同一段日线。"""

    engine: Engine
    _bars: dict[UUID, list[Bar]] = field(default_factory=dict)
    _instruments: list[Instrument] | None = None
    _members: dict[UUID, list[UUID]] | None = None
    _industry_names: dict[UUID, str] | None = None
    _macro: dict[str, list[VintagePoint]] | None = None

    def instruments(self) -> list[Instrument]:
        if self._instruments is None:
            with Session(self.engine) as session:
                self._instruments = list(session.scalars(select(Instrument)).all())
                for row in self._instruments:
                    session.expunge(row)
        return self._instruments

    def bars(self, instrument_id: UUID) -> list[Bar]:
        cached = self._bars.get(instrument_id)
        if cached is not None:
            return cached
        repository = MarketBarRepository(self.engine)
        market = next((row.market for row in self.instruments() if row.id == instrument_id), "CN")
        rows, _truncated = repository.list_daily(
            instrument_id=instrument_id,
            provider_id=repository.provider_id_for_market(market),
            adjustment="none",
            start=None,
            end=None,
            pit=date.max,
            limit=5000,
        )
        bars = [
            Bar(
                row.trade_date,
                _price(row.open),
                _price(row.high),
                _price(row.low),
                _price(row.close),
                row.volume,
                row.amount,
            )
            for row in rows
        ]
        self._bars[instrument_id] = bars
        return bars

    def industry_members(self) -> dict[UUID, list[UUID]]:
        if self._members is None:
            members: dict[UUID, list[UUID]] = {}
            with Session(self.engine) as session:
                for link in session.scalars(select(IndustryMembership)).all():
                    members.setdefault(link.industry_id, []).append(link.instrument_id)
            self._members = members
        return self._members

    def industry_names(self) -> dict[UUID, str]:
        if self._industry_names is None:
            with Session(self.engine) as session:
                rows = session.scalars(select(Industry)).all()
                self._industry_names = {row.id: row.name for row in rows}
        return self._industry_names

    def macro_vintages(self) -> dict[str, list[VintagePoint]]:
        if self._macro is None:
            repository = MacroRepository(self.engine)
            found: dict[str, list[VintagePoint]] = {}
            for code in MACRO_SERIES:
                rows = repository.list_vintages(code, start=None, end=None)
                if rows is not None:
                    found[code] = list(rows)
            self._macro = found
        return self._macro


def _price(value: Decimal) -> Decimal:
    """库里统一存 6 位小数；去掉无意义的尾零，但不丢精度。"""

    cents = value.quantize(Decimal("0.01"))
    return cents if cents == value else value.normalize()


class FinScopeDataBridge:
    def __init__(self, store: DataStore, as_of: date) -> None:
        self.store = store
        self.as_of = as_of

    @property
    def cutoff(self) -> datetime:
        """公告可见截止：as_of 当日上海时间 24:00。"""

        end = datetime.combine(self.as_of + timedelta(days=1), time.min, tzinfo=SHANGHAI)
        return end.astimezone(UTC)

    def subject(self, symbol: str) -> Subject | None:
        wanted = symbol.strip().upper()
        names = self.store.industry_names()
        for row in self.store.instruments():
            display = display_symbol_for(row.exchange, row.symbol).upper()
            if wanted in {display, row.display_symbol.upper()}:
                industry_id = row.industry_id or self._membership(row.id)
                return Subject(
                    instrument_id=row.id,
                    symbol=display_symbol_for(row.exchange, row.symbol),
                    name=row.name_cn or row.name_en,
                    market=str(row.market),
                    cik=row.cik,
                    industry_id=industry_id,
                    industry_name=None if industry_id is None else names.get(industry_id),
                )
        return None

    def bars(self, subject: Subject, limit: int = 260) -> list[Bar]:
        visible = [
            bar for bar in self.store.bars(subject.instrument_id) if bar.trade_date <= self.as_of
        ]
        return visible[-limit:]

    def benchmark(self, subject: Subject, limit: int = 260) -> tuple[str, list[Bar]]:
        if subject.market != "CN":
            return "", []
        index_id = self._instrument_id(*BENCHMARK_CN)
        if index_id is None:
            return "", []
        visible = [bar for bar in self.store.bars(index_id) if bar.trade_date <= self.as_of]
        return "000300.SH", visible[-limit:]

    def facts(self, subject: Subject) -> list[FactRecord]:
        if subject.market == "CN":
            # A 股报表与 SEC 事实同形；分析师按 us-gaap 概念名取数，这里只改前缀做适配，
            # 数值、期间、公告日和来源链接保持上游原样。
            return [
                replace(item, concept=item.concept.replace("cn-gaap:", "us-gaap:", 1))
                for item in CnFinancialRepository(self.store.engine).list_facts(
                    subject.instrument_id, self.as_of
                )
            ]
        if not subject.cik:
            return []
        repository = FilingRepository(self.store.engine)
        if not repository.has_live_filings(subject.cik):
            return []
        # 随包样例（status=fixture）只服务于离线测试，不能混进真实推演。
        facts = repository.list_facts(subject.cik, self.as_of)
        # 20-F / 6-K 的 IFRS 概念和多币种在这里归一，分析师只认 us-gaap 短名。
        return normalize_facts([item for item in facts if item.filing_status == "edgar"])

    def announcements(self, subject: Subject, days: int = 90) -> list[Announcement]:
        start = self.cutoff - timedelta(days=days)
        with Session(self.store.engine) as session:
            rows = session.scalars(
                select(NewsEvent)
                .where(NewsEvent.published_at < self.cutoff, NewsEvent.published_at >= start)
                .order_by(NewsEvent.published_at.desc())
            ).all()
        found: list[Announcement] = []
        for row in rows:
            # 巨潮标题只在原文不含简称时才补「简称：」前缀，所以按包含匹配。
            if not subject.name or subject.name not in row.title:
                continue
            found.append(
                Announcement(
                    title=row.title,
                    url=row.canonical_url,
                    source_name=row.source_name,
                    event_type=row.event_type,
                    published_at=row.published_at,
                )
            )
        return found

    def macro(self) -> MacroAssessment:
        points: list[MacroPoint] = []
        for code, rows in self.store.macro_vintages().items():
            for row in select_visible(rows, self.as_of):
                points.append(
                    MacroPoint(
                        series_code=code,
                        observation_date=row.observation_date,
                        value=row.value,
                        realtime_start=row.realtime_start,
                    )
                )
        return assess_macro(points)

    def temperature(self, subject: Subject) -> TemperatureResult | None:
        if subject.market != "CN":
            return None
        index_id = self._instrument_id(*BENCHMARK_CN)
        if index_id is None:
            return None
        members = {
            display_symbol_for(row.exchange, row.symbol): self._prices(row.id)
            for row in self.store.instruments()
            if str(row.instrument_type) == "STOCK" and str(row.market) == "CN"
        }
        return market_temperature(self._prices(index_id), members)

    def sector(self, subject: Subject, window: int = SECTOR_WINDOW) -> TemperatureResult | None:
        """所属行业近 window 个交易日相对沪深300 的中位超额。"""

        if subject.industry_id is None:
            return None
        index_id = self._instrument_id(*BENCHMARK_CN)
        index_prices = [] if index_id is None else self._prices(index_id)[-window:]
        start = index_prices[0].trade_date if index_prices else date.min
        member_ids = self.store.industry_members().get(subject.industry_id, [])
        by_id = {row.id: row for row in self.store.instruments()}
        members = {
            display_symbol_for(by_id[item].exchange, by_id[item].symbol): [
                bar for bar in self._prices(item) if bar.trade_date >= start
            ]
            for item in member_ids
            if item in by_id
        }
        return sector_strength(index_prices, members)

    def _prices(self, instrument_id: UUID) -> list[PriceBar]:
        return [
            PriceBar(bar.trade_date, bar.close, bar.amount)
            for bar in self.store.bars(instrument_id)
            if bar.trade_date <= self.as_of
        ]

    def _instrument_id(self, exchange: str, symbol: str) -> UUID | None:
        for row in self.store.instruments():
            if row.exchange == exchange and row.symbol == symbol:
                return row.id
        return None

    def _membership(self, instrument_id: UUID) -> UUID | None:
        for industry_id, members in self.store.industry_members().items():
            if instrument_id in members:
                return industry_id
        return None
