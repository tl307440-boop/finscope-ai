import hashlib
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DisclosureDraft:
    title: str
    canonical_url: str
    source_name: str
    published_at: datetime
    event_type: str


@dataclass(frozen=True)
class HotspotDay:
    day: str
    count: int
    titles: tuple[str, ...]


def event_hash(url: str, title: str, published_at: datetime) -> str:
    raw = f"{url.strip()}|{title.strip()}|{published_at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_disclosure(draft: DisclosureDraft) -> str | None:
    if draft.source_name.strip() not in {"cninfo", "sse", "szse", "sec"}:
        return "这个来源不在允许的公告名单里"
    if not draft.canonical_url.startswith("https://"):
        return "公告必须带官方链接"
    if not draft.title.strip():
        return "公告标题不能为空"
    return None


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
