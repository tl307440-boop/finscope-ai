"""`web_search`：库里没有的近期访谈、新闻、讲话，用联网检索补一句出处（知识库设计 §5.5）。

搜索结果只在本回合使用，不进语料库，也不写入回答记录。官方页面（美联储、统计局）
会改由我们自己的抓取器取原文并入库，那部分可以长期引用。
"""

import logging
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
    NO_KEY_REASON,
    QIANFAN_URL,
    SearchHit,
    SearchServiceError,
    bocha_body,
    bocha_hits,
    brave_hits,
    brave_request,
    describe_status,
    infer_freshness,
    qianfan_body,
    qianfan_failure,
    qianfan_hits,
    service_down_reason,
)
from app.repositories.corpus import CorpusRepository
from app.services.assistant.results import Column, Passage, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import _passage
from app.services.official_texts import FEEDS, OfficialFeed
from app.services.runtime_settings import effective_settings

logger = logging.getLogger(__name__)

# 每回合最多联网两次（中文一次、英文一次）。模型有时会按一周、一个月、中文、英文各查一遍。
MAX_SEARCHES_PER_TURN = 2
_TURN_LIMIT_REASON = (
    f"本回合已经联网检索 {MAX_SEARCHES_PER_TURN} 次，不要再查，直接用已有的资料回答；"
    "已有资料都没有结果时，如实说明网上没查到。"
)
_LABELS = {"bocha": "博查", "brave": "Brave", "baidu": "百度"}
_LOCK = threading.Lock()
_USED: dict[str, tuple[date, int]] = {}
# 只拦明显不能检索的内容。宏观、选举、政策问答不在这里，那些是投研问题。
_BLOCKED = ("儿童色情", "自制炸弹", "制作炸药")
_OFFICIAL = {
    "www.federalreserve.gov": "fed_press",
    "www.stats.gov.cn": "nbs_release",
    "www.szse.cn": "szse_news",
    "www.sse.com.cn": "sse_supervision",
    "www.gov.cn": "gov_policy",
    "www.csrc.gov.cn": "csrc_news",
}
# 这两个站上只有这些路径下的页面算对应来源的原文；政府网的新闻、服务页面照旧按网页摘要处理。
_OFFICIAL_PATHS = {"gov_policy": "/zhengce/", "csrc_news": "/csrc/"}


class WebSearchArgs(BaseModel):
    # 旧版本的参数里有 freshness。现在由系统按检索词里的时间词决定，模型再填也直接忽略。
    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "检索词，保留「前几天」「最近」「刚刚」这类时间词。中文问题用中文，英文来源用英文"
        ),
    )


class WebSearchTool:
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "在网上检索近期访谈、新闻、讲话报道。只在语料库和公告里没有原文时使用。"
        "检索词里的「前几天」「最近」「刚刚」会被系统换成发布时间范围，范围内没有结果时"
        f"自动改为不限时间再查。每回合最多查 {MAX_SEARCHES_PER_TURN} 次，不要换词重复查。"
        "返回的是网页摘要，没有被系统核对。回答第一句就要给出判断并写 [n]，"
        "说明这是报道不是官方原文。资料上的日期早于用户说的范围也要作答，并写明发布日期。"
        "不要因为没有逐字稿就说无法判断。结果不会保存，刷新后需要重新查。"
        "工具写明检索服务出错时，那是服务问题，不是网上没有这条消息。"
    )
    args_model: ClassVar[type[BaseModel]] = WebSearchArgs

    def __init__(self, search: Any = None, fetch_page: Any = None) -> None:
        self._search = search
        self._fetch_page = fetch_page

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, WebSearchArgs)
        query = args.query.strip()
        title = query[:40]
        if any(word in query for word in _BLOCKED):
            return unavailable(self.name, title, ctx.today_cn, "这个问题不能联网检索", "联网")
        if ctx.web_failure is not None:  # 本回合服务已经报错，再查也一样
            return unavailable(self.name, title, ctx.today_cn, ctx.web_failure, "联网")
        if ctx.web_searches >= MAX_SEARCHES_PER_TURN:
            return unavailable(self.name, title, ctx.today_cn, _TURN_LIMIT_REASON, "联网")
        settings: Settings | None = None
        if self._search is None:
            settings = _settings(ctx)
            if not settings.assistant_web_search_enabled:
                return unavailable(self.name, title, ctx.today_cn, "联网检索没有开通", "联网")
            if not _allow(ctx.user_id, ctx.today_cn, settings.assistant_web_daily_limit):
                return unavailable(self.name, title, ctx.today_cn, "今天的联网次数已经用完", "联网")
        ctx.web_searches += 1
        try:
            if settings is not None:
                hits, widened = _search_with_window(query, settings, infer_freshness(query))
            else:
                hits, widened = list(self._search(query)), False
        except SearchServiceError as exc:
            ctx.web_failure = exc.reason
            return unavailable(self.name, title, ctx.today_cn, exc.reason, "联网")
        usable = [
            hit for hit in hits if hit.url.startswith("https://") and hit.snippet and hit.title
        ]
        if not usable:
            return unavailable(
                self.name, title, ctx.today_cn, "检索服务正常，但没有查到可用的结果", "联网"
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
                        "published": (hit.published or ctx.today_cn).isoformat(),
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
            rows.append(
                {
                    "published": published.isoformat(),
                    "title": hit.title,
                    "url": hit.url,
                    "note": "未核实",
                }
            )
        dates = [hit.published for hit in usable if hit.published is not None]
        return ToolResult(
            tool=self.name,
            title=query[:40],
            columns=(
                Column("published", "发布日", "", "date"),
                Column("title", "标题"),
                Column("url", "链接"),
                Column("note", "核对"),
            ),
            rows=tuple(rows),
            as_of=max(dates) if dates else ctx.today_cn,
            source="联网",
            reason=_WIDENED if widened else None,
            passages=tuple(passages),
            excerpt=True,
        )

    def _official(self, ctx: ToolContext, hit: SearchHit) -> list[Passage]:
        parts = urlsplit(hit.url)
        code = _OFFICIAL.get((parts.hostname or "").lower())
        if code is not None and not parts.path.startswith(_OFFICIAL_PATHS.get(code, "/")):
            code = None
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


_WIDENED = (
    "限定的时间范围内没有结果，以下是不限时间的检索。"
    "回答时写明每条资料标题后的发布日期；早于用户说的范围也要按这些资料作答，不要说查不到。"
)


def _search_with_window(
    query: str,
    settings: Settings,
    freshness: str | None,
    live: Any = None,
) -> tuple[list[SearchHit], bool]:
    """限定时间经常把能用的报道滤成空结果。空了就再查一次不限时间。"""

    search = _live_search if live is None else live
    hits = list(search(query, settings, freshness))
    if hits or not freshness:
        return hits, False
    return list(search(query, settings, None)), True


def _live_search(
    query: str,
    settings: Settings,
    freshness: str | None = None,
    client: httpx.Client | None = None,
) -> list[SearchHit]:
    """按语言排好各家的先后，第一家有响应就用它。

    返回空列表表示「服务正常、没有匹配」；所有配置过的服务都出错（密钥、额度、网络）
    或者一个密钥都没有时抛 `SearchServiceError`，调用方要把它和「没有结果」分开说。
    """

    chinese = any("\u4e00" <= char <= "\u9fff" for char in query)
    bocha = ("bocha", settings.bocha_api_key)
    baidu = ("baidu", settings.baidu_qianfan_api_key)
    brave = ("brave", settings.brave_api_key)
    order = (bocha, baidu, brave) if chinese else (brave, bocha, baidu)
    configured = [(name, key.strip()) for name, key in order if key.strip()]
    if not configured:
        raise SearchServiceError(NO_KEY_REASON)
    failures: list[tuple[str, str]] = []
    http = client if client is not None else httpx.Client(timeout=15)
    try:
        for name, key in configured:
            try:
                response = _ask(http, name, key, query, freshness)
            except httpx.HTTPError as exc:
                logger.warning("web search failed: provider=%s error=%s", name, type(exc).__name__)
                failures.append((_LABELS[name], "网络超时或连接失败"))
                continue
            if response.status_code == 200:
                problem = qianfan_failure(response.content) if name == "baidu" else None
                if problem is not None:
                    logger.warning("web search failed: provider=%s detail=%s", name, problem)
                    failures.append((_LABELS[name], problem))
                    continue
                parse = {"brave": brave_hits, "bocha": bocha_hits, "baidu": qianfan_hits}[name]
                return [hit for hit in parse(response.content) if hit.url]
            logger.warning("web search failed: provider=%s status=%s", name, response.status_code)
            failures.append((_LABELS[name], describe_status(response.status_code)))
    finally:
        if client is None:
            http.close()
    raise SearchServiceError(service_down_reason(failures))


def _ask(
    client: httpx.Client, name: str, key: str, query: str, freshness: str | None
) -> httpx.Response:
    if name == "brave":
        url, headers = brave_request(query, freshness)
        return client.get(url, headers={**headers, "X-Subscription-Token": key})
    return client.post(
        QIANFAN_URL if name == "baidu" else BOCHA_URL,
        content=(qianfan_body if name == "baidu" else bocha_body)(query, freshness),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )


def _fetch_official(feed: OfficialFeed, url: str) -> str:
    from app.services.html_text import article_text

    email = contact_email(get_settings().sec_user_agent)
    if email is None:
        return ""
    data = live_client(email, feed.hosts).get(url, respect_robots=feed.respect_robots)
    return article_text(data, element_id=feed.element_id, css_class=feed.css_class)
