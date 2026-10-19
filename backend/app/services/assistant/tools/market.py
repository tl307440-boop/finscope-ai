"""证券与行情工具（规格 §7）。

包含 `instrument_search`、`price_bars`、`financial_metrics`、`board_strength`。

`price_bars` 与 `financial_metrics` 只接受本回合 `instrument_search` 返回过的证券 id，
防止模型编 id。时点按证券所属市场换算（A 股上海日历日、美股纽约日历日）。
"""

from datetime import date, timedelta
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.repositories.financial_metrics import FinancialMetricRepository
from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.repositories.macro import decimal_text
from app.repositories.market_bars import MarketBarRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.market_dashboard import sectors_for
from app.utils.identifiers import parse_search_key

MAX_CANDIDATES = 8
MAX_BARS = 250
MAX_METRIC_ROWS = 48
MAX_BAR_SPAN_DAYS = 3 * 366
DEFAULT_BAR_DAYS = 365
BAR_STALE_DAYS = 10
_NOT_SEARCHED = "instrument_id 必须来自本轮 instrument_search 返回的结果，请先搜索证券"


class InstrumentSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        description="证券名称、6 位 A 股代码或美股代码，1–40 字", min_length=1, max_length=40
    )
    market: Literal["CN", "US"] | None = Field(default=None, description="限定市场；不确定就不填")


class InstrumentSearchTool:
    name: ClassVar[str] = "instrument_search"
    description: ClassVar[str] = (
        "按名称或代码搜索本系统已收录的证券（A 股与部分美股），最多 8 个候选。"
        "查行情或财务前必须先用它拿到 instrument_id。没搜到说明库里没有这只证券，不要猜。"
    )
    args_model: ClassVar[type[BaseModel]] = InstrumentSearchArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, InstrumentSearchArgs)
        found = InstrumentRepository(ctx.engine).search(
            parse_search_key(args.query), market=args.market, limit=MAX_CANDIDATES
        )
        today = ctx.today_cn
        if not found:
            return unavailable(
                self.name,
                f"搜索证券：{args.query}",
                today,
                "本系统没有收录匹配的证券",
                "本系统证券库",
            )
        return ToolResult(
            tool=self.name,
            title=f"搜索证券：{args.query}",
            columns=(
                Column("instrument_id", "证券 id", "", "text", visible=False),
                Column("symbol", "代码", "", "text"),
                Column("name", "名称", "", "text"),
                Column("market", "市场", "", "text"),
                Column("type", "类型", "", "text"),
            ),
            rows=tuple(
                {
                    "instrument_id": str(item.id),
                    "symbol": item.display_symbol,
                    "name": item.name_cn or item.name_en,
                    "market": item.market,
                    "type": item.instrument_type,
                }
                for item in found
            ),
            as_of=today,
            source="本系统证券库",
        )


def _market_today(ctx: ToolContext, instrument: InstrumentRecord) -> date:
    return ctx.today_cn if instrument.market == "CN" else ctx.today_us


def _resolve(ctx: ToolContext, raw_id: str) -> InstrumentRecord | str:
    """校验 id 来自本回合的搜索，并取出证券；不合格返回原因文字。"""

    try:
        instrument_id = UUID(raw_id)
    except ValueError:
        return "instrument_id 不是合法的 id，请用 instrument_search 返回的值"
    if instrument_id not in ctx.searched:
        return _NOT_SEARCHED
    instrument = InstrumentRepository(ctx.engine).get(instrument_id)
    return instrument if instrument is not None else "没有这只证券"


class PriceBarsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(
        description="instrument_search 返回的证券 id", min_length=1, max_length=64
    )
    start: date | None = Field(default=None, description="起点 YYYY-MM-DD，默认约一年前")
    end: date | None = Field(default=None, description="终点 YYYY-MM-DD，默认今天")
    adjustment: Literal["none", "qfq"] = Field(
        default="none", description="none 不复权；qfq 前复权"
    )
    as_of: date | None = Field(default=None, description="按这个日期当时已有的日线查，默认今天")

    @model_validator(mode="after")
    def _check_window(self) -> "PriceBarsArgs":
        if self.start and self.end:
            if self.start > self.end:
                raise ValueError("start 不能晚于 end")
            if (self.end - self.start).days > MAX_BAR_SPAN_DAYS:
                raise ValueError("start 与 end 的跨度不能超过 3 年")
        return self


class PriceBarsTool:
    name: ClassVar[str] = "price_bars"
    description: ClassVar[str] = (
        "查一只证券的日线（开高低收、成交量），最多最近 250 个交易日，区间不超过 3 年。"
        "涨跌幅请用 calc 的 pct_change 引用首尾收盘价计算。"
    )
    args_model: ClassVar[type[BaseModel]] = PriceBarsArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, PriceBarsArgs)
        found = _resolve(ctx, args.instrument_id)
        if isinstance(found, str):
            return unavailable(self.name, "日线", ctx.today_cn, found)
        title = f"{found.name_cn or found.name_en}（{found.display_symbol}）日线"
        pit = effective_as_of(args.as_of, _market_today(ctx, found))
        end = min(args.end, pit) if args.end else pit
        start = args.start or end - timedelta(days=DEFAULT_BAR_DAYS)
        repository = MarketBarRepository(ctx.engine)
        rows, truncated = repository.list_daily(
            instrument_id=found.id,
            provider_id=repository.provider_id_for_market(found.market),
            adjustment=args.adjustment,
            start=start,
            end=end,
            pit=pit,
            limit=MAX_BARS,
        )
        source = "东方财富 / AkShare 日线" if found.market == "CN" else "Tiingo 日线"
        if not rows:
            return unavailable(
                self.name,
                title,
                pit,
                f"{start.isoformat()} 至 {end.isoformat()} 没有已入库的日线",
                source,
            )
        latest = rows[-1].trade_date
        stale = (pit - latest).days > BAR_STALE_DAYS
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("trade_date", "交易日", "", "date"),
                Column("open", "开盘", found.currency, "number"),
                Column("high", "最高", found.currency, "number"),
                Column("low", "最低", found.currency, "number"),
                Column("close", "收盘", found.currency, "number"),
                Column("volume", "成交量", "", "number"),
            ),
            rows=tuple(
                {
                    "trade_date": bar.trade_date.isoformat(),
                    "open": decimal_text(bar.open),
                    "high": decimal_text(bar.high),
                    "low": decimal_text(bar.low),
                    "close": decimal_text(bar.close),
                    "volume": decimal_text(bar.volume),
                }
                for bar in rows
            ),
            as_of=pit,
            source=source,
            status="stale" if stale else "ok",
            reason=(
                f"最新日线是 {latest.isoformat()}，"
                f"距 {pit.isoformat()} 已超过 {BAR_STALE_DAYS} 天，可能尚未更新"
                if stale
                else None
            ),
            truncated=1 if truncated else 0,
            primary="close",
        )


class FinancialMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(
        description="instrument_search 返回的证券 id", min_length=1, max_length=64
    )
    metric_codes: list[str] = Field(
        default_factory=list,
        description="只要这些指标代码（最多 6 个）；不填返回全部已有指标的最新一期",
        max_length=6,
    )
    as_of: date | None = Field(
        default=None, description="按这个日期当时已公布的财务数据查，默认今天"
    )

    @field_validator("metric_codes")
    @classmethod
    def _strip(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class FinancialMetricsTool:
    name: ClassVar[str] = "financial_metrics"
    description: ClassVar[str] = (
        "查一只证券每个财务指标的最新一期（毛利率、负债率、同比、PE、PB 等本系统已算好的指标）。"
        "只含当时已经公布的报告期。"
    )
    args_model: ClassVar[type[BaseModel]] = FinancialMetricsArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, FinancialMetricsArgs)
        found = _resolve(ctx, args.instrument_id)
        if isinstance(found, str):
            return unavailable(self.name, "财务指标", ctx.today_cn, found)
        title = f"{found.name_cn or found.name_en}（{found.display_symbol}）财务指标"
        pit = effective_as_of(args.as_of, _market_today(ctx, found))
        metrics = FinancialMetricRepository(ctx.engine).latest_for_instrument(found.id, pit)
        wanted = set(args.metric_codes)
        chosen = [item for item in metrics if not wanted or item.metric_code in wanted]
        if not chosen:
            return unavailable(
                self.name,
                title,
                pit,
                "没有当时已公布的财务指标（发布时间未知的不计入）",
                "本系统财务指标",
            )
        chosen.sort(key=lambda item: item.metric_code)
        missing = sorted(wanted - {item.metric_code for item in chosen})
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("metric_code", "指标", "", "text"),
                Column("period_end", "报告期", "", "date"),
                Column("period_type", "期类型", "", "text"),
                Column("value", "数值", "", "number"),
                Column("unit", "单位", "", "text"),
                Column("quality", "质量标记", "", "text"),
            ),
            rows=tuple(
                {
                    "metric_code": item.metric_code,
                    "period_end": item.period_end.isoformat(),
                    "period_type": item.period_type,
                    "value": None if item.value is None else decimal_text(item.value),
                    "unit": item.unit,
                    "quality": item.quality_flag,
                }
                for item in chosen[:MAX_METRIC_ROWS]
            ),
            as_of=pit,
            source="本系统财务指标（来自已入库财报）",
            reason=f"没有这些指标：{'、'.join(missing)}" if missing else None,
            truncated=max(0, len(chosen) - MAX_METRIC_ROWS),
        )


class BoardStrengthArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = Field(default=None, description="按这个日期当时已有的日线算，默认今天")


class BoardStrengthTool:
    name: ClassVar[str] = "board_strength"
    description: ClassVar[str] = (
        "申万一级行业相对沪深 300 的强弱（描述性，不是收益预测）：每个行业的强弱标签与得分。"
    )
    args_model: ClassVar[type[BaseModel]] = BoardStrengthArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, BoardStrengthArgs)
        pit = effective_as_of(args.as_of, ctx.today_cn)
        sectors = sectors_for(ctx.engine, pit)
        source = "本系统日线（成分以当前分类计算，有幸存者偏差）"
        usable = [(name, item) for name, item in sectors if item.status == "ok"]
        if not usable:
            return unavailable(self.name, "行业强弱", pit, "各行业的日线样本不足，无法判断", source)
        return ToolResult(
            tool=self.name,
            title="申万一级行业强弱",
            columns=(
                Column("name", "行业", "", "text"),
                Column("label", "强弱", "", "text"),
                Column("score", "得分", "", "number"),
                Column("coverage", "覆盖", "", "text"),
            ),
            rows=tuple(
                {
                    "name": name,
                    "label": item.label,
                    "score": None if item.score is None else decimal_text(item.score),
                    "coverage": item.coverage,
                }
                for name, item in usable
            ),
            as_of=pit,
            source=source,
        )
