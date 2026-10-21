import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import cast
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.providers.web_search import (
    NO_KEY_REASON,
    SearchHit,
    SearchServiceError,
    bocha_body,
    bocha_hits,
    brave_hits,
    brave_request,
    describe_status,
    infer_freshness,
)
from app.services.assistant.evidence import EvidenceBook, render, source_payload
from app.services.assistant.grounding import check
from app.services.assistant.results import Passage, ToolResult
from app.services.assistant.tools import ToolRegistry, web
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.web import (
    MAX_SEARCHES_PER_TURN,
    WebSearchArgs,
    WebSearchTool,
    _live_search,
    _search_with_window,
)
from app.services.html_text import article_text
from app.services.official_texts import parse_nbs_list
from app.services.rss_feed import parse_rss

NOW = datetime(2026, 10, 21, 2, tzinfo=UTC)
SPEECH = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item>
  <title>Powell, Economic outlook</title>
  <link>https://www.federalreserve.gov/newsevents/speech/powell20261020a.htm</link>
  <pubDate>Mon, 20 Oct 2026 14:00:00 GMT</pubDate>
</item>
<item>
  <title>Missing date</title>
  <link>https://www.federalreserve.gov/newsevents/speech/x.htm</link>
</item>
</channel></rss>
"""


def test_rss_keeps_dated_items_only() -> None:
    items = parse_rss(SPEECH.encode())
    assert [item.title for item in items] == ["Powell, Economic outlook"]
    assert items[0].published_at == datetime(2026, 10, 20, 14, tzinfo=UTC)


def test_article_text_stays_inside_the_container() -> None:
    html = b"""
    <nav>Menu</nav>
    <div id="article"><p>Inflation remains elevated.</p><script>secret()</script></div>
    <footer>Footer</footer>
    """
    text = article_text(html, element_id="article")
    assert text == "Inflation remains elevated."
    assert "Menu" not in text and "secret" not in text
    assert article_text(html, element_id="missing") == ""


def test_nbs_list_dedupes_and_reads_the_title() -> None:
    html = """
    <a href="./202610/t20261020_1.html" title="2026年三季度国内生产总值初步核算结果"></a>
    <a href="./202610/t20261020_1.html" title="2026年三季度国内生产总值初步核算结果"></a>
    <a href="https://other.example/a.html" title="外站"></a>
    """.encode()
    items = parse_nbs_list(html, "https://www.stats.gov.cn/sj/zxfb/")
    assert [(item.title, item.url) for item in items] == [
        (
            "2026年三季度国内生产总值初步核算结果",
            "https://www.stats.gov.cn/sj/zxfb/202610/t20261020_1.html",
        )
    ]
    assert items[0].published_at.date() == date(2026, 10, 20)


def test_search_payloads_keep_title_url_and_date() -> None:
    brave = brave_hits(
        json.dumps(
            {
                "web": {
                    "results": [
                        {
                            "title": "Warsh interview",
                            "url": "https://news.example/warsh",
                            "description": "He sounded cautious.",
                            "page_age": "2026-10-18T12:00:00Z",
                        }
                    ]
                }
            }
        ).encode()
    )
    bocha = bocha_hits(
        json.dumps(
            {
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "name": "沃什访谈",
                                "url": "https://news.example/cn",
                                "snippet": "措辞偏谨慎。",
                                "datePublished": "2026-10-18",
                            }
                        ]
                    }
                }
            }
        ).encode()
    )
    assert (brave[0].provider, brave[0].published) == ("Brave", date(2026, 10, 18))
    assert bocha[0].title == "沃什访谈"


def test_freshness_is_passed_through_to_both_search_providers() -> None:
    assert json.loads(bocha_body("沃什", "oneWeek"))["freshness"] == "oneWeek"
    url, _headers = brave_request("warsh", "oneWeek")
    assert "freshness=pw" in url


def test_an_empty_time_window_is_searched_again_without_one() -> None:
    calls: list[str | None] = []
    older = SearchHit(
        "沃什听证会", "https://news.example/warsh", "利率上留有空间。", date(2026, 4, 23), "博查"
    )

    def live(_query: str, _settings: object, freshness: str | None) -> list[SearchHit]:
        calls.append(freshness)
        return [] if freshness else [older]

    hits, widened = _search_with_window("沃什", cast(Settings, object()), "oneWeek", live)
    assert calls == ["oneWeek", None]
    assert widened is True
    assert hits == [older]

    kept, again = _search_with_window(
        "沃什", cast(Settings, object()), "oneWeek", lambda *_: [older]
    )
    assert again is False and kept == [older]


def test_a_widened_search_tells_the_model_to_use_the_report_date() -> None:
    result = ToolResult(
        tool="web_search",
        title="沃什",
        columns=(),
        rows=(),
        as_of=date(2026, 4, 23),
        source="联网",
        reason="限定的时间范围内没有结果，以下是不限时间的检索。",
        passages=(
            Passage(
                key="web:1",
                title="沃什听证会",
                publisher="博查",
                corpus_kind="web",
                published=date(2026, 4, 23),
                content="利率上留有空间。",
                url="https://news.example/warsh",
                verified=False,
            ),
        ),
        excerpt=True,
    )
    text = render(EvidenceBook().add(result))
    assert "注意：限定的时间范围内没有结果" in text
    assert "2026-04-23" in text


def test_web_snippets_are_unverified_and_numbers_need_a_citation() -> None:
    def search(_query: str) -> list[SearchHit]:
        return [
            SearchHit(
                "某报：沃什访谈",
                "https://news.example/warsh",
                "报道称他倾向于再降息 25 个基点。",
                date(2026, 10, 18),
                "Brave",
            )
        ]

    tool = WebSearchTool(search=search, fetch_page=lambda _url: "")
    result = tool.run(_ctx(), WebSearchArgs(query="凯文沃什访谈"))
    assert result.excerpt is True
    passage = result.passages[0]
    assert passage.verified is False
    payload = source_payload(1, passage)
    assert payload["kind"] == "web" and payload["verified"] is False
    book = EvidenceBook()
    book.add(result)
    assert not check("他倾向于再降息 25 个基点。", book).ok
    assert check("报道称他倾向于再降息 25 个基点[1]。", book).ok


def test_an_official_page_from_search_is_fetched_instead_of_the_snippet() -> None:
    def search(_query: str) -> list[SearchHit]:
        return [
            SearchHit(
                "Powell speech",
                "https://www.federalreserve.gov/newsevents/speech/a.htm",
                "snippet should not be used",
                date(2026, 10, 20),
                "Brave",
            )
        ]

    tool = WebSearchTool(
        search=search, fetch_page=lambda _url: "The Committee decided to lower the target range."
    )
    result = tool.run(_ctx(), WebSearchArgs(query="Powell speech"))
    assert result.passages[0].verified is True
    assert "lower the target range" in result.passages[0].content
    assert "snippet" not in result.passages[0].content
    assert result.rows[0]["note"] == "已改为官方原文"


def test_exchange_pages_from_search_are_taken_as_official_text_but_reposts_are_not() -> None:
    def search(_query: str) -> list[SearchHit]:
        return [
            SearchHit(
                "深交所召开上市公司座谈会",
                "https://www.szse.cn/aboutus/trends/news/t20261019_1.html",
                "摘要不应被使用",
                date(2026, 10, 19),
                "博查",
            ),
            SearchHit(
                "上交所发布监管动态",
                "https://www.sse.com.cn/disclosure/credibility/supervision/dynamic/c/c_1.shtml",
                "摘要同样不应被使用",
                date(2026, 10, 18),
                "博查",
            ),
            SearchHit(
                "某报转载深交所座谈会",
                "https://news.example/szse",
                "转载的摘要",
                date(2026, 10, 19),
                "博查",
            ),
        ]

    pages = {"count": 0}

    def fetch(_url: str) -> str:
        pages["count"] += 1
        return "交易所召开座谈会，听取上市公司意见建议。"

    result = WebSearchTool(search=search, fetch_page=fetch).run(
        _ctx(), WebSearchArgs(query="交易所 座谈会")
    )
    szse, sse, repost = result.passages
    assert (szse.verified, sse.verified, repost.verified) == (True, True, False)
    assert "听取上市公司意见建议" in szse.content and "摘要" not in szse.content
    assert repost.content == "转载的摘要"
    assert pages["count"] == 2  # 转载页不去下载
    assert [row["note"] for row in result.rows] == ["已改为官方原文", "已改为官方原文", "未核实"]


def test_blocked_queries_are_not_searched() -> None:
    called = {"n": 0}

    def search(_query: str) -> list[SearchHit]:
        called["n"] += 1
        return []

    result = WebSearchTool(search=search).run(_ctx(), WebSearchArgs(query="如何自制炸弹"))
    assert result.status == "unavailable" and called["n"] == 0


# ---- ASSISTANT-3A4：服务出错和「没有结果」分开、每回合次数、时间范围 ----

BOCHA_OK = json.dumps(
    {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "马斯克称美债问题只能靠 AI 和机器人解决",
                        "url": "https://news.example/musk",
                        "snippet": "他称单靠削减开支无法解决。",
                        "datePublished": "2026-10-18",
                    }
                ]
            }
        }
    },
    ensure_ascii=False,
).encode()
BRAVE_OK = json.dumps(
    {
        "web": {
            "results": [
                {
                    "title": "Musk says US debt needs AI",
                    "url": "https://news.example/musk-en",
                    "description": "Cutting spending alone will not work.",
                    "page_age": "2026-10-17T00:00:00Z",
                }
            ]
        }
    }
).encode()


SERVICE_DOWN = "联网检索服务暂时不可用（博查：密钥无效或额度不足），不是网上没有这条消息。"


def _keys(bocha: str = "", brave: str = "") -> Settings:
    return Settings(bocha_api_key=bocha, brave_api_key=brave)


def _http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("query", "window"),
    [
        ("马斯克 推特 前几天 AI 机器人", "oneWeek"),
        ("美联储 最近一次讲话", "oneMonth"),
        ("最近几天美股科技股", "oneWeek"),  # 「最近几天」比「最近」更具体
        ("今天 A 股涨停 刚刚", "oneDay"),
        ("Powell speech this week", "oneWeek"),
        ("Musk robots recently", "oneMonth"),
        ("美联储 点阵图 2025 年 12 月", None),
        ("", None),
    ],
)
def test_the_time_window_comes_from_the_words_in_the_query(query: str, window: str | None) -> None:
    assert infer_freshness(query) == window


def test_the_model_cannot_choose_the_time_window() -> None:
    args = WebSearchArgs.model_validate({"query": "马斯克 前几天 推特", "freshness": "oneYear"})
    assert not hasattr(args, "freshness")


def test_a_quota_error_is_reported_as_a_service_problem_not_as_no_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"code": "403", "message": "You do not have enough money or package quota"}
        )

    with pytest.raises(SearchServiceError) as caught:
        _live_search("马斯克 前几天", _keys(bocha="secret-bocha-key"), "oneWeek", _http(handler))
    reason = caught.value.reason
    assert "博查" in reason and "额度不足" in reason
    assert "不是网上没有这条消息" in reason
    assert "secret-bocha-key" not in reason
    assert len(reason) <= 200  # 渲染给模型时按 200 字截断，关键的话不能被截掉


def test_the_second_provider_is_used_when_the_first_one_is_down() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        if request.url.host == "api.bochaai.com":
            return httpx.Response(403)
        return httpx.Response(200, content=BRAVE_OK)

    hits = _live_search("马斯克 前几天", _keys("b", "r"), None, _http(handler))
    assert seen == ["api.bochaai.com", "api.search.brave.com"]
    assert [hit.provider for hit in hits] == ["Brave"]


def test_an_empty_answer_from_a_healthy_service_is_not_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"webPages": {"value": []}}})

    assert _live_search("马斯克", _keys(bocha="b"), None, _http(handler)) == []


def test_a_timeout_and_a_missing_key_are_service_problems_too() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    with pytest.raises(SearchServiceError) as timed_out:
        _live_search("Musk", _keys(brave="r"), None, _http(broken))
    assert "Brave" in timed_out.value.reason and "网络超时" in timed_out.value.reason

    with pytest.raises(SearchServiceError) as no_key:
        _live_search("马斯克", _keys(), None, _http(broken))
    assert no_key.value.reason == NO_KEY_REASON and len(NO_KEY_REASON) <= 200


def test_status_codes_are_explained_in_words() -> None:
    assert describe_status(403) == "密钥无效或额度不足"
    assert describe_status(429) == "请求过于频繁或额度用完"
    assert describe_status(503) == "服务暂时故障"
    assert describe_status(418) == "返回 HTTP 418"


def test_the_tool_tells_the_model_the_service_failed_and_stops_retrying() -> None:
    calls = {"n": 0}

    def search(_query: str) -> list[SearchHit]:
        calls["n"] += 1
        raise SearchServiceError(SERVICE_DOWN)

    tool = WebSearchTool(search=search)
    ctx = _ctx()
    first = tool.run(ctx, WebSearchArgs(query="马斯克 前几天 推特"))
    assert first.status == "unavailable" and "不是网上没有这条消息" in str(first.reason)
    assert "不是网上没有这条消息" in render(EvidenceBook().add(first))
    again = tool.run(ctx, WebSearchArgs(query="Elon Musk X post"))
    assert again.reason == first.reason
    assert calls["n"] == 1  # 服务已经报错，本回合不再重试


def test_no_matching_report_is_worded_differently_from_a_service_failure() -> None:
    tool = WebSearchTool(search=lambda _query: [])
    result = tool.run(_ctx(), WebSearchArgs(query="马斯克 前几天 推特"))
    assert result.status == "unavailable"
    assert result.reason == "检索服务正常，但没有查到可用的结果"
    assert "出了问题" not in str(result.reason)


def test_a_turn_can_search_the_web_at_most_twice() -> None:
    calls = {"n": 0}

    def search(_query: str) -> list[SearchHit]:
        calls["n"] += 1
        return [SearchHit("某报", "https://news.example/a", "摘要。", date(2026, 10, 18), "博查")]

    tool = WebSearchTool(search=search, fetch_page=lambda _url: "")
    ctx = _ctx()
    for index in range(MAX_SEARCHES_PER_TURN):
        assert tool.run(ctx, WebSearchArgs(query=f"马斯克 推特 第{index}次")).status == "ok"
    over = tool.run(ctx, WebSearchArgs(query="Elon Musk X post"))
    assert over.status == "unavailable" and "不要再查" in str(over.reason)
    assert calls["n"] == MAX_SEARCHES_PER_TURN
    # 下一个回合有新的上下文，次数重新算
    assert tool.run(_ctx(), WebSearchArgs(query="马斯克 推特")).status == "ok"


def test_the_live_path_derives_the_window_and_surfaces_a_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows: list[str | None] = []

    def live(_query: str, _settings: object, freshness: str | None = None) -> list[SearchHit]:
        windows.append(freshness)
        raise SearchServiceError("联网检索服务暂时不可用（博查：服务暂时故障）。")

    monkeypatch.setattr(web, "_live_search", live)
    monkeypatch.setattr(web, "_settings", lambda _ctx: Settings(assistant_web_search_enabled=True))
    ctx = _ctx()
    registry = ToolRegistry([WebSearchTool()])
    # 模型多填的 freshness 被忽略；时间范围由检索词里的「前几天」决定
    outcome = registry.execute(
        "web_search", json.dumps({"query": "马斯克 前几天 推特", "freshness": "oneYear"}), ctx
    )
    assert windows == ["oneWeek"]
    assert outcome.result.status == "unavailable"
    assert "服务暂时故障" in str(outcome.result.reason)
    assert ctx.web_failure == outcome.result.reason


def test_startup_fills_empty_official_sources_and_skips_a_placeholder_email() -> None:
    from uuid import UUID

    from app.core.config import Settings
    from app.repositories.sync_jobs import JobSnapshot
    from app.services.official_texts import catch_up_official_texts
    from app.services.scheduler import ScheduledTask

    class _Jobs:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def enqueue(self, **kwargs: object) -> tuple[JobSnapshot, bool]:
            self.keys.append(str(kwargs["dedupe_key"]))
            assert kwargs["scope"] == {"source": "fed_speeches", "limit": "20"}  # type: ignore[index]
            job = JobSnapshot(
                id=uuid4(),
                job_type="corpus_official",
                provider_id=UUID(int=1),
                scope={},
                dedupe_key=str(kwargs["dedupe_key"]),
                status="queued",
                attempts=0,
                max_attempts=3,
                rows_written=0,
                error_code=None,
                error_summary=None,
                progress_stage="queued",
                requested_at=NOW,
            )
            return job, True

    def tasks(_engine: object, _settings: object, now: datetime) -> list[ScheduledTask]:
        return [
            ScheduledTask(
                task_name="sync_official_texts",
                job_type="corpus_official",
                dedupe_key=f"corpus-official:{code}:{now:%Y-%m-%dT%H}",
                scope={"source": code},
                provider_id=UUID(int=1),
                failure_policy="test",
            )
            for code in ("fed_speeches", "nbs_release")
        ]

    jobs = _Jobs()
    ready = Settings(sec_user_agent="FinScope ops@finscope.test")
    queued = catch_up_official_texts(
        None,  # type: ignore[arg-type]
        ready,
        missing=["fed_speeches"],
        enqueuer=jobs,
        list_tasks=tasks,
    )
    assert queued == 1
    assert jobs.keys[0].startswith("corpus-official:fed_speeches:")
    assert (
        catch_up_official_texts(
            None,  # type: ignore[arg-type]
            Settings(),
            missing=["fed_speeches"],
            enqueuer=jobs,
            list_tasks=tasks,
        )
        == 0
    )
    assert len(jobs.keys) == 1
    assert (
        catch_up_official_texts(
            None,  # type: ignore[arg-type]
            ready,
            missing=[],
            enqueuer=jobs,
            list_tasks=tasks,
        )
        == 0
    )


def _ctx() -> ToolContext:
    return ToolContext(engine=None, user_id=uuid4(), now=NOW)  # type: ignore[arg-type]
