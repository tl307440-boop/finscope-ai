"""东方财富行业 / 概念板块工具 `industry_board`（规格 §7）。

数据来自本系统每天入库的板块快照（`board_daily_snapshots`）。问的是现在、而快照还停在
更早的收盘日时，用新浪板块榜补当日涨跌幅；新浪给了领涨股名称才换领涨股。新浪没给涨跌幅
时不盖掉快照。5 日 / 60 日、市盈率、主力、涨跌家数新浪没有，仍用快照，并在说明里写清。
回看过去某一天时不拿今天的新浪去填。
按名称模糊匹配，一次可以查几个关键词。金额按亿元给出（元 ÷ 10^8，Decimal 计算）。
"""

from datetime import date
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.providers import sina_market as sina
from app.providers.errors import ProviderError
from app.repositories.board_market import BoardMarketRepository, StoredSnapshot
from app.services.assistant.loop_bridge import call_async
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.fmt import count, plain, yi
from app.services.board_gap import fetch_sina_boards, index_sina, needs_live_change
from app.services.market_cache import PacedGateway, shared_gateway

MAX_QUERIES = 4
PER_QUERY = 4
MAX_ROWS = 12
STALE_DAYS = 10  # 与日线一致：长假（国庆约 8 天不开市）不算过期
SOURCE = "东方财富行业 / 概念板块（本系统每日快照）；快照落后时当日涨跌用新浪板块榜"
_KIND_NAMES = {"industry": "行业", "concept": "概念"}


class IndustryBoardArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(
        min_length=1,
        max_length=MAX_QUERIES,
        description='板块名称关键词，1–4 个，如 ["半导体", "芯片", "光刻机"]',
    )
    kind: Literal["industry", "concept"] | None = Field(
        default=None, description="industry 行业板块；concept 概念板块；不填两种都查"
    )
    as_of: date | None = Field(default=None, description="按这个日期当时的快照查，默认今天")

    @field_validator("queries")
    @classmethod
    def _strip(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip()[:20] for item in value if item.strip()))
        if not cleaned:
            raise ValueError("至少给一个板块名称关键词")
        return cleaned


class IndustryBoardTool:
    name: ClassVar[str] = "industry_board"
    description: ClassVar[str] = (
        "按名称查东方财富行业 / 概念板块的最新快照（如半导体、芯片、光伏设备、白酒）："
        "当日、5 日、60 日、年初至今涨跌幅，市盈率 TTM，换手率，主力净流入，涨跌家数，领涨股。"
        "快照早于最近收盘日时，名称对得上且新浪有涨跌幅的板块，用新浪补当日涨跌幅；"
        "新浪给了领涨股名称才换领涨股。「涨跌来源」列写明是哪一家；其余列仍是快照。"
        "一次可查 1–4 个关键词。已经发生的行情，不是预测。"
    )
    args_model: ClassVar[type[BaseModel]] = IndustryBoardArgs

    def __init__(self, gateway: PacedGateway | None = None) -> None:
        self._gateway = gateway

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, IndustryBoardArgs)
        pit = effective_as_of(args.as_of, ctx.today_cn)
        title = f"板块行情：{'、'.join(args.queries)}"
        kinds = [args.kind] if args.kind else ["industry", "concept"]
        repository = BoardMarketRepository(ctx.engine)
        found: dict[str, StoredSnapshot] = {}
        for query in args.queries:
            for item in repository.search_latest(query, pit, kinds, PER_QUERY):
                found.setdefault(item.node.code, item)
        if not found:
            return unavailable(
                self.name,
                title,
                pit,
                f"没有名称包含「{'」「'.join(args.queries)}」的板块快照；"
                "可以换个说法再查，例如「半导体」「芯片」「消费电子」",
                SOURCE,
            )
        items = list(found.values())[:MAX_ROWS]
        latest = max(item.trade_date for item in items)
        historical = args.as_of is not None and args.as_of < ctx.today_cn
        fill = needs_live_change(latest, ctx.now, historical=historical)
        by_name: dict[str, sina.SinaBoard] = {}
        live_note: str | None = None
        if fill:
            try:
                live = call_async(
                    lambda: fetch_sina_boards(self._gateway or shared_gateway()), timeout=20.0
                )
                by_name = index_sina(live)
            except ProviderError as error:
                live_note = (
                    f"新浪板块榜也取不到（{error.message}），"
                    f"当日涨跌仍是 {latest.isoformat()} 的快照"
                )
        rows = tuple(_row(item, by_name.get(item.node.name.strip())) for item in items)
        matched = sum(1 for row in rows if row["change_source"] == "新浪实时")
        notes = _notes(latest, pit, fill, matched, live_note)
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=rows,
            as_of=latest,
            source=SOURCE,
            status="stale" if notes else "ok",
            reason="；".join(notes) or None,
            truncated=max(0, len(found) - MAX_ROWS),
        )


COLUMNS = (
    Column("name", "板块", "", "text"),
    Column("kind", "类型", "", "text"),
    Column("trade_date", "日期", "", "date"),
    Column("change_pct", "当日涨跌幅", "%", "number"),
    Column("change_source", "涨跌来源", "", "text"),
    Column("change_5d_pct", "5日涨跌幅", "%", "number"),
    Column("change_60d_pct", "60日涨跌幅", "%", "number"),
    Column("change_ytd_pct", "年初至今涨跌幅", "%", "number"),
    Column("pe_ttm", "市盈率TTM", "倍", "number"),
    Column("turnover_pct", "换手率", "%", "number"),
    Column("main_net_inflow", "主力净流入", "亿元", "number"),
    Column("main_net_inflow_5d", "5日主力净流入", "亿元", "number"),
    Column("advancers", "上涨家数", "家", "number"),
    Column("decliners", "下跌家数", "家", "number"),
    Column("leader_name", "领涨股", "", "text"),
    Column("leader_change_pct", "领涨股涨跌幅", "%", "number"),
)


def _notes(latest: date, pit: date, fill: bool, matched: int, live_note: str | None) -> list[str]:
    notes: list[str] = []
    if (pit - latest).days > STALE_DAYS:
        notes.append(
            f"最新的板块快照是 {latest.isoformat()}，距 {pit.isoformat()} "
            f"已超过 {STALE_DAYS} 天，可能尚未更新"
        )
    if live_note:
        notes.append(live_note)
    elif fill and matched:
        notes.append(
            f"当日涨跌幅里，{matched} 个板块已用新浪实时补上；"
            "领涨股只在新浪给出名称时换成新浪，没给的仍是快照；"
            "5 日、60 日、年初至今、市盈率、主力净流入和涨跌家数，"
            f"仍是 {latest.isoformat()} 的收盘快照"
        )
    elif fill:
        notes.append(
            f"新浪板块榜里没有名称相同的板块，当日涨跌仍是 {latest.isoformat()} 的收盘快照"
        )
    return notes


def _row(item: StoredSnapshot, live: sina.SinaBoard | None = None) -> dict[str, str | None]:
    quote = item.quote
    change = quote.change_pct
    source = "收盘快照"
    leader_name = quote.leader_name
    leader_change = quote.leader_change_pct
    # 新浪没给涨跌幅时不盖掉快照。给了涨跌但没给领涨股名字时，领涨股仍留快照，
    # 避免把快照里另一只股票的涨跌幅安到空名字上。
    if live is not None and live.change_pct is not None:
        change = live.change_pct
        source = "新浪实时"
        if live.leader_name:
            leader_name = live.leader_name
            leader_change = live.leader_change_pct
    return {
        "name": item.node.name,
        "kind": _KIND_NAMES.get(item.node.kind, item.node.kind),
        "trade_date": item.trade_date.isoformat(),
        "change_pct": plain(change),
        "change_source": source,
        "change_5d_pct": plain(quote.change_5d_pct),
        "change_60d_pct": plain(quote.change_60d_pct),
        "change_ytd_pct": plain(quote.change_ytd_pct),
        "pe_ttm": plain(quote.pe_ttm),
        "turnover_pct": plain(quote.turnover_pct),
        "main_net_inflow": yi(quote.main_net_inflow),
        "main_net_inflow_5d": yi(quote.main_net_inflow_5d),
        "advancers": count(quote.advancers),
        "decliners": count(quote.decliners),
        "leader_name": leader_name,
        "leader_change_pct": plain(leader_change),
    }
