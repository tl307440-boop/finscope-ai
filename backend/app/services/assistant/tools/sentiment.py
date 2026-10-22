"""`market_sentiment`：短线情绪和热点：涨停 / 炸板 / 跌停与连板梯队、机构调研、热榜。

数据来自和「产业研究」页面同一批服务（东方财富涨停板行情、东财机构调研、东财人气榜和同花顺热榜）。
- limit_up：涨停、炸板、跌停家数、封板率、最高连板、涨停集中的行业和连板梯队；
  可以按 as_of 查某个交易日；
- limit_up_stocks：涨停股清单（连板数、所属行业、总市值与流通市值、首次封板时间、封单资金），
  可以按总市值或流通市值区间、是否只看沪深主板先筛选再取前 N 行；
- surveys：近 N 天机构调研最密集的公司（接待次数、参与机构家数）；
- hot：东财人气榜与同花顺热股、热门行业、热门概念。
只描述已经发生的盘面和关注度，不构成买卖建议。调研和热榜只有实时数据。金额按亿元给出。
"""

from datetime import date
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.providers.errors import ProviderError
from app.schemas.industry_market import HotList, HotPlateItem, HotStockItem, Section
from app.schemas.market_sentiment import LimitUpView, PoolStockItem, SurveyView
from app.services.assistant.loop_bridge import call_async
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.fmt import YI, count, plain, yi
from app.services.assistant.tools.live import (
    industry_service,
    live_only_reason,
    sentiment_service,
    upstream_reason,
)
from app.services.industry_market import IndustryMarketService
from app.services.market_sentiment import MarketSentimentService

SOURCE = "东方财富涨停板行情、机构调研、人气榜；同花顺热榜"
MAX_LADDER_ROWS = 6
MAX_INDUSTRY_ROWS = 8
MAX_NAMES = 6
HOT_PER_LIST = 10
View = Literal["limit_up", "limit_up_stocks", "surveys", "hot"]
CapBasis = Literal["total", "float"]
CAP_LABELS: dict[str, str] = {"total": "总市值", "float": "流通市值"}
# 沪深主板：沪市 600 / 601 / 603 / 605，深市 000 / 001 / 002 / 003（中小板已并入主板）。
MAIN_BOARD_PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003")


class MarketSentimentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: View = Field(
        description=(
            "limit_up 涨停炸板跌停与连板梯队；limit_up_stocks 涨停股清单；"
            "surveys 机构调研；hot 热榜（人气榜、热股、热门行业与概念）"
        )
    )
    days: int = Field(default=30, ge=1, le=90, description="surveys 回看多少天，默认 30")
    top: int = Field(
        default=15,
        ge=5,
        le=60,
        description="清单类视图返回几行，默认 15；涨停股清单一天可能有几十只，要看全可以到 60",
    )
    as_of: date | None = Field(
        default=None, description="limit_up / limit_up_stocks 可以指定交易日；其余只有实时"
    )
    min_cap: float | None = Field(
        default=None,
        ge=0,
        description="limit_up_stocks 只留市值不低于这个数的股票，单位亿元；先筛选再取前 top 行",
    )
    max_cap: float | None = Field(
        default=None,
        ge=0,
        description="limit_up_stocks 只留市值不高于这个数的股票，单位亿元；先筛选再取前 top 行",
    )
    cap_basis: CapBasis = Field(
        default="total",
        description="min_cap / max_cap 用哪个口径：total 总市值（默认）、float 流通市值",
    )
    main_board_only: bool = Field(
        default=False,
        description="limit_up_stocks 只看沪深主板（不含创业板、科创板、北交所）",
    )


class MarketSentimentTool:
    name: ClassVar[str] = "market_sentiment"
    description: ClassVar[str] = (
        "查 A 股短线情绪与市场热点：涨停 / 炸板 / 跌停家数、封板率、连板梯队、涨停集中的行业，"
        "涨停股清单（可按总市值 / 流通市值区间、沪深主板先筛选），近期机构调研最密集的公司，"
        "东财人气榜与同花顺热榜。问「最近市场在炒什么」「哪些方向最热」「资金关注谁」"
        "「今天涨停的股票里市值在某个区间的有哪些」时用。描述的是已经发生的盘面和关注度，"
        "不是预测，也不构成买卖建议。"
    )
    args_model: ClassVar[type[BaseModel]] = MarketSentimentArgs

    def __init__(
        self,
        sentiment: MarketSentimentService | None = None,
        market: IndustryMarketService | None = None,
    ) -> None:
        self._sentiment = sentiment
        self._market = market

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, MarketSentimentArgs)
        today = ctx.today_cn
        pit = effective_as_of(args.as_of, today)
        title = _TITLES[args.view]
        if args.view not in ("limit_up", "limit_up_stocks"):
            refusal = live_only_reason(args.as_of, today, title)
            if refusal is not None:
                return unavailable(self.name, title, pit, refusal, SOURCE)
        try:
            return self._run(ctx, args, pit)
        except ProviderError as error:
            return unavailable(self.name, title, pit, upstream_reason(error), SOURCE)

    def _run(self, ctx: ToolContext, args: MarketSentimentArgs, pit: date) -> ToolResult:
        sentiment = self._sentiment or sentiment_service()
        if args.view in ("limit_up", "limit_up_stocks"):
            day = pit if args.as_of is not None else None
            pool = call_async(lambda: sentiment.limit_up(day), timeout=45.0)
            if args.view == "limit_up":
                return _limit_up(self.name, pool, pit)
            return _limit_up_stocks(self.name, pool, args, pit)
        if args.view == "surveys":
            surveys = call_async(lambda: sentiment.surveys(args.days), timeout=45.0)
            return _surveys(self.name, surveys, args, pit)
        market = self._market or industry_service(ctx.engine)
        hot = call_async(lambda: market.hot(), timeout=45.0)
        return _hot(self.name, hot, pit)


_TITLES: dict[str, str] = {
    "limit_up": "涨停与情绪",
    "limit_up_stocks": "涨停股清单",
    "surveys": "机构调研",
    "hot": "市场热榜",
}

LIMIT_COLUMNS = (
    Column("item", "指标", "", "text"),
    Column("value", "数值", "", "number"),
    Column("unit", "单位", "", "text"),
    Column("note", "说明", "", "text"),
)
STOCK_COLUMNS = (
    Column("code", "代码", "", "text"),
    Column("name", "名称", "", "text"),
    Column("streak", "连板数", "板", "number"),
    Column("industry", "所属行业", "", "text"),
    Column("total_cap", "总市值", "亿元", "number"),
    Column("float_cap", "流通市值", "亿元", "number"),
    Column("first_seal", "首次封板时间", "", "text"),
    Column("change_pct", "涨跌幅", "%", "number"),
    Column("amount", "成交额", "亿元", "number"),
    Column("turnover_pct", "换手率", "%", "number"),
    Column("seal_fund", "封单资金", "亿元", "number"),
    Column("breaks", "炸板次数", "次", "number"),
)
SURVEY_COLUMNS = (
    Column("code", "代码", "", "text"),
    Column("name", "名称", "", "text"),
    Column("industry", "所属行业", "", "text"),
    Column("events", "接待次数", "次", "number"),
    Column("institutions", "参与机构", "家", "number"),
    Column("latest", "最近公告日", "", "date"),
    Column("ways", "接待方式", "", "text"),
)
HOT_COLUMNS = (
    Column("list", "榜单", "", "text"),
    Column("rank", "排名", "", "number"),
    Column("code", "代码", "", "text"),
    Column("name", "名称", "", "text"),
    Column("change_pct", "涨跌幅", "%", "number"),
    Column("heat", "热度", "", "number"),
    Column("note", "标签 / 说明", "", "text"),
)


def _status(section: Section, extra: list[str] | None = None) -> tuple[bool, str | None]:
    """(是否要标 stale, 原因)：区块不是 ok、或有任何说明，都不能当作完整数据。"""

    notes = [section.reason] if section.reason else []
    notes.extend(extra or [])
    return section.status != "ok" or bool(notes), ("；".join(notes) or None)


def _limit_up(tool: str, view: LimitUpView, pit: date) -> ToolResult:
    title = _TITLES["limit_up"]
    if view.trade_date is None:
        return unavailable(tool, title, pit, view.section.reason or "没有股池", SOURCE)
    rows: list[dict[str, str | None]] = [
        _item("涨停家数", count(view.limit_up), "家"),
        _item(
            "炸板家数", count(view.broken), "家", None if view.broken is not None else "暂不可用"
        ),
        _item(
            "跌停家数",
            count(view.limit_down),
            "家",
            None if view.limit_down is not None else "暂不可用",
        ),
        _item("封板率", plain(view.seal_rate), "%", "涨停家数 /（涨停 + 炸板）"),
        _item("最高连板数", count(view.max_streak), "板"),
    ]
    rows.extend(
        _item(f"涨停行业：{row.label}", count(row.count), "家")
        for row in view.industries[:MAX_INDUSTRY_ROWS]
    )
    for step in view.ladder[:MAX_LADDER_ROWS]:
        if step.streak < 2:
            continue
        names = "、".join(stock.name for stock in step.stocks[:MAX_NAMES])
        more = f" 等 {len(step.stocks)} 只" if len(step.stocks) > MAX_NAMES else ""
        rows.append(_item(f"{step.streak}连板", count(len(step.stocks)), "只", f"{names}{more}"))
    bad, reason = _status(view.section)
    return ToolResult(
        tool=tool,
        title=f"{title}（{view.trade_date.isoformat()}）",
        columns=LIMIT_COLUMNS,
        rows=tuple(rows),
        as_of=view.trade_date,
        source=SOURCE,
        status="stale" if bad else "ok",
        reason=reason,
    )


def _item(
    label: str, value: str | None, unit: str, note: str | None = None
) -> dict[str, str | None]:
    return {"item": label, "value": value, "unit": unit, "note": note}


def _limit_up_stocks(
    tool: str, view: LimitUpView, args: MarketSentimentArgs, pit: date
) -> ToolResult:
    title = _TITLES["limit_up_stocks"]
    if view.trade_date is None or not view.limit_up_stocks:
        return unavailable(tool, title, pit, view.section.reason or "这一天没有涨停股", SOURCE)
    heading = f"{title}（{view.trade_date.isoformat()}）"
    pool = view.limit_up_stocks
    kept, scope, missing = _screen(pool, args)
    if scope:
        # 筛选条件和数量写进标题：它会进证据，回答可以直接引用，读者也看得到表是怎么来的。
        heading = f"{heading}·{'，'.join(scope)}（涨停池 {len(pool)} 只，符合 {len(kept)} 只）"
    if not kept:
        empty = (
            f"涨停池里没有一只返回了{missing}数据，没法按市值筛选"
            if missing
            else "涨停池里没有符合这些条件的股票"
        )
        return unavailable(tool, heading, pit, empty, SOURCE)
    ordered = sorted(kept, key=lambda stock: (-(stock.streak or 1), stock.first_seal or "99"))
    bad, reason = _status(view.section)
    return ToolResult(
        tool=tool,
        title=heading,
        columns=STOCK_COLUMNS,
        rows=tuple(_stock_row(stock) for stock in ordered[: args.top]),
        as_of=view.trade_date,
        source=SOURCE,
        status="stale" if bad else "ok",
        reason=reason,
        truncated=max(0, len(ordered) - args.top),
    )


def _screen(
    stocks: list[PoolStockItem], args: MarketSentimentArgs
) -> tuple[list[PoolStockItem], list[str], str]:
    """先筛选再取前 N 行：返回（留下的股票，筛选条件说明，筛不了时缺的市值口径名）。"""

    kept = list(stocks)
    scope: list[str] = []
    if args.main_board_only:
        kept = [stock for stock in kept if stock.code.startswith(MAIN_BOARD_PREFIXES)]
        scope.append("沪深主板")
    if args.min_cap is None and args.max_cap is None:
        return kept, scope, ""
    label = CAP_LABELS[args.cap_basis]
    low = None if args.min_cap is None else Decimal(str(args.min_cap)) * YI
    high = None if args.max_cap is None else Decimal(str(args.max_cap)) * YI

    def cap_of(stock: PoolStockItem) -> Decimal | None:
        return stock.total_cap if args.cap_basis == "total" else stock.float_cap

    scope.append(f"{label}{_cap_range(args.min_cap, args.max_cap)}")
    if kept and not any(cap_of(stock) is not None for stock in kept):
        return [], scope, label
    kept = [
        stock
        for stock in kept
        if (cap := cap_of(stock)) is not None
        and (low is None or cap >= low)
        and (high is None or cap <= high)
    ]
    return kept, scope, ""


def _cap_range(low: float | None, high: float | None) -> str:
    def text(value: float) -> str:
        return plain(Decimal(str(value))) or "0"

    if low is not None and high is not None:
        return f" {text(low)}–{text(high)} 亿元"
    if low is not None:
        return f"不低于 {text(low)} 亿元"
    return f"不高于 {text(high or 0)} 亿元"


def _stock_row(stock: PoolStockItem) -> dict[str, str | None]:
    return {
        "code": stock.code,
        "name": stock.name,
        "streak": count(stock.streak),
        "industry": stock.industry,
        "total_cap": yi(stock.total_cap),
        "float_cap": yi(stock.float_cap),
        "first_seal": stock.first_seal,
        "change_pct": plain(stock.change_pct),
        "amount": yi(stock.amount),
        "turnover_pct": plain(stock.turnover_pct),
        "seal_fund": yi(stock.seal_fund),
        "breaks": count(stock.breaks),
    }


def _surveys(tool: str, view: SurveyView, args: MarketSentimentArgs, pit: date) -> ToolResult:
    title = f"{_TITLES['surveys']}（近 {args.days} 天）"
    if not view.top_stocks:
        return unavailable(
            tool, title, pit, view.section.reason or f"近 {args.days} 天没有调研记录", SOURCE
        )
    extra = (
        [] if view.industry_section.status == "ok" else [f"行业：{view.industry_section.reason}"]
    )
    bad, reason = _status(view.section, extra)
    rows = tuple(
        {
            "code": stock.code,
            "name": stock.name,
            "industry": stock.industry,
            "events": count(stock.events),
            "institutions": count(stock.institutions),
            "latest": stock.latest.isoformat(),
            "ways": "、".join(stock.ways[:3]) or None,
        }
        for stock in view.top_stocks[: args.top]
    )
    return ToolResult(
        tool=tool,
        title=title,
        columns=SURVEY_COLUMNS,
        rows=rows,
        as_of=pit,
        source=SOURCE,
        status="stale" if bad else "ok",
        reason=reason,
        truncated=max(0, len(view.top_stocks) - args.top),
    )


def _hot(tool: str, view: HotList, pit: date) -> ToolResult:
    title = _TITLES["hot"]
    rows: list[dict[str, str | None]] = []
    notes: list[str] = []
    for label, items, section in (
        ("东财人气榜", view.eastmoney, view.eastmoney_section),
        ("同花顺热股", view.ths_stocks, view.ths_section),
        ("同花顺热门行业", view.ths_industries, view.plates_section),
        ("同花顺热门概念", view.ths_concepts, view.plates_section),
    ):
        if section.status == "unavailable":
            notes.append(f"{label}：{section.reason or '暂不可用'}")
            continue
        if section.status == "stale":
            notes.append(f"{label}：{section.reason or '数据偏旧'}")
        rows.extend(_hot_row(label, item) for item in items[:HOT_PER_LIST])
    if not rows:
        return unavailable(tool, title, pit, "；".join(notes) or "榜单暂时是空的", SOURCE)
    return ToolResult(
        tool=tool,
        title=title,
        columns=HOT_COLUMNS,
        rows=tuple(rows),
        as_of=pit,
        source=SOURCE,
        status="stale" if notes else "ok",
        reason="；".join(dict.fromkeys(notes)) or None,
    )


def _hot_row(label: str, item: HotStockItem | HotPlateItem) -> dict[str, str | None]:
    if isinstance(item, HotStockItem):
        note = "、".join(item.tags[:4]) or None
        if item.note:
            note = f"{note}；{item.note}" if note else item.note
    else:
        note = item.tag
    return {
        "list": label,
        "rank": count(item.rank),
        "code": item.code,
        "name": item.name,
        "change_pct": plain(item.change_pct),
        "heat": plain(item.heat),
        "note": note,
    }
