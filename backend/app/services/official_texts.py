"""美联储与国家统计局的公开文本（知识库设计 §5.2）。

美联储走官方 RSS，统计局走数据发布栏目的第一页。正文只从白名单主机下载。
RSS 里没有发布时间的条目丢掉。访谈如果官方没有文字稿，这里不会有，要靠联网检索。
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from uuid import UUID

from sqlalchemy import Engine, text

from app.core.config import Settings, get_settings
from app.providers.errors import ProviderError
from app.providers.polite import PoliteError, contact_email, live_client
from app.repositories.corpus import (
    FED_PRESS_SOURCE,
    FED_SPEECHES_SOURCE,
    FED_TESTIMONY_SOURCE,
    NBS_RELEASE_SOURCE,
    CorpusRepository,
)
from app.services.cninfo_filings import store_filing
from app.services.filing_meta import FilingHit
from app.services.html_text import article_text
from app.services.pdf_text import PageText
from app.services.rss_feed import FeedItem, parse_rss

FED_HOSTS = frozenset({"www.federalreserve.gov"})
NBS_HOSTS = frozenset({"www.stats.gov.cn"})
_NBS_PAGE = "https://www.stats.gov.cn/sj/zxfb/"
_NO_CONTACT = "没有配置可用的联系邮箱（SEC_USER_AGENT），不能抓取官方文本"


@dataclass(frozen=True)
class OfficialFeed:
    code: str
    source_id: UUID
    publisher: str
    list_url: str
    hosts: frozenset[str]
    doc_type: str
    element_id: str | None = None
    css_class: str | None = None
    listing: str = "rss"  # rss | nbs


FEEDS: dict[str, OfficialFeed] = {
    item.code: item
    for item in (
        OfficialFeed(
            "fed_speeches",
            FED_SPEECHES_SOURCE,
            "美联储",
            "https://www.federalreserve.gov/feeds/speeches.xml",
            FED_HOSTS,
            "speech",
            element_id="article",
        ),
        OfficialFeed(
            "fed_press",
            FED_PRESS_SOURCE,
            "美联储",
            "https://www.federalreserve.gov/feeds/press_all.xml",
            FED_HOSTS,
            "statement",
            element_id="article",
        ),
        OfficialFeed(
            "fed_testimony",
            FED_TESTIMONY_SOURCE,
            "美联储",
            "https://www.federalreserve.gov/feeds/testimony.xml",
            FED_HOSTS,
            "testimony",
            element_id="article",
        ),
        OfficialFeed(
            "nbs_release",
            NBS_RELEASE_SOURCE,
            "国家统计局",
            _NBS_PAGE,
            NBS_HOSTS,
            "release",
            css_class="txt-content",
            listing="nbs",
        ),
    )
}


def parse_nbs_list(payload: bytes, page_url: str) -> tuple[FeedItem, ...]:
    parser = _NbsLinks()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    found: list[FeedItem] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = urljoin(page_url, href)
        if url in seen or not url.startswith("https://www.stats.gov.cn/"):
            continue
        published = _nbs_date(href)
        if published is None or not title.strip():
            continue
        seen.add(url)
        found.append(FeedItem(title=title.strip(), url=url, published_at=published))
    return tuple(found)


def sync_official(engine: Engine, code: str, *, limit: int = 8) -> int:
    """抓一个来源最近若干篇还没入库的正文。返回新写入的篇数。"""

    feed = FEEDS.get(code)
    if feed is None:
        raise ProviderError(code="VALIDATION_ERROR", message="没有这个官方来源", status_code=422)
    email = contact_email(get_settings().sec_user_agent)
    if email is None:
        raise ProviderError(code="DATA_UNAVAILABLE", message=_NO_CONTACT, status_code=503)
    client = live_client(email, feed.hosts)
    try:
        listing = client.get(feed.list_url)
    except PoliteError as exc:
        raise ProviderError(code=exc.code, message=str(exc), status_code=503) from exc
    items = parse_rss(listing) if feed.listing == "rss" else parse_nbs_list(listing, feed.list_url)
    repo = CorpusRepository(engine)
    saved = 0
    for item in items:
        if saved >= limit:
            break
        if repo.latest_passages(source_id=feed.source_id, url=item.url, limit=1):
            continue
        try:
            body = client.get(item.url)
        except PoliteError as exc:
            if exc.code == "BLOCKED":
                raise ProviderError(
                    code="UPSTREAM_BLOCKED", message=str(exc), status_code=403
                ) from exc
            continue
        text = article_text(body, element_id=feed.element_id, css_class=feed.css_class)
        if not text.strip():
            continue
        hit = FilingHit(
            code="",
            title=item.title,
            url=item.url,
            published_at=item.published_at,
            report_type="",
            period=None,
        )
        written = store_filing(
            repo,
            hit,
            [PageText(page_number=1, text=text)],
            instrument_id=None,
            source_id=feed.source_id,
            publisher=feed.publisher,
            license_note="官方公开文本，仅作研究引用，保留原文链接",
            kind="official",
            doc_type=feed.doc_type,
        )
        if written is not None:
            saved += 1
    return saved


def empty_official_sources(engine: Engine) -> list[str]:
    """还没有任何正文的官方来源。迁移没建出来的来源不算，避免启动时报错。"""

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT s.code FROM corpus_sources s
                WHERE s.code = ANY(:codes)
                  AND NOT EXISTS (
                      SELECT 1 FROM corpus_documents d WHERE d.source_id = s.id
                  )
                ORDER BY s.code
                """
            ),
            {"codes": list(FEEDS)},
        )
    return [str(row[0]) for row in rows]


def catch_up_official_texts(
    engine: Engine,
    settings: Settings,
    *,
    missing: list[str] | None = None,
    enqueuer: Any = None,
    list_tasks: Any = None,
) -> int:
    """调度器启动时，空着的官方来源马上补最近一批，不用等到下一个整点。

    已经有正文的来源不重复登记，之后靠每小时的定时任务补新的。
    没有真实联系邮箱时不登记。
    """

    from app.services.scheduler import build_official_text_tasks, enqueue_built

    if not settings.scheduler_enable_official_texts:
        return 0
    if contact_email(settings.sec_user_agent) is None:
        return 0
    wanted = set(FEEDS if missing is None else missing)
    if missing is None:
        wanted = set(empty_official_sources(engine))
    if not wanted:
        return 0

    def builder(current: Engine, active: Settings, now: datetime) -> list[Any]:
        source = list_tasks or build_official_text_tasks
        picked = []
        for task in source(current, active, now):
            if task.scope.get("source") not in wanted:
                continue
            task.scope["limit"] = "20"
            picked.append(task)
        return picked

    return enqueue_built(engine, settings, builder, "sync_official_texts", enqueuer=enqueuer)


_NBS_STAMP = re.compile(r"t(20\d{2})(\d{2})(\d{2})_")


def _nbs_date(href: str) -> datetime | None:
    found = _NBS_STAMP.search(href)
    if found is None:
        return None
    year, month, day = (int(part) for part in found.groups())
    try:
        # 统计局数据发布通常在上海时间 9:30。
        return datetime(year, month, day, 1, 30, tzinfo=UTC)
    except ValueError:
        return None


class _NbsLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attr = {key: value or "" for key, value in attrs}
        href = attr.get("href", "")
        title = attr.get("title", "")
        if title and _NBS_STAMP.search(href):
            self.links.append((href, title))
