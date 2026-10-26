"""`news_flash`：多来源财经快讯标题，用来回答「现在市场在讨论什么」。

东方财富、华尔街见闻、金十、财联社电报并行取。一家失败不影响其余几家，失败写进说明。
只给标题、发布时间、媒体名和原文链接；不取正文（版权归原媒体），也不入库。
只有实时数据，不能回看过去某一天。雪球、韭研公社、开盘啦、通达信不在这里：
前三家要登录令牌或设备令牌，通达信对不上板块代码，都不绕过。
"""

import asyncio
from datetime import date, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.providers.eastmoney_news import NewsItem
from app.providers.errors import ProviderError
from app.services.assistant.loop_bridge import call_async
from app.services.assistant.results import Column, Passage, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.live import live_only_reason, upstream_reason
from app.services.news_flash import NewsFlashService, get_news_service
from app.utils.market_time import SHANGHAI, shanghai_pit

SOURCE = "东方财富、华尔街见闻、金十数据、财联社电报（只有标题，未核实）"
MAX_CALLS_PER_TURN = 3
FLASH_LABEL = "7x24 快讯"
SEARCH_LABEL = "新闻搜索"
_TURN_LIMIT_REASON = (
    f"本回合已经查过 {MAX_CALLS_PER_TURN} 次实时新闻标题，不要再查，用已有的标题回答；"
    "已有标题不够时如实说明。"
)
_UNVERIFIED = "这些只是媒体标题，没有被系统核对；回答要写明是报道，不要当成已证实的事实。"


class NewsFlashArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(
        default=None,
        min_length=2,
        max_length=20,
        description="要找的主题或公司名，如「半导体」「中芯国际」；不填看最新快讯",
    )
    hours: int = Field(default=72, ge=1, le=168, description="只要最近多少小时内的，默认 72")
    top: int = Field(default=15, ge=5, le=30, description="最多返回几条，默认 15")
    as_of: date | None = Field(default=None, description="只有实时数据；填过去的日期会被拒绝")


class NewsFlashTool:
    name: ClassVar[str] = "news_flash"
    description: ClassVar[str] = (
        "查此刻的财经快讯和新闻标题（东方财富、华尔街见闻、金十、财联社，以及东方财富新闻搜索），"
        "带发布时间和原文链接。一家来源取不到时用其余几家。"
        "适合「最近有什么消息」「这几天市场在讨论什么」；用 keyword 找某个主题或公司。"
        "只有标题没有正文，只有实时数据，来源没有被系统核对。"
        f"每回合最多查 {MAX_CALLS_PER_TURN} 次。要看政策原文、公告原文不要用它。"
    )
    args_model: ClassVar[type[BaseModel]] = NewsFlashArgs

    def __init__(self, service: NewsFlashService | None = None) -> None:
        self._service = service

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, NewsFlashArgs)
        title = f"新闻标题：{args.keyword}" if args.keyword else "最新快讯"
        today = ctx.today_cn
        refused = live_only_reason(args.as_of, today, "新闻标题")
        if refused is not None:
            return unavailable(self.name, title, today, refused, SOURCE)
        if ctx.news_calls >= MAX_CALLS_PER_TURN:
            return unavailable(self.name, title, today, _TURN_LIMIT_REASON, SOURCE)
        ctx.news_calls += 1
        service = self._service or get_news_service()
        try:
            items, notes = call_async(lambda: _collect(service, args.keyword), timeout=30.0)
        except ProviderError as error:
            return unavailable(self.name, title, today, upstream_reason(error), SOURCE)
        earliest = ctx.now - timedelta(hours=args.hours)
        recent = [item for item in items if earliest <= item.published_at <= ctx.now]
        shown = recent[: args.top]
        if not shown:
            scope = f"包含「{args.keyword}」的" if args.keyword else ""
            reason = f"最近 {args.hours} 小时没有查到{scope}新闻标题"
            return unavailable(self.name, title, today, "；".join([reason, *notes]), SOURCE)
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=tuple(_row(item) for item in shown),
            as_of=shanghai_pit(shown[0].published_at),
            source=SOURCE,
            status="stale" if notes else "ok",
            reason="；".join([*notes, _UNVERIFIED]),
            truncated=max(0, len(recent) - len(shown)),
            passages=tuple(_passage(item) for item in shown),
            excerpt=True,
        )


COLUMNS = (
    Column("published", "日期", "", "date"),
    Column("time", "时间（上海）", "", "text"),
    Column("publisher", "媒体", "", "text"),
    Column("title", "标题", "", "text"),
    Column("url", "链接", "", "text"),
    Column("note", "核对", "", "text"),
)


async def _collect(
    service: NewsFlashService, keyword: str | None
) -> tuple[list[NewsItem], list[str]]:
    """快讯池和（有关键词时的）新闻搜索并行取；一路失败另一路仍然有用，失败写进说明。"""

    jobs = {
        FLASH_LABEL: service.flashes(),
        "华尔街见闻": service.wallstreet(),
        "金十数据": service.jin10(),
        "财联社电报": service.cls(),
    }
    if keyword:
        jobs[SEARCH_LABEL] = service.search(keyword)
    outcomes = await asyncio.gather(*jobs.values(), return_exceptions=True)
    items: dict[str, NewsItem] = {}
    notes: list[str] = []
    errors: list[ProviderError] = []
    for label, outcome in zip(jobs, outcomes, strict=True):
        if isinstance(outcome, ProviderError):
            errors.append(outcome)
            notes.append(f"{label}暂时取不到（{outcome.message}）")
            continue
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome.stale:
            notes.append(f"{label}是缓存的旧数据（{outcome.stale_reason or '上游暂时取不到'}）")
        for item in outcome.value:
            # 四路快讯都是全市场最新的一批，有关键词时只留标题里含这个词的。
            # 新闻搜索本身已按关键词查过，标题里不一定逐字出现，不再筛掉。
            if keyword and label != SEARCH_LABEL and keyword.lower() not in item.title.lower():
                continue
            items.setdefault(item.code, item)
    if len(errors) == len(jobs):
        raise errors[0]
    ordered = sorted(items.values(), key=lambda item: item.published_at, reverse=True)
    return ordered, notes


def _row(item: NewsItem) -> dict[str, str | None]:
    local = item.published_at.astimezone(SHANGHAI)
    return {
        "published": local.date().isoformat(),
        "time": local.strftime("%H:%M"),
        "publisher": item.publisher,
        "title": item.title,
        "url": item.url,
        "note": "未核实",
    }


def _passage(item: NewsItem) -> Passage:
    local = item.published_at.astimezone(SHANGHAI)
    return Passage(
        key=f"news:{item.code}",
        title=item.title,
        publisher=item.publisher,
        corpus_kind="web",
        published=local.date(),
        content=f"{local:%Y-%m-%d %H:%M} 发布（上海时间）：{item.title}",
        url=item.url,
        verified=False,
    )
