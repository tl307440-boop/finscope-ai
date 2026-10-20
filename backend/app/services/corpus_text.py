"""语料库的文本处理：语言判断、全文索引用的词串、查询词、块头。纯函数。

见规格 §11、知识库设计 §4 与 §6。中文没有分词器（官方 pgvector 镜像不带 zhparser），
按二字词写进 `simple` 配置；英文用 `english` 配置，带词形还原。
和「我的文档」页的旧索引不同，这里二字词**全部**保留，不截断（R10）。
"""

import re
from collections.abc import Sequence
from datetime import date

from app.services.text_embed import search_tokens

# 语料库独立的分块参数（规格 R12）：块小一些，便于精确引用。
CHUNK_TARGET_TOKENS = 280
CHUNK_MAX_TOKENS = 360
CHUNK_OVERLAP_TOKENS = 40

_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
# 中文字符占字母类字符的比例达到这个值就按中文处理：中文文档里常夹英文缩写（PCR、CPI）。
_ZH_SHARE = 0.2


def detect_language(text: str) -> str:
    cjk = len(_CJK.findall(text))
    latin = len(_LATIN.findall(text))
    if cjk == 0:
        return "en"
    # 一个汉字约等于一个英文单词的信息量，英文按 5 个字母折一个词。
    return "zh" if cjk / (cjk + latin / 5) >= _ZH_SHARE else "en"


def ts_config(language: str) -> str:
    return "english" if language == "en" else "simple"


def index_text(text: str, language: str) -> str:
    """写进 `to_tsvector` 的文本：英文原样交给 `english` 配置，中文换成二字词串。"""

    if language == "en":
        return text
    return " ".join(search_tokens(text))


def zh_query_terms(keywords: Sequence[str]) -> list[str]:
    """中文文档的查询：每个关键词一组二字词（组内同时出现），英文关键词也按单词放进来。"""

    terms = [" ".join(search_tokens(keyword)) for keyword in keywords]
    return [term for term in dict.fromkeys(terms) if term]


def en_query_terms(keywords: Sequence[str]) -> list[str]:
    terms = [keyword.strip() for keyword in keywords if _LATIN.search(keyword)]
    return [term for term in dict.fromkeys(terms) if term]


def context_header(
    *,
    title: str,
    publisher: str,
    published: date,
    section: str | None = None,
    symbol: str | None = None,
    period: str | None = None,
) -> str:
    """块头：说明这一块属于哪篇文档。

    用模板拼，不调模型（知识库设计 §2「便宜版 Contextual Retrieval」）。
    """

    parts = [publisher, symbol or "", title, period or "", published.isoformat(), section or ""]
    return "｜".join(part.strip() for part in parts if part and part.strip())
