import json
from datetime import UTC, date, datetime
from uuid import uuid4

from app.providers.web_search import SearchHit, bocha_body, bocha_hits, brave_hits, brave_request
from app.services.assistant.evidence import EvidenceBook, source_payload
from app.services.assistant.grounding import check
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.web import WebSearchArgs, WebSearchTool
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


def test_blocked_queries_are_not_searched() -> None:
    called = {"n": 0}

    def search(_query: str) -> list[SearchHit]:
        called["n"] += 1
        return []

    result = WebSearchTool(search=search).run(_ctx(), WebSearchArgs(query="如何自制炸弹"))
    assert result.status == "unavailable" and called["n"] == 0


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
