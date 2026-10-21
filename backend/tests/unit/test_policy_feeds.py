"""深交所本所要闻、上交所监管动态、国务院最新政策、证监会要闻：列表解析、正文容器、发布时间、
robots 处理、迁移与调度的一致性。

夹具是按真实页面（2026-10-21 核对）的结构缩写的：只留结构和几条标题，正文是自拟的短句，
不含整页的真实版权全文。全部离线，不联网。
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.providers.errors import ProviderError
from app.providers.polite import Exchange, PoliteClient, user_agent_for
from app.repositories.corpus import (
    CSRC_NEWS_SOURCE,
    GOV_POLICY_SOURCE,
    SSE_SUPERVISION_SOURCE,
    SZSE_NEWS_SOURCE,
    NewChunk,
    NewDocument,
)
from app.services import official_texts, scheduler
from app.services.html_text import article_text
from app.services.official_texts import FEEDS, parse_listing, sync_official
from app.services.policy_lists import (
    CSRC_HOME,
    GOV_LIST_LIMIT,
    GOV_POLICY_LIST,
    SSE_SUPERVISION_PAGE,
    SZSE_NEWS_PAGE,
    day_start_in_shanghai,
    page_published_at,
    parse_csrc_home_news,
    parse_gov_policy_list,
    parse_sse_list,
    parse_szse_news_list,
)
from sqlalchemy import Engine

NOW = datetime(2026, 10, 21, 6, tzinfo=UTC)
READY = Settings(sec_user_agent="FinScope ops@finscope.test")
SZSE = "https://www.szse.cn"
SSE = "https://www.sse.com.cn"


# ---------------------------------------------------------------- 夹具
def _szse_item(href: str, title: str, day: str | None) -> str:
    stamp = f'<span class="time">\n  {day}\n</span>' if day else ""
    return f"""
<li>
  <div class="title">
    <script>
      var curHref = '{href}';
      //var curTitle = '被注释掉的旧标题，不能取';
      var curTitle ='{title}';
      var curCmsDocType = '20'
      document.write('<a class="art-list-link" href="' + curHref + '">' + curTitle + '</a>');
    </script>
    {stamp}
  </div>
</li>"""


def _szse_page(*items: str) -> bytes:
    return (
        '<html><head><script>var title = "本所要闻";</script></head><body>'
        '<ul class="menu"><li><a href="/other">导航</a></li></ul>'
        f'<div class="article-list"><ul class="newslist date-right">{"".join(items)}</ul></div>'
        "<script>var curHref = './t99999999_1.html'; var curTitle = '列表外的脚本';</script>"
        "</body></html>"
    ).encode()


FIRST_TITLE = "首批主动ETF正式获批，场内主动投资开启新篇章"
SZSE_PAGE = _szse_page(
    _szse_item("./t20261019_623374.html", FIRST_TITLE, "2026-10-19"),
    _szse_item("./t20261015_623313.html", "联亚药业在深交所上市", "2026-10-15"),
    _szse_item("./t20260928_623075.html", "关于聘任学术委员会委员的公告", "2026-09-28"),
)


def _sse_item(day: str | None, href: str | None, title: str | None, label: str = "") -> str:
    span = f"<span>\n  {day}\n</span>" if day else ""
    attrs = f' href="{href}"' if href else ""
    attrs += f' title="{title}"' if title else ""
    return f'<dd>{span}<a{attrs} target="_blank">\n  {label or title or ""}\n</a></dd>'


def _sse_page(*items: str) -> bytes:
    return (
        "<html><body><div class='user'><dl><dt>用户入口</dt>"
        '<dd><a href="//one.sse.com.cn/investor/">投资者</a></dd></dl></div>'
        '<div class="sse_list_1 js_listPage" id="sse_list_1"><dl>'
        f"{''.join(items)}"
        "<script>createPageHTML('paging',6, 1,'s_list','shtml',89);</script>"
        "</dl></div></body></html>"
    ).encode()


_SSE_DIR = "/disclosure/credibility/supervision/dynamic/c"
SSE_TITLE_A = "关于向甲公司公告送达纪律处分意向书的通知"
SSE_TITLE_B = "关于向乙公司公告送达纪律处分意向书的通知"
SSE_PAGE = _sse_page(
    _sse_item("2024-02-22", f"{_SSE_DIR}/c_20240222_5735661.shtml", SSE_TITLE_A),
    _sse_item("2024-02-08", f"{_SSE_DIR}/c_20240208_5735501.shtml", SSE_TITLE_B),
)

SZSE_ARTICLE = (
    "<html><body><div class='header'>深交所 导航</div>"
    '<div class="news-detail-con"><h2 class="title">标题</h2>'
    '<div class="des-content" id="desContent"><p>近日，深交所首批主动 ETF 获批。</p>'
    "<p>下一步，深交所将做好发行上市工作。<br>稳妥推进。</p><img src='x.png'></div>"
    '<div class="share">分享到微信</div></div>'
    "<div class='footer'>版权所有 深圳证券交易所</div></body></html>"
).encode()
SSE_ARTICLE = (
    '<html><body><div class="article-infor"><h2>标题</h2>'
    '<div class="allZoom"><p>　　经查明，某公司涉嫌违反上市规则。</p>'
    '<p>　　附件：<a href="a.docx">意向书<br><br type="_moz"></a></p>'
    "<p>　　上海证券交易所上市公司管理二部</p></div>"
    '<div class="share">分享：微信扫一扫，分享好友</div></div>'
    "<footer>联系我们</footer></body></html>"
).encode()


# ---------------------------------------------------------------- 深交所列表
def test_szse_list_reads_title_link_and_date_from_the_inline_scripts() -> None:
    items = parse_szse_news_list(SZSE_PAGE, SZSE_NEWS_PAGE)
    assert [(item.title, item.url) for item in items] == [
        (FIRST_TITLE, f"{SZSE}/aboutus/trends/news/t20261019_623374.html"),
        ("联亚药业在深交所上市", f"{SZSE}/aboutus/trends/news/t20261015_623313.html"),
        ("关于聘任学术委员会委员的公告", f"{SZSE}/aboutus/trends/news/t20260928_623075.html"),
    ]
    # 只有日期：取当天 0 点（上海），即前一天 16:00 UTC。
    assert items[0].published_at == datetime(2026, 10, 18, 16, tzinfo=UTC)
    assert day_start_in_shanghai(datetime(2026, 10, 19).date()) == items[0].published_at
    assert items == parse_listing(FEEDS["szse_news"], SZSE_PAGE)


def test_szse_list_drops_records_without_a_date_and_never_borrows_the_next_one() -> None:
    page = _szse_page(
        _szse_item("./t20261019_1.html", "没有日期的一条", None),
        _szse_item("./t20261018_2.html", "有日期的一条", "2026-10-18"),
        _szse_item("./t20261017_3.html", "日期写坏了", "2026-13-45"),
        _szse_item("./t20261016_4.html", "外站链接", "2026-10-16").replace(
            "./t20261016_4.html", "https://evil.example/x.html"
        ),
        _szse_item("./t20261018_2.html", "重复的一条", "2026-10-18"),
    )
    items = parse_szse_news_list(page, SZSE_NEWS_PAGE)
    assert [item.title for item in items] == ["有日期的一条"]
    assert items[0].url == f"{SZSE}/aboutus/trends/news/t20261018_2.html"


def test_szse_list_ignores_pages_without_the_list_and_empty_lists() -> None:
    assert parse_szse_news_list(b"", SZSE_NEWS_PAGE) == ()
    assert parse_szse_news_list(_szse_page(), SZSE_NEWS_PAGE) == ()
    assert parse_szse_news_list("<html><body>验证码</body></html>".encode(), SZSE_NEWS_PAGE) == ()
    # 列表外的脚本和日期不算。
    outside = (
        "<script>var curHref = './a.html'; var curTitle = 'x';</script>"
        "<span class='time'>2026-10-01</span>"
    )
    assert parse_szse_news_list(outside.encode(), SZSE_NEWS_PAGE) == ()


def test_szse_titles_keep_escaped_quotes_and_entities() -> None:
    page = _szse_page(_szse_item("./t20261019_9.html", r"深交所\'新\'规 &amp; 解读", "2026-10-19"))
    assert [item.title for item in parse_szse_news_list(page, SZSE_NEWS_PAGE)] == [
        "深交所'新'规 & 解读"
    ]


# ---------------------------------------------------------------- 上交所列表
def test_sse_list_reads_dated_links_and_skips_navigation() -> None:
    items = parse_sse_list(SSE_PAGE, SSE_SUPERVISION_PAGE)
    assert [(item.title, item.url) for item in items] == [
        (SSE_TITLE_A, f"{SSE}{_SSE_DIR}/c_20240222_5735661.shtml"),
        (SSE_TITLE_B, f"{SSE}{_SSE_DIR}/c_20240208_5735501.shtml"),
    ]
    assert items[0].published_at == datetime(2024, 2, 21, 16, tzinfo=UTC)
    assert items == parse_listing(FEEDS["sse_supervision"], SSE_PAGE)


def test_sse_list_drops_incomplete_records_and_falls_back_to_the_link_text() -> None:
    page = _sse_page(
        _sse_item(None, f"{_SSE_DIR}/c_1.shtml", "没有日期"),
        _sse_item("2024-02-01", None, None, label="没有链接"),
        _sse_item("2024-13-01", f"{_SSE_DIR}/c_2.shtml", "日期写坏了"),
        _sse_item("2024-02-02", "//one.sse.com.cn/x/", "外站"),
        _sse_item("2024-02-03", f"{_SSE_DIR}/c_3.shtml", None, label="没有 title 属性的一条"),
        _sse_item("2024-02-03", f"{_SSE_DIR}/c_3.shtml", "重复"),
    )
    items = parse_sse_list(page, SSE_SUPERVISION_PAGE)
    assert [item.title for item in items] == ["没有 title 属性的一条"]


def test_sse_list_handles_empty_and_foreign_pages() -> None:
    assert parse_sse_list(b"", SSE_SUPERVISION_PAGE) == ()
    assert parse_sse_list(_sse_page(), SSE_SUPERVISION_PAGE) == ()
    assert parse_sse_list(b"<html>access denied</html>", SSE_SUPERVISION_PAGE) == ()


# ---------------------------------------------------------------- 正文容器
def test_szse_article_container_and_missing_container() -> None:
    text = article_text(SZSE_ARTICLE, element_id=FEEDS["szse_news"].element_id)
    assert text == "近日，深交所首批主动 ETF 获批。\n下一步，深交所将做好发行上市工作。\n稳妥推进。"
    assert "分享" not in text and "版权" not in text
    redesigned = "<html><body><p>改版后的页面</p></body></html>".encode()
    assert article_text(redesigned, element_id="desContent") == ""


def test_sse_article_container_does_not_leak_the_footer_after_a_bare_br() -> None:
    text = article_text(SSE_ARTICLE, css_class=FEEDS["sse_supervision"].css_class)
    assert text.splitlines() == [
        "经查明，某公司涉嫌违反上市规则。",
        "附件：意向书",
        "上海证券交易所上市公司管理二部",
    ]
    assert article_text(b"<div class='other'>x</div>", css_class="allZoom") == ""


def test_void_elements_do_not_stretch_a_container() -> None:
    html = '<div id="x">甲<br>乙<img src="a.png">丙<hr>丁<br/>戊</div><div>页脚</div>'.encode()
    assert article_text(html, element_id="x") == "甲\n乙丙丁\n戊"


# ---------------------------------------------------------------- 来源登记
def test_new_feeds_are_registered_and_respect_robots() -> None:
    szse, sse = FEEDS["szse_news"], FEEDS["sse_supervision"]
    assert (szse.source_id, szse.hosts, szse.doc_type) == (
        SZSE_NEWS_SOURCE,
        frozenset({"www.szse.cn"}),
        "news",
    )
    assert (sse.source_id, sse.hosts, sse.doc_type) == (
        SSE_SUPERVISION_SOURCE,
        frozenset({"www.sse.com.cn"}),
        "news",
    )
    assert szse.respect_robots and sse.respect_robots
    assert szse.list_url == SZSE_NEWS_PAGE and sse.list_url == SSE_SUPERVISION_PAGE
    # 已有的来源行为不变：不额外读 robots.txt。
    assert not any(FEEDS[code].respect_robots for code in FEEDS if code.startswith(("fed_", "nbs")))


def test_feed_source_ids_are_unique_and_match_the_migrations() -> None:
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for name in (
            "20261021_0032_official_sources.py",
            "20261022_0033_policy_sources.py",
            "20261023_0034_gov_csrc_sources.py",
        )
        for path in [versions / name]
    )
    rows = dict(re.findall(r"'(00000000-0000-4000-8000-[0-9a-f]{12})',\s*'([a-z_]+)'", sql))
    assert len({str(feed.source_id) for feed in FEEDS.values()}) == len(FEEDS)
    for code, feed in FEEDS.items():
        assert rows.get(str(feed.source_id)) == code, f"{code} 的 UUID 不在 0032–0034 里"
        assert all(f"'{host}'" in sql for host in feed.hosts), f"{code} 的主机没有写进迁移"
    assert set(rows.values()) == set(FEEDS)  # 迁移里没有多出 FEEDS 里不存在的来源


def test_migration_0033_chains_after_0032_and_can_be_undone() -> None:
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    source = (versions / "20261022_0033_policy_sources.py").read_text(encoding="utf-8")
    assert 'revision: str = "20261022_0033"' in source
    assert 'down_revision: str | None = "20261021_0032"' in source
    assert "def downgrade" in source and "DELETE FROM corpus_sources" in source
    # 其他迁移没有再接在 0032 后面：迁移链仍是单链。
    followers = [
        path.name
        for path in versions.glob("*.py")
        if 'down_revision: str | None = "20261021_0032"' in path.read_text(encoding="utf-8")
    ]
    assert followers == ["20261022_0033_policy_sources.py"]


def test_migration_0034_chains_after_0033_and_can_be_undone() -> None:
    versions = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    source = (versions / "20261023_0034_gov_csrc_sources.py").read_text(encoding="utf-8")
    assert 'revision: str = "20261023_0034"' in source
    assert 'down_revision: str | None = "20261022_0033"' in source
    assert "def downgrade" in source and "DELETE FROM corpus_sources" in source
    followers = [
        path.name
        for path in versions.glob("*.py")
        if 'down_revision: str | None = "20261022_0033"' in path.read_text(encoding="utf-8")
    ]
    assert followers == ["20261023_0034_gov_csrc_sources.py"]


def test_the_scheduler_registers_every_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "_provider_id", lambda _engine, **_kw: UUID(int=7))
    tasks = scheduler.build_official_text_tasks(cast(Engine, None), READY, NOW)
    assert [task.scope["source"] for task in tasks] == list(FEEDS)
    assert {"szse_news", "sse_supervision"} <= {task.scope["source"] for task in tasks}
    assert len({task.dedupe_key for task in tasks}) == len(tasks)
    assert {task.job_type for task in tasks} == {"corpus_official"}
    assert all(task.unavailable_reason is None for task in tasks)


# ---------------------------------------------------------------- sync_official（假站点、假语料库）
class _Site:
    """按地址给响应的假站点，记录每次请求。"""

    def __init__(self, pages: dict[str, tuple[int, bytes]]) -> None:
        self.pages = pages
        self.requests: list[str] = []

    def __call__(self, method: str, url: str, headers: object, content: object) -> Exchange:
        self.requests.append(url)
        status, body = self.pages.get(url, (404, b"not found"))
        return Exchange(status, {}, body)


class _Repo:
    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()
        self.saved: list[tuple[NewDocument, list[NewChunk]]] = []

    def latest_passages(self, *, source_id: UUID, url: str, limit: int = 8) -> list[Any]:
        return [object()] if url in self.known else []

    def save(self, document: NewDocument, chunks: list[NewChunk]) -> UUID | None:
        self.saved.append((document, list(chunks)))
        self.known.add(document.canonical_url)
        return uuid4()


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Callable[[_Site, _Repo], None]:
    def install(site: _Site, repo: _Repo) -> None:
        monkeypatch.setattr(official_texts, "get_settings", lambda: READY)
        monkeypatch.setattr(official_texts, "CorpusRepository", lambda _engine: repo)
        monkeypatch.setattr(
            official_texts,
            "live_client",
            lambda email, hosts: PoliteClient(
                user_agent=user_agent_for(email),
                allowed_hosts=hosts,
                opener=site,
                resolve=lambda _host: ("1.1.1.1",),
                sleeper=lambda _seconds: None,
                clock=lambda: 0,
            ),
        )

    return install


ARTICLE_URLS = [
    f"{SZSE}/aboutus/trends/news/t20261019_623374.html",
    f"{SZSE}/aboutus/trends/news/t20261015_623313.html",
    f"{SZSE}/aboutus/trends/news/t20260928_623075.html",
]
ROBOTS = f"{SZSE}/robots.txt"


def _szse_site(robots: tuple[int, bytes] = (200, b""), **overrides: tuple[int, bytes]) -> _Site:
    pages: dict[str, tuple[int, bytes]] = {
        ROBOTS: robots,
        SZSE_NEWS_PAGE: (200, SZSE_PAGE),
        **{url: (200, SZSE_ARTICLE) for url in ARTICLE_URLS},
    }
    pages.update({key: value for key, value in overrides.items()})
    return _Site(pages)


def _engine() -> Engine:
    return cast(Engine, None)


def test_sync_stores_new_articles_with_publisher_type_and_source(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site, repo = _szse_site(), _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "szse_news") == 3
    assert site.requests[0] == ROBOTS  # 先读 robots.txt
    assert site.requests[1] == SZSE_NEWS_PAGE
    document, chunks = repo.saved[0]
    assert (document.source_id, document.kind, document.doc_type) == (
        SZSE_NEWS_SOURCE,
        "official",
        "news",
    )
    assert document.canonical_url == ARTICLE_URLS[0]
    assert document.published_at == datetime(2026, 10, 18, 16, tzinfo=UTC)
    assert document.license_note == "官方公开文本，仅作研究引用，保留原文链接"
    assert chunks and "首批主动 ETF 获批" in chunks[0].content


def test_sync_is_idempotent_and_respects_the_per_run_limit(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site, repo = _szse_site(), _Repo(known={ARTICLE_URLS[1]})
    wire(site, repo)
    assert sync_official(_engine(), "szse_news", limit=1) == 1
    assert [doc.canonical_url for doc, _ in repo.saved] == [ARTICLE_URLS[0]]
    assert ARTICLE_URLS[2] not in site.requests  # 到上限就停，后面的不抓
    site.requests.clear()
    assert sync_official(_engine(), "szse_news") == 1  # 只剩第三篇没入库
    assert [doc.canonical_url for doc, _ in repo.saved][-1] == ARTICLE_URLS[2]
    site.requests.clear()
    assert sync_official(_engine(), "szse_news") == 0
    assert not any(url in site.requests for url in ARTICLE_URLS)  # 已入库的不再请求正文


def test_sync_stops_when_robots_forbids_the_list_page(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site = _szse_site(robots=(200, b"User-agent: *\nDisallow: /aboutus/\n"))
    repo = _Repo()
    wire(site, repo)
    with pytest.raises(ProviderError) as caught:
        sync_official(_engine(), "szse_news")
    assert (caught.value.code, caught.value.status_code) == ("UPSTREAM_BLOCKED", 403)
    assert "robots.txt" in caught.value.message
    assert site.requests == [ROBOTS]  # 列表页和正文都没请求
    assert repo.saved == []


@pytest.mark.parametrize("status", [401, 403, 500])
def test_an_unreadable_robots_file_is_treated_as_forbidden(
    wire: Callable[[_Site, _Repo], None], status: int
) -> None:
    site, repo = _szse_site(robots=(status, b"WAF")), _Repo()
    wire(site, repo)
    with pytest.raises(ProviderError) as caught:
        sync_official(_engine(), "szse_news")
    assert caught.value.code == "UPSTREAM_BLOCKED"
    assert site.requests == [ROBOTS] and repo.saved == []


def test_a_missing_robots_file_means_no_restriction(wire: Callable[[_Site, _Repo], None]) -> None:
    site, repo = _szse_site(robots=(404, b"")), _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "szse_news") == 3


def test_a_forbidden_article_path_skips_only_that_article(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    robots = b"User-agent: *\nDisallow: /aboutus/trends/news/t20261015\n"
    site, repo = _szse_site(robots=(200, robots)), _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "szse_news") == 2
    assert [doc.canonical_url for doc, _ in repo.saved] == [ARTICLE_URLS[0], ARTICLE_URLS[2]]
    assert ARTICLE_URLS[1] not in site.requests


def test_sync_skips_pages_whose_container_is_missing_and_empty_lists(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    redesigned = "<html><body><div class='new-layout'>改版</div></body></html>".encode()
    site = _szse_site(**{url: (200, redesigned) for url in ARTICLE_URLS})
    repo = _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "szse_news") == 0 and repo.saved == []

    empty = _szse_site(**{SZSE_NEWS_PAGE: (200, _szse_page())})
    wire(empty, repo)
    assert sync_official(_engine(), "szse_news") == 0
    assert empty.requests == [ROBOTS, SZSE_NEWS_PAGE]


def test_a_403_on_an_article_stops_the_source_like_before(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site = _szse_site(**{ARTICLE_URLS[0]: (403, b"forbidden")})
    wire(site, _Repo())
    with pytest.raises(ProviderError) as caught:
        sync_official(_engine(), "szse_news")
    assert (caught.value.code, caught.value.status_code) == ("UPSTREAM_BLOCKED", 403)


def test_sse_source_syncs_with_its_own_container(wire: Callable[[_Site, _Repo], None]) -> None:
    pages: dict[str, tuple[int, bytes]] = {
        f"{SSE}/robots.txt": (404, b"not found"),
        SSE_SUPERVISION_PAGE: (200, SSE_PAGE),
        f"{SSE}{_SSE_DIR}/c_20240222_5735661.shtml": (200, SSE_ARTICLE),
        f"{SSE}{_SSE_DIR}/c_20240208_5735501.shtml": (200, SSE_ARTICLE),
    }
    site, repo = _Site(pages), _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "sse_supervision") == 2
    document, chunks = repo.saved[0]
    assert (document.source_id, document.doc_type) == (SSE_SUPERVISION_SOURCE, "news")
    assert "分享" not in chunks[0].content and "经查明" in chunks[0].content


def test_existing_feeds_do_not_read_robots(wire: Callable[[_Site, _Repo], None]) -> None:
    rss = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>Speech</title>'
        b"<link>https://www.federalreserve.gov/newsevents/speech/a.htm</link>"
        b"<pubDate>Mon, 20 Oct 2026 14:00:00 GMT</pubDate></item></channel></rss>"
    )
    article = b'<div id="article"><p>The Committee is attentive to risks on both sides.</p></div>'
    site = _Site(
        {
            "https://www.federalreserve.gov/feeds/speeches.xml": (200, rss),
            "https://www.federalreserve.gov/newsevents/speech/a.htm": (200, article),
        }
    )
    wire(site, _Repo())
    assert sync_official(_engine(), "fed_speeches") == 1
    assert not any(url.endswith("robots.txt") for url in site.requests)


def test_unknown_source_and_missing_contact_are_reported(
    wire: Callable[[_Site, _Repo], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ProviderError) as unknown:
        sync_official(_engine(), "no_such_source")
    assert unknown.value.code == "VALIDATION_ERROR"
    wire(_szse_site(), _Repo())
    monkeypatch.setattr(official_texts, "get_settings", lambda: Settings())
    with pytest.raises(ProviderError) as missing:
        sync_official(_engine(), "szse_news")
    assert missing.value.code == "DATA_UNAVAILABLE"


# ---------------------------------------------------------------- 国务院「最新政策」、证监会要闻
GOV = "https://www.gov.cn"
CSRC = "https://www.csrc.gov.cn"
GOV_A = f"{GOV}/zhengce/202610/content_7083930.htm"
GOV_B = f"{GOV}/zhengce/content/202610/content_7082737.htm"
GOV_TITLE = "中办国办关于推动科技创新和产业创新深度融合的意见"


def _gov_row(title: str, url: str, day: str) -> dict[str, str]:
    return {"TITLE": title, "SUB_TITLE": "", "URL": url, "DOCRELPUBTIME": day}


GOV_JSON = json.dumps(
    [
        _gov_row(GOV_TITLE, GOV_A, "2026-10-21"),
        _gov_row("国务院关于某行动计划的批复", GOV_B, "2026-10-08"),
        _gov_row("一条旧的新闻稿", f"{GOV}/xinwen/2020-01/05/content_1.htm", "2020-01-05"),
        _gov_row("", f"{GOV}/zhengce/202610/content_2.htm", "2026-10-07"),
        _gov_row("没有日期的条目", f"{GOV}/zhengce/202610/content_3.htm", ""),
        "不是对象的一行",
    ],
    ensure_ascii=False,
).encode()


def _gov_article(published: str | None) -> bytes:
    meta = f'<meta name="firstpublishedtime" content="{published}">' if published else ""
    return (
        f"<html><head>{meta}</head><body><div class='header'>中国政府网 导航</div>"
        '<div class="pages_content" id="UCAP-CONTENT">'
        "<p>为推动科技创新和产业创新深度融合，现提出如下意见。</p></div>"
        "<div class='footer'>版权所有 中国政府网</div></body></html>"
    ).encode()


# 真实文件以 UTF-8 BOM 开头。
GOV_ROBOTS = b"\xef\xbb\xbfUser-agent: * \nAllow: /1\nDisallow:/2016gov/\nDisallow:/premier/\n"
CSRC_A = f"{CSRC}/csrc/c100028/c7665462/content.shtml"
CSRC_B = f"{CSRC}/csrc/c100028/c7663814/content.shtml"
CSRC_TITLE = "中国证监会就《证券市场先行赔付指导意见》公开征求意见"
CSRC_HOME_PAGE = (
    "<html><body>"
    '<div class="nav"><a href="/csrc/c100028/common_list.shtml">证监会要闻</a></div><ul>'
    f'<li><a href="/csrc/c100028/c7665462/content.shtml" target="_blank" >{CSRC_TITLE}</a></li>'
    '<li><a href="/csrc/c100028/c7663814/content.shtml">中国证监会召开资本市场和金融形势'
    "专家座谈会</a></li>"
    f'<li><a href="/csrc/c100028/c7665462/content.shtml">{CSRC_TITLE}</a></li>'
    '<li><a href="/csrc/c101928/c7665091/content.shtml">行政处罚决定书</a></li>'
    '<li><a href="https://example.com/csrc/c100028/c1/content.shtml">外站链接</a></li>'
    '<li><a href="/csrc/c100028/c7660000/content.shtml"> </a></li>'
    "</ul></body></html>"
).encode()


def _csrc_article(published: str | None) -> bytes:
    meta = f'<meta name="PubDate" content="{published}"/>' if published else ""
    return (
        f'<html><head><meta name="ColumnName" content="证监会要闻"/>{meta}</head><body>'
        '<div class="content"><div class="detail-news">'
        "<p>为进一步保护投资者合法权益，中国证监会起草了指导意见。</p></div></div>"
        "<div class='foot'>网站地图</div></body></html>"
    ).encode()


class _RedirectingSite(_Site):
    """在假站点上加几条重定向（地址 → 跳转目标）。"""

    def __init__(self, pages: dict[str, tuple[int, bytes]], redirects: dict[str, str]) -> None:
        super().__init__(pages)
        self.redirects = redirects

    def __call__(self, method: str, url: str, headers: object, content: object) -> Exchange:
        if url in self.redirects:
            self.requests.append(url)
            return Exchange(302, {"location": self.redirects[url]}, b"")
        return super().__call__(method, url, headers, content)


def test_gov_list_reads_the_json_newest_first_and_keeps_only_policy_pages() -> None:
    items = parse_gov_policy_list(GOV_JSON, GOV_POLICY_LIST)
    assert [item.url for item in items] == [GOV_A, GOV_B]
    assert items[0].title == GOV_TITLE
    assert items[0].published_at == day_start_in_shanghai(datetime(2026, 10, 21).date())


@pytest.mark.parametrize("payload", [b"", b"<html>not json</html>", b'{"TITLE": "x"}', b"\xff\xfe"])
def test_gov_list_that_is_not_a_json_array_gives_no_items(payload: bytes) -> None:
    assert parse_gov_policy_list(payload, GOV_POLICY_LIST) == ()


def test_gov_list_only_looks_at_the_newest_rows() -> None:
    rows = [
        _gov_row(f"政策 {n}", f"{GOV}/zhengce/202610/content_{n}.htm", "2026-10-01")
        for n in range(GOV_LIST_LIMIT + 30)
    ]
    items = parse_gov_policy_list(json.dumps(rows).encode(), GOV_POLICY_LIST)
    assert len(items) == GOV_LIST_LIMIT and items[0].title == "政策 0"


def test_csrc_home_gives_the_news_column_links_without_dates() -> None:
    items = parse_csrc_home_news(CSRC_HOME_PAGE, CSRC_HOME)
    assert [item.url for item in items] == [CSRC_A, CSRC_B]  # 去重；其他栏目、外站、空标题不要
    assert items[0].title == CSRC_TITLE
    assert all(item.published_at is None for item in items)
    assert parse_csrc_home_news("<html><body>改版</body></html>".encode(), CSRC_HOME) == ()


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (_gov_article("2026-10-21-18:17:00"), datetime(2026, 10, 21, 10, 17, tzinfo=UTC)),
        (_csrc_article("2026-10-19 19:03:25"), datetime(2026, 10, 19, 11, 3, 25, tzinfo=UTC)),
        (_csrc_article("2026-10-19"), datetime(2026, 10, 18, 16, tzinfo=UTC)),
        (_csrc_article("2026-13-40 10:00:00"), None),
        (_csrc_article("待定"), None),
        (_csrc_article(None), None),
    ],
)
def test_page_time_is_read_from_the_named_meta_in_shanghai_time(
    page: bytes, expected: datetime | None
) -> None:
    name = "firstpublishedtime" if b"firstpublishedtime" in page else "pubdate"  # 名字不分大小写
    assert page_published_at(page, name) == expected


def test_gov_and_csrc_feeds_are_registered_with_robots_and_page_times() -> None:
    gov, csrc = FEEDS["gov_policy"], FEEDS["csrc_news"]
    assert (gov.source_id, gov.hosts, gov.doc_type, gov.list_url) == (
        GOV_POLICY_SOURCE,
        frozenset({"www.gov.cn"}),
        "policy",
        GOV_POLICY_LIST,
    )
    assert (csrc.source_id, csrc.hosts, csrc.doc_type, csrc.list_url) == (
        CSRC_NEWS_SOURCE,
        frozenset({"www.csrc.gov.cn"}),
        "news",
        CSRC_HOME,
    )
    assert gov.respect_robots and csrc.respect_robots
    assert (gov.time_meta, csrc.time_meta) == ("firstpublishedtime", "PubDate")


def _gov_site(article_a: bytes, article_b: bytes) -> _Site:
    return _Site(
        {
            f"{GOV}/robots.txt": (200, GOV_ROBOTS),
            GOV_POLICY_LIST: (200, GOV_JSON),
            GOV_A: (200, article_a),
            GOV_B: (200, article_b),
        }
    )


def test_gov_sync_takes_the_exact_time_from_the_page_when_it_is_the_same_day(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    # 第二篇的页面时间和列表日期对不上：信列表的日期，不信页面。
    site = _gov_site(_gov_article("2026-10-21-18:17:00"), _gov_article("2026-09-30-10:00:00"))
    repo = _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "gov_policy") == 2
    assert site.requests[:2] == [f"{GOV}/robots.txt", GOV_POLICY_LIST]
    first, second = (document for document, _ in repo.saved)
    assert (first.source_id, first.kind, first.doc_type) == (
        GOV_POLICY_SOURCE,
        "official",
        "policy",
    )
    assert first.published_at == datetime(2026, 10, 21, 10, 17, tzinfo=UTC)
    assert second.published_at == day_start_in_shanghai(datetime(2026, 10, 8).date())
    assert "科技创新和产业创新深度融合" in repo.saved[0][1][0].content
    assert "版权所有" not in repo.saved[0][1][0].content


def test_gov_sync_without_page_time_keeps_the_list_date(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site, repo = _gov_site(_gov_article(None), _gov_article(None)), _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "gov_policy") == 2
    assert repo.saved[0][0].published_at == day_start_in_shanghai(datetime(2026, 10, 21).date())


def test_gov_sync_stops_when_robots_forbids_the_policy_section(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site = _gov_site(_gov_article(None), _gov_article(None))
    site.pages[f"{GOV}/robots.txt"] = (200, b"\xef\xbb\xbfUser-agent: *\nDisallow: /zhengce/\n")
    wire(site, _Repo())
    with pytest.raises(ProviderError) as caught:
        sync_official(_engine(), "gov_policy")
    assert caught.value.code == "UPSTREAM_BLOCKED"
    assert site.requests == [f"{GOV}/robots.txt"]


def _csrc_site(article_a: bytes, article_b: bytes) -> _RedirectingSite:
    # 证监会没有 robots.txt：请求会跳到首页，首页 HTML 里没有任何规则，等于不限制。
    return _RedirectingSite(
        {CSRC_HOME: (200, CSRC_HOME_PAGE), CSRC_A: (200, article_a), CSRC_B: (200, article_b)},
        {f"{CSRC}/robots.txt": CSRC_HOME},
    )


def test_csrc_sync_dates_articles_by_their_page_and_skips_undated_ones(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site = _csrc_site(_csrc_article("2026-10-19 19:03:25"), _csrc_article(None))
    repo = _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "csrc_news") == 1
    assert site.requests[:3] == [f"{CSRC}/robots.txt", CSRC_HOME, CSRC_HOME]
    document, chunks = repo.saved[0]
    assert (document.source_id, document.doc_type, document.canonical_url) == (
        CSRC_NEWS_SOURCE,
        "news",
        CSRC_A,
    )
    assert document.published_at == datetime(2026, 10, 19, 11, 3, 25, tzinfo=UTC)
    assert "保护投资者合法权益" in chunks[0].content and "网站地图" not in chunks[0].content
    assert CSRC_B in site.requests  # 读过正文，但正文页没有发布时间，所以没有入库


def test_csrc_sync_honours_a_real_robots_file_if_one_appears(
    wire: Callable[[_Site, _Repo], None],
) -> None:
    site = _Site(
        {
            f"{CSRC}/robots.txt": (200, b"User-agent: *\nDisallow: /csrc/c100028/\n"),
            CSRC_HOME: (200, CSRC_HOME_PAGE),
            CSRC_A: (200, _csrc_article("2026-10-19 19:03:25")),
        }
    )
    repo = _Repo()
    wire(site, repo)
    assert sync_official(_engine(), "csrc_news") == 0  # 首页可读，要闻正文路径被禁止，逐篇跳过
    assert CSRC_A not in site.requests and repo.saved == []
