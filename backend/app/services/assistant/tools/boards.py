"""东方财富行业 / 概念板块工具 `industry_board`（规格 §7）。

数据来自本系统每天入库的板块快照（`board_daily_snapshots`），不现抓。按名称模糊匹配，
一次可以查几个关键词，方便把「半导体」「芯片」「光刻机」这类相关板块放在一起比。
时点：只看 `as_of` 当天或之前最近一个快照日。金额按亿元给出（元 ÷ 10^8，Decimal 计算）。
"""

from datetime import date
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.repositories.board_market import BoardMarketRepository, StoredSnapshot
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.fmt import count, plain, yi

MAX_QUERIES = 4
PER_QUERY = 4
MAX_ROWS = 12
STALE_DAYS = 10  # 与日线一致：长假（国庆约 8 天不开市）不算过期
SOURCE = "东方财富行业 / 概念板块（本系统每日快照）"
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
        "一次可查 1–4 个关键词。描述的是已经发生的行情，不是预测。"
    )
    args_model: ClassVar[type[BaseModel]] = IndustryBoardArgs

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
        stale = (pit - latest).days > STALE_DAYS
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=tuple(_row(item) for item in items),
            as_of=latest,
            source=SOURCE,
            status="stale" if stale else "ok",
            reason=(
                f"最新的板块快照是 {latest.isoformat()}，距 {pit.isoformat()} "
                f"已超过 {STALE_DAYS} 天，可能尚未更新"
                if stale
                else None
            ),
            truncated=max(0, len(found) - MAX_ROWS),
        )


COLUMNS = (
    Column("name", "板块", "", "text"),
    Column("kind", "类型", "", "text"),
    Column("trade_date", "日期", "", "date"),
    Column("change_pct", "当日涨跌幅", "%", "number"),
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


def _row(item: StoredSnapshot) -> dict[str, str | None]:
    quote = item.quote
    return {
        "name": item.node.name,
        "kind": _KIND_NAMES.get(item.node.kind, item.node.kind),
        "trade_date": item.trade_date.isoformat(),
        "change_pct": plain(quote.change_pct),
        "change_5d_pct": plain(quote.change_5d_pct),
        "change_60d_pct": plain(quote.change_60d_pct),
        "change_ytd_pct": plain(quote.change_ytd_pct),
        "pe_ttm": plain(quote.pe_ttm),
        "turnover_pct": plain(quote.turnover_pct),
        "main_net_inflow": yi(quote.main_net_inflow),
        "main_net_inflow_5d": yi(quote.main_net_inflow_5d),
        "advancers": count(quote.advancers),
        "decliners": count(quote.decliners),
        "leader_name": quote.leader_name,
        "leader_change_pct": plain(quote.leader_change_pct),
    }
