"""网页读取工具 `fetch_url`：站点白名单、robots、次数、正文截取、失败原因。不碰网络。"""

from collections.abc import Mapping
from typing import Any

import pytest
from app.providers.polite import Exchange, PoliteClient, PoliteError
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools import fetch as fetch_tools
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.fetch import MAX_FETCHES_PER_TURN, FetchUrlTool, Site

from live_fakes import context

PARAGRAPH = (
    "国务院办公厅近日印发通知，要求进一步加强集成电路产业链上下游协同，"
    "提升关键设备和材料的自主保障能力。"
)
GOV_URL = "https://www.gov.cn/zhengce/content/2026-10/20/content_1.htm"
MEDIA_URL = "https://finance.eastmoney.com/a/202610203901661225.html"


def _page(paragraphs: int, *, date_line: str = "发布时间：2026-10-20") -> bytes:
    body = "".join(f"<p>{PARAGRAPH}（第 {n} 段）</p>" for n in range(paragraphs))
    head = "<title>某通知</title>"
    html = f"<html><head>{head}</head><body><h1>某通知</h1><p>{date_line}</p>{body}</body></html>"
    return html.encode()


class Fetcher:
    """假的读取函数：记下被读的（地址, 站点），按预置内容或异常回应。"""

    def __init__(self, response: bytes | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, Site]] = []

    def __call__(self, url: str, site: Site) -> bytes:
        self.calls.append((url, site))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _run(fetcher: Any, url: str, ctx: ToolContext | None = None, **tool: Any) -> ToolResult:
    registry = ToolRegistry([FetchUrlTool(fetcher, **tool)])
    arguments = f'{{"url": "{url}"}}'
    return registry.execute("fetch_url", arguments, ctx or context()).result


def test_an_official_page_gives_text_passages_and_a_dated_row() -> None:
    fetcher = Fetcher(_page(60))
    result = _run(fetcher, GOV_URL)
    assert result.status == "ok" and fetcher.calls[0][0] == GOV_URL
    (row,) = result.rows
    assert (row["published"], row["publisher"], row["url"]) == (
        "2026-10-20",
        "中国政府网站",
        GOV_URL,
    )
    assert "官方网站原文" in str(row["note"]) and "日期取自页面标注" in str(row["note"])
    assert result.as_of.isoformat() == "2026-10-20" and result.excerpt
    assert result.passages and all(not passage.verified for passage in result.passages)
    assert all(passage.corpus_kind == "official" for passage in result.passages)
    assert all(len(passage.content) <= fetch_tools.CHUNK_CHARS for passage in result.passages)
    assert result.passages[0].section == f"第 1/{len(result.passages)} 段"
    assert result.passages[0].key.startswith(f"page:{GOV_URL}:")


def test_an_official_page_is_cut_at_the_official_limit_and_says_so() -> None:
    result = _run(Fetcher(_page(130)), GOV_URL)
    total = sum(len(passage.content) for passage in result.passages)
    assert total <= 6000 and len(result.passages) >= 3
    assert "只读了页面开头" in str(result.rows[0]["note"])


def test_a_media_page_gives_only_a_short_excerpt_in_one_passage() -> None:
    result = _run(Fetcher(_page(30)), MEDIA_URL)
    (passage,) = result.passages
    assert len(passage.content) <= 1500 and passage.corpus_kind == "web"
    assert passage.publisher == "东方财富" and passage.section is None
    note = str(result.rows[0]["note"])
    assert "媒体页面开头摘录" in note and "未核实" in note and "全文请看原文链接" in note


def test_a_page_without_a_date_is_dated_today_and_says_so() -> None:
    result = _run(
        Fetcher(_page(5, date_line="本通知自印发之日起施行，由国务院办公厅负责解释。")), GOV_URL
    )
    assert result.rows[0]["published"] == "2026-10-21"
    assert "页面没有标注日期" in str(result.rows[0]["note"])


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/news",
        "https://www.gov.cn.evil.example/a.html",
        "https://evilgov.cn/a.html",
        "https://notsec.gov.evil.com/a",
        "https://127.0.0.1/a",
        "https://user:pass@www.gov.cn/a.html",
        "https://www.gov.cn:8443/a.html",
        "ftp://www.gov.cn/a.html",
        "https://www.gov.cn/a b.html",
    ],
)
def test_sites_outside_the_whitelist_or_with_odd_urls_are_refused_without_a_request(
    url: str,
) -> None:
    fetcher = Fetcher(_page(5))
    result = _run(fetcher, url)
    assert result.status == "unavailable" and fetcher.calls == []


def test_the_refusal_names_the_host_and_the_allowed_sites() -> None:
    result = _run(Fetcher(_page(5)), "https://example.com/news")
    assert "example.com" in (result.reason or "") and "美联储" in (result.reason or "")


def test_http_is_upgraded_to_https_and_fragments_are_dropped() -> None:
    fetcher = Fetcher(_page(5))
    result = _run(fetcher, "http://www.gov.cn/zhengce/a.htm#top")
    assert result.status == "ok"
    assert fetcher.calls[0][0] == "https://www.gov.cn/zhengce/a.htm"


def test_subdomains_of_whitelisted_suffixes_are_allowed_and_classified() -> None:
    fetcher = Fetcher(_page(5))
    _run(fetcher, "https://www.miit.gov.cn/xwfb/a.html")
    _run(fetcher, "https://www.sec.gov/Archives/edgar/data/1/a.htm")
    assert [site.suffix for _, site in fetcher.calls] == ["gov.cn", "sec.gov"]


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ROBOTS", "robots.txt 不允许"),
        ("BLOCKED", "拒绝了访问"),
        ("HOST", "跳转到了名单以外"),
        ("TIMEOUT", "响应超时"),
        ("TOO_LARGE", "页面太大"),
        ("DNS", "解析不了"),
        ("UPSTREAM", "页面读取失败：上游返回 404"),
    ],
)
def test_every_fetch_failure_is_explained_in_chinese(code: str, expected: str) -> None:
    result = _run(Fetcher(PoliteError(code, "上游返回 404")), GOV_URL)
    assert result.status == "unavailable" and expected in (result.reason or "")


def test_pdf_files_are_not_read() -> None:
    result = _run(Fetcher(b"%PDF-1.7\n..."), GOV_URL)
    assert result.status == "unavailable" and "PDF" in (result.reason or "")


@pytest.mark.parametrize(
    "page",
    [b"", b'<html><body><div id="app"></div><script src="/a.js"></script></body></html>'],
)
def test_pages_without_readable_text_say_why_instead_of_returning_nothing(page: bytes) -> None:
    result = _run(Fetcher(page), GOV_URL)
    assert result.status == "unavailable" and "没有读到正文" in (result.reason or "")


def test_a_turn_can_read_only_a_few_pages() -> None:
    fetcher = Fetcher(_page(5))
    ctx = context()
    results = [_run(fetcher, GOV_URL, ctx) for _ in range(MAX_FETCHES_PER_TURN + 1)]
    assert [result.status for result in results[:-1]] == ["ok"] * MAX_FETCHES_PER_TURN
    assert results[-1].status == "unavailable" and "不要再读" in (results[-1].reason or "")
    assert len(fetcher.calls) == MAX_FETCHES_PER_TURN


def test_without_a_real_contact_email_nothing_is_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setattr(
        fetch_tools, "get_settings", lambda: Settings(sec_user_agent="FinScope you@example.com")
    )
    result = (
        ToolRegistry([FetchUrlTool()])
        .execute("fetch_url", f'{{"url": "{GOV_URL}"}}', context())
        .result
    )
    assert result.status == "unavailable" and "联系邮箱" in (result.reason or "")


def test_chunks_split_on_paragraph_boundaries_and_hard_cut_oversized_blocks() -> None:
    blocks = ["甲" * 900, "乙" * 900, "丙" * 900, "丁" * 5000]
    chunks = fetch_tools._chunks("\n".join(blocks))
    assert all(len(chunk) <= fetch_tools.CHUNK_CHARS for chunk in chunks)
    assert chunks[0] == "甲" * 900 + "\n" + "乙" * 900  # 能放进一段的整段并在一起
    assert "".join(chunks).replace("\n", "") == "".join(blocks)


# ---- 和真实的礼貌客户端串起来：robots.txt 不允许时根本不会请求页面 ----------------------------
def _client(robots: str, page: bytes, requested: list[str]) -> PoliteClient:
    def opener(
        method: str, url: str, headers: Mapping[str, str], content: bytes | None
    ) -> Exchange:
        requested.append(url)
        if url.endswith("/robots.txt"):
            return Exchange(200, {}, robots.encode())
        return Exchange(200, {}, page)

    return PoliteClient(
        user_agent="FinScopeAI/1.0 (+ops@finscope.cn)",
        allowed_hosts=frozenset(),
        allowed_suffixes=frozenset({"gov.cn"}),
        opener=opener,
        resolve=lambda host: ("8.8.8.8",),
        sleeper=lambda seconds: None,
    )


def test_a_disallowed_path_is_never_requested() -> None:
    requested: list[str] = []
    client = _client("User-agent: *\nDisallow: /zhengce/", _page(5), requested)
    fetcher = lambda url, site: client.get(url, respect_robots=True)  # noqa: E731
    result = _run(fetcher, GOV_URL)
    assert result.status == "unavailable" and "robots.txt 不允许" in (result.reason or "")
    assert requested == ["https://www.gov.cn/robots.txt"]  # 只读了 robots.txt，没碰页面


def test_an_allowed_path_is_read_through_the_polite_client() -> None:
    requested: list[str] = []
    client = _client("User-agent: *\nDisallow: /private/", _page(5), requested)
    fetcher = lambda url, site: client.get(url, respect_robots=True)  # noqa: E731
    result = _run(fetcher, GOV_URL)
    assert result.status == "ok"
    assert requested == ["https://www.gov.cn/robots.txt", GOV_URL]
