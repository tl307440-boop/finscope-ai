"""RSS 2.0 解析。只用标准库，发布时间解析不出来的条目直接丢掉，不用抓取时间顶替。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class FeedItem:
    title: str
    url: str
    # 只有列表本身不写日期的来源（证监会首页）才是 None：
    # 发布时间以正文页元数据为准，正文也没有就丢弃。
    published_at: datetime | None


def parse_rss(payload: bytes) -> tuple[FeedItem, ...]:
    try:
        root = ElementTree.fromstring(payload.lstrip(_BOM))
    except ElementTree.ParseError:
        return ()
    found: list[FeedItem] = []
    for item in root.iter("item"):
        title = _text(item, "title")
        url = _text(item, "link")
        published = _published(_text(item, "pubDate"))
        if title and url.startswith(("https://", "http://")) and published is not None:
            found.append(FeedItem(title=title, url=url, published_at=published))
    return tuple(found)


def _text(item: ElementTree.Element, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _published(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
