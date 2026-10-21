"""`corpus_search`：在语料库里做全文检索，返回原文片段（规格 §7、§12，知识库设计 §6）。

片段登记成来源 [n]；叙述引用片段内容时必须写 `[n]`，片段里的数字只对引用了它的句子放行。
结果含用户私有文档，不进工具缓存（见 `tool_cache.py`）。
"""

from datetime import date
from typing import Any, ClassVar, Literal, Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.repositories.corpus import CorpusQuery, CorpusRepository
from app.repositories.corpus import Passage as StoredPassage
from app.services.assistant.results import Passage, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext

SOURCE_LABEL = "本系统语料库"
NOTHING_FOUND = "语料库里没有相关资料"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class CorpusSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keywords_zh: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "中文关键词，每个 1–40 字；一个关键词里的字词要同时出现，关键词之间任一命中即可"
        ),
    )
    keywords_en: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="英文关键词；查英文来源（美联储等）时必须给，中文文档里的英文缩写也会匹配",
    )
    kind: Literal["filing", "official", "reference", "user"] | None = Field(
        default=None,
        description=(
            "只查某一类：filing 公告、official 官方文本、reference 参考资料、user 用户上传的文档"
        ),
    )
    symbol: str | None = Field(default=None, max_length=32, description="证券代码，如 688825")
    report_type: (
        Literal["annual", "semiannual", "q1", "q3", "forecast", "express", "inquiry", "other"]
        | None
    ) = Field(default=None, description="公告细类，forecast 是业绩预告，express 是业绩快报")
    period: str | None = Field(default=None, max_length=16, description="期次，如 2026Q3、2026H1")
    publisher: str | None = Field(default=None, max_length=80, description="发布方，如 美联储")
    since: date | None = Field(default=None, description="只要这天及以后发布的")
    until: date | None = Field(default=None, description="只要这天及以前发布的")

    @model_validator(mode="after")
    def _has_keywords(self) -> Self:
        words = [*self.keywords_zh, *self.keywords_en]
        if not any(word.strip() for word in words):
            raise ValueError("至少给一个关键词")
        if any(not 1 <= len(word.strip()) <= 40 for word in words):
            raise ValueError("每个关键词 1–40 字")
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since 不能晚于 until")
        return self


class CorpusSearchTool:
    name: ClassVar[str] = "corpus_search"
    description: ClassVar[str] = (
        "在本系统语料库里按关键词检索原文片段（用户上传的文档、已抓取的公告，以及美联储、统计局等官方文本）。"
        "适合问文档、公告、讲话里写了什么；数字、行情、宏观序列用其他数据工具，不要来这里查。"
        "返回的每段原文有编号 [n]，回答里用到它的内容就在句末写 [n]。"
    )
    args_model: ClassVar[type[BaseModel]] = CorpusSearchArgs

    def __init__(self, min_rank: float = 0.0) -> None:
        self._min_rank = min_rank

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, CorpusSearchArgs)
        words = [word.strip() for word in [*args.keywords_zh, *args.keywords_en]]
        # 步骤名会写成「检索资料：<标题>」，标题只放关键词。
        title = "、".join(words)[:60]
        found = CorpusRepository(ctx.engine).search(
            CorpusQuery(
                user_id=ctx.user_id,
                as_of=ctx.now,
                keywords_zh=tuple(word.strip() for word in args.keywords_zh),
                keywords_en=tuple(word.strip() for word in args.keywords_en),
                kind=args.kind,
                symbol=args.symbol,
                report_type=args.report_type,
                period=args.period,
                publisher=args.publisher,
                since=args.since,
                until=args.until,
                min_rank=self._min_rank,
            )
        )
        if not found:
            return unavailable(self.name, title, ctx.today_cn, NOTHING_FOUND, SOURCE_LABEL)
        passages = tuple(_passage(item) for item in found)
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(),
            rows=(),
            # 资料截止取最新一篇的发布日，不用今天：今天的日期不该因此被放行。
            as_of=max(passage.published for passage in passages),
            source=SOURCE_LABEL,
            passages=passages,
        )


def _passage(item: StoredPassage) -> Passage:
    return Passage(
        key=str(item.chunk_id),
        title=item.title,
        publisher=item.publisher,
        corpus_kind=item.kind,
        published=item.published_at.astimezone(_SHANGHAI).date(),
        content=item.content,
        url=item.url,
        section=item.section_title,
        page_start=item.page_start,
        page_end=item.page_end,
    )
