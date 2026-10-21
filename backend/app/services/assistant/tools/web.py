"""`web_search`：库里没有的近期访谈、新闻、讲话，用联网检索补一句出处（知识库设计 §5.5）。

搜索结果只在本回合使用，不进语料库，也不写入回答记录。官方页面（美联储、统计局）
会改由我们自己的抓取器取原文并入库，那部分可以长期引用。
"""

import threading
from datetime import UTC, date
from typing import Any, ClassVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.providers.polite import PoliteError, contact_email, live_client
from app.providers.web_search import (
    BOCHA_URL,
    SearchHit,
    bocha_body,
    bocha_hits,
    brave_hits,
    brave_request,
)
from app.repositories.corpus import CorpusRepository
from app.services.assistant.results import Column, Passage, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import _passage
from app.services.official_texts import FEEDS, OfficialFeed
from app.services.runtime_settings import effective_settings

_CJK = "\u4e00"  # 只要问题里有汉字就先走博查
_LOCK = threading.Lock()
_USED: dict[str, tuple[date, int]] = {}
# 只拦明显不能检索的内容。宏观、选举、政策问答不在这里，那些是投研问题。
_BLOCKED = ("儿童色情", "自制炸弹", "制作炸药")
_OFFICIAL = {
    "www.federalreserve.gov": "fed_press",
    "www.stats.gov.cn": "nbs_release",
}


class WebSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=2, max_length=200, description="检索词。中文问题用中文，英文来源用英文"
    )


class WebSearchTool:
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "在网上检索近期访谈、新闻、讲话报道。只在语料库和公告里没有原文时使用。"
        "返回的是网页摘要，没有被系统核对，回答里必须写 [n]，并且说明这是报道不是官方原文。"
        "结果不会保存，刷新后需要重新查。"
    )
    args_model: ClassVar[type[BaseModel]] = WebSearchArgs

    def __init__(self, search: Any = None, fetch_page: Any = None) -> None:
        self._search = search
        self._fetch_page = fetch_page

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, WebSearchArgs)
        query = args.query.strip()
        if any(word in query for word in _BLOCKED):
            return unavailable(self.name, query[:40], ctx.today_cn, "这个问题不能联网检索", "联网")
        if self._search is None:
            settings = _settings(ctx)
            if not settings.assistant_web_search_enabled:
                return unavailable(self.name, query[:40], ctx.today_cn, "联网检索没有开通", "联网")
            if not _allow(ctx.user_id, ctx.today_cn, settings.assistant_web_daily_limit):
                return unavailable(
                    self.name, query[:40], ctx.today_cn, "今天的联网次数已经用完", "联网"
                )
            hits = _live_search(query, settings)
        else:
            hits = list(self._search(query))
        usable = [
            hit for hit in hits if hit.url.startswith("https://") and hit.snippet and hit.title
        ]
        if not usable:
            return unavailable(
                self.name, query[:40], ctx.today_cn, "网上没有查到可用的结果", "联网"
            )
        passages: list[Passage] = []
        rows: list[dict[str, str | None]] = []
        official = 0
        for index, hit in enumerate(usable[:8]):
            stored = self._official(ctx, hit) if official < 2 else []
            if stored:
                official += 1
                passages.extend(stored)
                rows.append(
                    {
                        "title": hit.title,
                        "url": hit.url,
                        "note": "已改为官方原文",
                    }
                )
                continue
            published = hit.published or ctx.today_cn
            passages.append(
                Passage(
                    key=f"web:{hit.provider}:{index}:{hit.url}",
                    title=hit.title,
                    publisher=hit.provider,
                    corpus_kind="web",
                    published=published,
                    content=hit.snippet,
                    url=hit.url,
                    verified=False,
                )
            )
            rows.append({"title": hit.title, "url": hit.url, "note": "未核实"})
        dates = [hit.published for hit in usable if hit.published is not None]
        return ToolResult(
            tool=self.name,
            title=query[:40],
            columns=(
                Column("title", "标题"),
                Column("url", "链接"),
                Column("note", "核对"),
            ),
            rows=tuple(rows),
            as_of=max(dates) if dates else ctx.today_cn,
            source="联网",
            passages=tuple(passages),
            excerpt=True,
        )

    def _official(self, ctx: ToolContext, hit: SearchHit) -> list[Passage]:
        code = _OFFICIAL.get((urlsplit(hit.url).hostname or "").lower())
        feed = FEEDS.get(code or "")
        if (
            feed is None
            or self._fetch_page is None
            and contact_email(get_settings().sec_user_agent) is None
        ):
            return []
        try:
            text = (
                self._fetch_page(hit.url)
                if self._fetch_page is not None
                else _fetch_official(feed, hit.url)
            )
        except PoliteError:
            return []
        if not str(text).strip():
            return []
        from app.services.cninfo_filings import store_filing
        from app.services.filing_meta import FilingHit
        from app.services.pdf_text import PageText

        repo = CorpusRepository(ctx.engine) if ctx.engine is not None else None
        published = datetime_of(hit.published or ctx.today_cn)
        filing = FilingHit("", hit.title, hit.url, published, "", None)
        if repo is not None:
            store_filing(
                repo,
                filing,
                [PageText(page_number=1, text=str(text))],
                instrument_id=None,
                source_id=feed.source_id,
                publisher=feed.publisher,
                license_note="官方公开文本，仅作研究引用，保留原文链接",
                kind="official",
                doc_type=feed.doc_type,
            )
            stored = repo.latest_passages(source_id=feed.source_id, url=hit.url, limit=4)
            if stored:
                return [_passage(item) for item in stored]
        return [
            Passage(
                key=f"official:{hit.url}",
                title=hit.title,
                publisher=feed.publisher,
                corpus_kind="official",
                published=hit.published or ctx.today_cn,
                content=str(text)[:1200],
                url=hit.url,
                verified=True,
            )
        ]


def datetime_of(day: date) -> Any:
    from datetime import datetime

    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _settings(ctx: ToolContext) -> Settings:
    if ctx.engine is None:
        return get_settings()
    return effective_settings(ctx.engine)


def _allow(user_id: Any, today: date, limit: int) -> bool:
    if limit <= 0:
        return False
    key = str(user_id)
    with _LOCK:
        day, count = _USED.get(key, (today, 0))
        if day != today:
            count = 0
        if count >= limit:
            return False
        _USED[key] = (today, count + 1)
        return True


def _live_search(query: str, settings: Settings) -> list[SearchHit]:
    chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
    order = (("bocha", settings.bocha_api_key), ("brave", settings.brave_api_key))
    if not chinese:
        order = (("brave", settings.brave_api_key), ("bocha", settings.bocha_api_key))
    with httpx.Client(timeout=15) as client:
        for name, key in order:
            if not key.strip():
                continue
            try:
                if name == "brave":
                    url, headers = brave_request(query)
                    headers = {**headers, "X-Subscription-Token": key.strip()}
                    payload = client.get(url, headers=headers)
                    if payload.status_code == 200:
                        return [hit for hit in brave_hits(payload.content) if hit.url]
                else:
                    payload = client.post(
                        BOCHA_URL,
                        content=bocha_body(query),
                        headers={
                            "Authorization": f"Bearer {key.strip()}",
                            "Content-Type": "application/json",
                        },
                    )
                    if payload.status_code == 200:
                        return [hit for hit in bocha_hits(payload.content) if hit.url]
            except httpx.HTTPError:
                continue
    return []


def _fetch_official(feed: OfficialFeed, url: str) -> str:
    from app.services.html_text import article_text

    email = contact_email(get_settings().sec_user_agent)
    if email is None:
        return ""
    data = live_client(email, feed.hosts).get(url)
    return article_text(data, element_id=feed.element_id, css_class=feed.css_class)
