"""检索的新鲜度衰减（知识库设计 §6）：纯函数，没有副作用，可以离线测试。

只有检索词里带时间意图（「最近、最新、今年、昨天……」）时才按发布时间的新旧给分数打折；
没有时间词时检索排序就是全文相关度本身。打折只用于排序：`CORPUS_MIN_RANK` 的闸门拿的是
原始相关度，旧但相关的片段不会因为衰减被整体丢掉。

分数 = 原始相关度 × 0.5^(天数 / 半衰期)，天数 = 今天减发布日，小于 0 按 0（当天或未来都不打折）。
半衰期按类别（数值待评测集校准）：

| 类别 | 半衰期 |
| --- | --- |
| 官方政策、声明、讲话、统计发布（kind=official，要闻类除外） | 180 天 |
| 公告、研报（kind=filing，或 doc_type 为 announcement、filing、report、research） | 30 天 |
| 官方要闻（kind=official 且 doc_type=news） | 30 天 |
| 快讯（doc_type 为 flash、newsflash） | 3 天 |
| 用户上传的文档（kind=user）、参考资料等其他类别 | 不衰减 |
"""

import re
from collections.abc import Iterable, Sequence
from datetime import date

POLICY_HALF_LIFE_DAYS = 180.0
ANNOUNCEMENT_HALF_LIFE_DAYS = 30.0
FLASH_HALF_LIFE_DAYS = 3.0

_FLASH_TYPES = frozenset({"flash", "newsflash"})
_ANNOUNCEMENT_TYPES = frozenset({"announcement", "filing", "report", "research"})
_NEWS_TYPES = frozenset({"news"})

# 中文按子串匹配；英文按整词匹配（不区分大小写）。
TIME_WORDS_ZH: tuple[str, ...] = (
    "最近",
    "近期",
    "最新",
    "今年",
    "本周",
    "这周",
    "本月",
    "近日",
    "刚刚",
    "刚才",
    "今天",
    "今日",
    "昨天",
    "昨日",
    "前天",
    "新发布",
    "最近几天",
    "近几天",
    "这几天",
    "前几天",
    "这个月",
    "上周",
    "上个月",
)
TIME_WORDS_EN: tuple[str, ...] = (
    "latest",
    "newest",
    "recent",
    "recently",
    "lately",
    "today",
    "yesterday",
    "this week",
    "last week",
    "this month",
    "last month",
    "this year",
    "just now",
    "breaking",
)
_EN = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(word) for word in TIME_WORDS_EN) + r")(?![a-z])",
    re.IGNORECASE,
)
# 先去长词，免得「最近几天」先被「最近」截掉一半。
_ZH = re.compile("|".join(re.escape(word) for word in sorted(TIME_WORDS_ZH, key=len, reverse=True)))


def has_time_intent(words: Iterable[str]) -> bool:
    """检索词里是否带时间意图。"""

    return any(_ZH.search(word) or _EN.search(word) for word in words)


def strip_time_words(words: Sequence[str]) -> tuple[str, ...]:
    """去掉每个关键词里的时间词，剩下空的关键词丢掉。

    时间词不该参与全文匹配：「最近 降息」要求片段里同时出现「最近」，会把该查到的挡掉。
    """

    cleaned = (" ".join(_EN.sub(" ", _ZH.sub(" ", word)).split()) for word in words)
    return tuple(word for word in cleaned if word)


def half_life_days(kind: str, doc_type: str = "") -> float | None:
    """文档的半衰期（天）；None 表示不衰减。"""

    if kind == "user":
        return None
    if doc_type in _FLASH_TYPES:
        return FLASH_HALF_LIFE_DAYS
    if kind == "filing" or doc_type in _ANNOUNCEMENT_TYPES:
        return ANNOUNCEMENT_HALF_LIFE_DAYS
    if kind == "official":
        return ANNOUNCEMENT_HALF_LIFE_DAYS if doc_type in _NEWS_TYPES else POLICY_HALF_LIFE_DAYS
    return None


def decayed_score(
    rank: float, published: date, today: date, *, kind: str, doc_type: str = ""
) -> float:
    """原始相关度 × 0.5^(天数 / 半衰期)。不在半衰期表里的类别原样返回。"""

    life = half_life_days(kind, doc_type)
    if life is None:
        return rank
    age_days = max(0, (today - published).days)
    return float(rank * 0.5 ** (age_days / life))
