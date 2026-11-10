"""`corpus_search`：在语料库里做全文检索，返回原文片段（规格 §7、§12，知识库设计 §6）。

片段登记成来源 [n]；叙述引用片段内容时必须写 `[n]`，片段里的数字只对引用了它的句子放行。
结果含用户私有文档，不进工具缓存（见 `tool_cache.py`）。
关键词里带时间意图（最近、最新、今年……）时按发布时间新旧加权排序，规则见 `corpus_freshness.py`。
配了语料向量密钥时是混合检索（全文 + 语义），转向量失败就只用全文，见 `corpus_vectors.py`。
"""

import hashlib
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, ClassVar, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine

from app.repositories.corpus import (
    USER_UPLOAD_SOURCE,
    CorpusQuery,
    CorpusRepository,
    NewChunk,
    NewDocument,
    QueryVector,
    semantic_query_text,
)
from app.repositories.corpus import Passage as StoredPassage
from app.services.assistant.results import Column, Passage, ToolResult, unavailable
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
        "用户问「最近、最新、今年、本周、昨天」这类时间意图时，把这个时间词也写进关键词，"
        "结果会按发布时间的新旧加权，新的排前面；时间词本身不参与匹配。没有时间词就只按相关度排。"
    )
    args_model: ClassVar[type[BaseModel]] = CorpusSearchArgs

    def __init__(
        self,
        min_rank: float = 0.0,
        *,
        min_similarity: float = 0.0,
        embed_query: Callable[[Engine, str], QueryVector | None] | None = None,
    ) -> None:
        self._min_rank = min_rank
        self._min_similarity = min_similarity
        self._embed_query = embed_query

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, CorpusSearchArgs)
        words = [word.strip() for word in [*args.keywords_zh, *args.keywords_en]]
        # 步骤名会写成「检索资料：<标题>」，标题只放关键词。
        title = "、".join(words)[:60]
        keywords_zh = tuple(word.strip() for word in args.keywords_zh)
        keywords_en = tuple(word.strip() for word in args.keywords_en)
        vector = (
            None
            if self._embed_query is None
            else self._embed_query(ctx.engine, semantic_query_text(keywords_zh, keywords_en))
        )
        found = CorpusRepository(ctx.engine).search(
            CorpusQuery(
                user_id=ctx.user_id,
                as_of=ctx.now,
                keywords_zh=keywords_zh,
                keywords_en=keywords_en,
                kind=args.kind,
                symbol=args.symbol,
                report_type=args.report_type,
                period=args.period,
                publisher=args.publisher,
                since=args.since,
                until=args.until,
                min_rank=self._min_rank,
                vector=vector,
                min_similarity=self._min_similarity,
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


SAVE_COLUMNS = (
    Column("title", "标题", "", "text"),
    Column("section", "分类", "", "text"),
    Column("chars", "字数", "字", "number"),
    Column("status", "状态", "", "text"),
)


class CorpusSaveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        min_length=1,
        max_length=120,
        description="词条或研究笔记标题，如「短线情绪周期：节点」",
    )
    content: str = Field(
        min_length=4,
        max_length=4000,
        description="词条定义或研究笔记正文，写清楚核心口径、适用场景与注意事项",
    )
    section_title: str | None = Field(
        default=None,
        max_length=80,
        description="可选的章节或分类名，如「短线交易术语」「研究笔记」",
    )


class CorpusSaveTool:
    name: ClassVar[str] = "corpus_save"
    description: ClassVar[str] = (
        "把用户整理或确认的概念词条、交易术语、投研笔记保存到本人的语料库"
        "（保存后即可通过 corpus_search 检索引用）。"
        "只在用户明确要求「加入语料库」「存进知识库」「记下这条笔记」时使用。"
    )
    args_model: ClassVar[type[BaseModel]] = CorpusSaveArgs

    def __init__(
        self,
        save_fn: Callable[[NewDocument, Sequence[NewChunk]], UUID | None] | None = None,
    ) -> None:
        self._save_fn = save_fn

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, CorpusSaveArgs)
        title = args.title.strip()
        content = args.content.strip()
        section = args.section_title.strip() if args.section_title else None
        if not title or not content:
            return unavailable(
                self.name, title or "保存语料", ctx.today_cn, "标题和正文不能为空", SOURCE_LABEL
            )
        if self._save_fn is None and ctx.engine is None:
            return unavailable(
                self.name, title, ctx.today_cn, "当前环境未连接语料库，无法写入", SOURCE_LABEL
            )
        title_hash = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        canonical_url = f"user-note://{ctx.user_id}/{title_hash}"
        doc = NewDocument(
            source_id=USER_UPLOAD_SOURCE,
            canonical_url=canonical_url,
            kind="user",
            title=title,
            doc_type="note",
            language="zh",
            published_at=ctx.now,
            content_hash=content_hash,
            authority="user",
            owner_user_id=ctx.user_id,
            summary=content[:200],
            license_note="用户保存在助手中的个人研究笔记与词条",
        )
        chunk = NewChunk(
            content=content,
            char_start=0,
            char_end=len(content),
            token_count=max(1, len(content) // 2),
            section_title=section,
            context_header=f"{title} {section or ''}".strip(),
        )
        saver = self._save_fn or CorpusRepository(ctx.engine).save
        doc_id = saver(doc, [chunk])
        status_note = "已写入语料库" if doc_id is not None else "语料库已有相同内容，无需重复写入"
        passage = Passage(
            key=f"note:{title_hash}",
            title=title,
            publisher="你上传的文档",
            corpus_kind="user",
            published=ctx.today_cn,
            content=content,
            url="",
            section=section,
        )
        row: dict[str, str | None] = {
            "title": title,
            "section": section or "个人笔记",
            "chars": str(len(content)),
            "status": status_note,
        }
        return ToolResult(
            tool=self.name,
            title=f"已保存语料：{title[:40]}",
            columns=SAVE_COLUMNS,
            rows=(row,),
            as_of=ctx.today_cn,
            source=SOURCE_LABEL,
            passages=(passage,),
        )
