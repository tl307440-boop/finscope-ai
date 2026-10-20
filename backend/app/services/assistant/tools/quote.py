"""`quote_table`：把检索到的原文整理成「原文 / 倾向 / 解读」表（规格 §8 规则 7、§9）。

每行的原文必须是来源 [n] 片段里的逐字子串（NFKC 归一、去掉空白后比较）；解读和标题是模型写的，
按引用了 [n] 的句子核对数字和日期。任何一行不过关，整张表都不出，原因只回给模型。
"""

import re
import unicodedata
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.assistant.evidence import as_shown
from app.services.assistant.grounding import check
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext

DEFAULT_TITLE = "原文摘录"
MIN_ORIGINAL_CHARS = 4
_SPACE = re.compile(r"\s+")
_DIGIT = re.compile(r"\d")

COLUMNS = (
    Column("source", "来源"),
    Column("original", "原文"),
    Column("stance", "倾向"),
    Column("note", "解读"),
)


class QuoteRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: int = Field(
        ge=1, le=99, description="原文出自哪个来源编号，即 corpus_search 结果里的 [n]"
    )
    original: str = Field(
        min_length=MIN_ORIGINAL_CHARS,
        max_length=300,
        description="从该来源原文里逐字摘抄的一句或一段，不能改字、不能拼接",
    )
    stance: str = Field(
        min_length=1, max_length=10, description="倾向，如 偏鹰、偏鸽、中性、利好、利空、不确定"
    )
    note: str = Field(min_length=1, max_length=160, description="一句话解读或中文意译")

    @field_validator("stance")
    @classmethod
    def _no_digits(cls, value: str) -> str:
        if _DIGIT.search(value):
            raise ValueError("倾向里不要写数字")
        return value.strip()


class QuoteTableArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default=DEFAULT_TITLE, min_length=1, max_length=40, description="表标题")
    rows: list[QuoteRow] = Field(min_length=1, max_length=10)


class QuoteTableTool:
    name: ClassVar[str] = "quote_table"
    description: ClassVar[str] = (
        "把 corpus_search 查到的原文整理成表给读者看：每行一句逐字摘抄的原文、倾向、一句解读。"
        "适合讲话、声明、公告措辞的逐条解读。原文必须一字不差地出自所写的来源 [n]，"
        "解读里的数字也只能来自那段原文；不符合的整张表都不会显示。"
    )
    args_model: ClassVar[type[BaseModel]] = QuoteTableArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, QuoteTableArgs)
        title = args.title.strip() or DEFAULT_TITLE
        problems: list[str] = []
        published: list[date] = []
        for index, row in enumerate(args.rows, 1):
            passage = ctx.book.source(row.source)
            if passage is None:
                problems.append(f"第 {index} 行：没有来源 [{row.source}]")
                continue
            published.append(passage.published)
            if _normalized(row.original) not in _normalized(passage.content):
                problems.append(f"第 {index} 行：原文不是来源 [{row.source}] 里的原话")
            if not check(f"{row.note}[{row.source}]", ctx.book).ok:
                problems.append(f"第 {index} 行：解读里有来源 [{row.source}] 里找不到的数字或日期")
        cited = "".join(f"[{n}]" for n in sorted({row.source for row in args.rows}))
        if not problems and not check(f"{title}{cited}", ctx.book).ok:
            problems.append("标题里有资料里找不到的数字或日期")
        if problems:
            return unavailable(self.name, title, ctx.today_cn, "；".join(problems[:5]))
        return ToolResult(
            tool=self.name,
            title=title,
            columns=COLUMNS,
            rows=tuple(
                {
                    "source": f"[{row.source}]",
                    "original": _SPACE.sub(" ", row.original).strip(),
                    "stance": row.stance,
                    "note": row.note.strip(),
                }
                for row in args.rows
            ),
            as_of=max(published),
            source="本系统语料库",
            excerpt=True,
        )


def _normalized(text: str) -> str:
    return _SPACE.sub("", unicodedata.normalize("NFKC", as_shown(text)))
