import hashlib
from dataclasses import dataclass
from datetime import datetime

# 合规披露来源，用官方站点的简称作为徽标文案。
ALLOWED_SOURCES: tuple[tuple[str, str], ...] = (
    ("cninfo", "巨潮资讯"),
    ("sse", "上海证券交易所"),
    ("szse", "深圳证券交易所"),
    ("sec", "SEC EDGAR"),
)

# 事件类型是闭合集合：页面按类型统计主题热度，不能让任意字符串混进来。
ALLOWED_EVENT_TYPES: tuple[tuple[str, str], ...] = (
    ("filing", "定期报告"),
    ("earnings", "业绩公告"),
    ("dividend", "分红送转"),
    ("governance", "治理与股权"),
    ("inquiry", "问询与监管"),
    ("other", "其他公告"),
)

STREAM_LABEL = "公告流"
EXTERNAL_FEED_NAMES: tuple[tuple[str, str], ...] = (
    ("tushare_news", "Tushare 新闻"),
    ("akshare_news", "AKShare 快讯"),
)


@dataclass(frozen=True)
class DisclosureDraft:
    title: str
    canonical_url: str
    source_name: str
    published_at: datetime
    event_type: str


@dataclass(frozen=True)
class NewsEventInput:
    """导入请求里的一条事件。index 用来在错误明细里定位原始行。"""

    index: int
    draft: DisclosureDraft
    usage_rights: str
    industry_id: str | None = None


@dataclass(frozen=True)
class HotspotDay:
    day: str
    count: int
    titles: tuple[str, ...]


@dataclass(frozen=True)
class HotspotTopic:
    event_type: str
    label: str
    count: int


def event_hash(url: str, title: str, published_at: datetime) -> str:
    raw = f"{url.strip()}|{title.strip()}|{published_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_disclosure(draft: DisclosureDraft) -> str | None:
    if draft.source_name.strip() not in {code for code, _label in ALLOWED_SOURCES}:
        return "这个来源不在允许的公告名单里"
    if not draft.canonical_url.startswith("https://"):
        return "公告必须带官方链接"
    if not draft.title.strip():
        return "公告标题不能为空"
    return None


def validate_event_type(event_type: str) -> str | None:
    if event_type.strip() not in {code for code, _label in ALLOWED_EVENT_TYPES}:
        return "事件类型不在已登记的公告类型里"
    return None


def validate_import(item: NewsEventInput) -> str | None:
    """导入前的逐条校验：来源、链接、标题、事件类型、使用许可都要过。"""

    problem = validate_disclosure(item.draft)
    if problem is not None:
        return problem
    problem = validate_event_type(item.draft.event_type)
    if problem is not None:
        return problem
    if not item.usage_rights.strip():
        return "必须记录使用许可，没有许可的公告不能收录"
    return None


def source_label(source_name: str) -> str:
    for code, label in ALLOWED_SOURCES:
        if code == source_name:
            return label
    return source_name


def event_type_label(event_type: str) -> str:
    for code, label in ALLOWED_EVENT_TYPES:
        if code == event_type:
            return label
    return event_type


def heat_label(count: int) -> str:
    if count <= 0:
        return "暂无合规公告，不展示情绪分数"
    return f"公告 {count} 条。热度只按条数计算，不使用媒体情绪。"


def group_by_day(rows: list[tuple[datetime, str]]) -> list[HotspotDay]:
    buckets: dict[str, list[str]] = {}
    for published_at, title in rows:
        key = published_at.date().isoformat()
        buckets.setdefault(key, []).append(title)
    return [
        HotspotDay(day=day, count=len(titles), titles=tuple(titles))
        for day, titles in sorted(buckets.items(), reverse=True)
    ]


def group_by_type(rows: list[tuple[str, str]]) -> list[HotspotTopic]:
    """按事件类型聚合主题热度；只按条数排序，不做任何情绪打分。"""

    counts: dict[str, int] = {}
    for event_type, _title in rows:
        counts[event_type] = counts.get(event_type, 0) + 1
    topics = [
        HotspotTopic(event_type=event_type, label=event_type_label(event_type), count=count)
        for event_type, count in counts.items()
    ]
    topics.sort(key=lambda item: (-item.count, item.event_type))
    return topics
