"""`hotspot_events`：本系统收录的上市公司公告与披露事件（`news_events` 表）。

只收系统跟踪的公司在巨潮资讯（A 股）和 SEC EDGAR（美股）披露的公告，带公布时间和原文链接，
不是全市场新闻，也不含媒体报道。热度只是「近期披露条数」，没有情绪或评分。
时点：只返回 `published_at <= as_of 当天结束` 的事件，按上海日历日换算。
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.news_events import NewsEventRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.utils.market_time import SHANGHAI, shanghai_pit

SOURCE = "本系统收录的巨潮资讯 / SEC EDGAR 披露事件（系统跟踪的公司）"
_WHY_EMPTY = (
    "只收录系统跟踪的公司的公告，不是全市场新闻；想看媒体报道和行业动态可以加载 news 技能联网查"
)


class HotspotEventsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(
        default=None, min_length=1, max_length=20, description="标题里要包含的词，如公司名「中芯」"
    )
    days: int = Field(default=7, ge=1, le=30, description="回看多少天，默认 7")
    top: int = Field(default=20, ge=5, le=40, description="最多返回几条，默认 20")
    as_of: date | None = Field(default=None, description="只看这个日期当天及之前公布的事件")


class HotspotEventsTool:
    name: ClassVar[str] = "hotspot_events"
    description: ClassVar[str] = (
        "查本系统收录的上市公司公告与披露事件：业绩预告、重大合同、增减持、问询函、美股 8-K 等，"
        "按公布时间从新到旧，带原文链接。只覆盖系统跟踪的公司，不是全市场新闻。"
        "可以用 keyword 按标题找某家公司或某类事件。"
    )
    args_model: ClassVar[type[BaseModel]] = HotspotEventsArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, HotspotEventsArgs)
        pit = effective_as_of(args.as_of, ctx.today_cn)
        end = min(ctx.now, _end_of_day(pit))
        start = _start_of_day(pit - timedelta(days=args.days - 1))
        title = f"披露事件：{args.keyword}" if args.keyword else "披露事件"
        events, total = NewsEventRepository(ctx.engine).search(
            start=start, end=end, keyword=args.keyword, limit=args.top
        )
        if not events:
            scope = f"包含「{args.keyword}」的" if args.keyword else ""
            return unavailable(
                self.name,
                title,
                pit,
                f"最近 {args.days} 天没有{scope}披露事件；{_WHY_EMPTY}",
                SOURCE,
            )
        rows: list[dict[str, str | None]] = [
            {
                "published": shanghai_pit(event.published_at).isoformat(),
                "source": event.source_label,
                "type": event.event_type_label,
                "title": event.title,
                "url": event.canonical_url,
            }
            for event in events
        ]
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=tuple(rows),
            as_of=shanghai_pit(events[0].published_at),
            source=SOURCE,
            truncated=max(0, total - len(events)),
        )


COLUMNS = (
    Column("published", "公布日", "", "date"),
    Column("source", "来源", "", "text"),
    Column("type", "类型", "", "text"),
    Column("title", "标题", "", "text"),
    Column("url", "原文链接", "", "text"),
)


def _start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=SHANGHAI).astimezone(UTC)


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=SHANGHAI).astimezone(UTC)
