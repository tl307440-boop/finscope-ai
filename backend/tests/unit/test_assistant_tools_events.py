"""披露事件工具 `hotspot_events` 与 `NewsEventRepository.search`。

用内存 SQLite，不需要 PostgreSQL。"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from app.db.models.news import NewsEvent
from app.repositories.news_events import NewsEventRepository
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.events import HotspotEventsTool
from sqlalchemy import Engine, create_engine
from sqlalchemy.pool import StaticPool

from live_fakes import NOW

CNINFO = "cninfo"


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    NewsEvent.__table__.create(engine)  # type: ignore[attr-defined]
    yield engine
    engine.dispose()


def _add(engine: Engine, title: str, published: datetime, source: str = CNINFO) -> None:
    from sqlalchemy.orm import Session

    with Session(engine) as session, session.begin():
        session.add(
            NewsEvent(
                id=uuid.uuid4(),
                title=title,
                canonical_url=f"https://www.cninfo.com.cn/{uuid.uuid4().hex}",
                source_name=source,
                published_at=published,
                captured_at=NOW,
                event_type="announcement",
                dedupe_hash=uuid.uuid4().hex,
                usage_rights="公开披露，保留原文链接",
                quality_flag="verified",
            )
        )


def _run(engine: Engine, arguments: str, now: datetime = NOW) -> ToolResult:
    ctx = ToolContext(engine=engine, user_id=uuid.uuid4(), now=now)
    return ToolRegistry([HotspotEventsTool()]).execute("hotspot_events", arguments, ctx).result


def _seed(engine: Engine) -> None:
    _add(engine, "中芯国际：关于签订重大合同的公告", NOW - timedelta(hours=2))
    _add(engine, "北方华创：2026 年三季度业绩预告", NOW - timedelta(days=2))
    _add(engine, "中芯国际：问询函回复", NOW - timedelta(days=5))
    _add(engine, "某公司：100% 全资子公司注销公告", NOW - timedelta(days=3))
    _add(engine, "太早的公告", NOW - timedelta(days=40))


def test_events_are_listed_newest_first_with_source_and_link(engine: Engine) -> None:
    _seed(engine)
    result = _run(engine, '{"days": 7}')
    assert result.status == "ok"
    assert [row["title"] for row in result.rows][:2] == [
        "中芯国际：关于签订重大合同的公告",
        "北方华创：2026 年三季度业绩预告",
    ]
    assert "太早的公告" not in [row["title"] for row in result.rows]
    first = result.rows[0]
    assert first["published"] == "2026-10-21" and str(first["url"]).startswith("https://")
    assert first["source"] and first["type"]
    assert result.as_of == date(2026, 10, 21)


def test_keyword_filters_titles_and_counts_the_overflow(engine: Engine) -> None:
    _seed(engine)
    result = _run(engine, '{"keyword": "中芯", "top": 5}')
    assert [row["title"] for row in result.rows] == [
        "中芯国际：关于签订重大合同的公告",
        "中芯国际：问询函回复",
    ]
    assert result.title == "披露事件：中芯"
    for n in range(8):
        _add(engine, f"中芯国际：公告 {n}", NOW - timedelta(hours=3 + n))
    many = _run(engine, '{"keyword": "中芯", "top": 5}')
    assert len(many.rows) == 5 and many.truncated == 5  # 一共 10 条命中，只给 5 条


def test_like_wildcards_in_the_keyword_are_literal(engine: Engine) -> None:
    _seed(engine)
    assert _run(engine, '{"keyword": "100%"}').rows[0]["title"] == "某公司：100% 全资子公司注销公告"
    assert _run(engine, '{"keyword": "%"}').rows[0]["title"] == "某公司：100% 全资子公司注销公告"
    assert _run(engine, '{"keyword": "_"}').status == "unavailable"  # 下划线不是通配符


def test_as_of_hides_events_published_after_that_day(engine: Engine) -> None:
    _seed(engine)
    result = _run(engine, '{"as_of": "2026-10-17", "days": 7}')
    titles = [row["title"] for row in result.rows]
    assert "中芯国际：问询函回复" in titles  # 10-16 的
    assert "中芯国际：关于签订重大合同的公告" not in titles  # 10-21 的，当时还没发生
    assert all(row["published"] <= "2026-10-17" for row in result.rows)


def test_the_as_of_day_is_included_up_to_the_end_of_the_shanghai_day(engine: Engine) -> None:
    # 上海 10-19 23:30 = UTC 10-19 15:30；上海 10-20 00:10 = UTC 10-19 16:10
    _add(engine, "晚间公告", datetime(2026, 10, 19, 15, 30, tzinfo=UTC))
    _add(engine, "次日零点后的公告", datetime(2026, 10, 19, 16, 10, tzinfo=UTC))
    result = _run(engine, '{"as_of": "2026-10-19"}')
    assert [row["title"] for row in result.rows] == ["晚间公告"]


def test_nothing_found_is_unavailable_and_explains_the_coverage(engine: Engine) -> None:
    _seed(engine)
    result = _run(engine, '{"keyword": "不存在的公司"}')
    assert result.status == "unavailable"
    reason = result.reason or ""
    assert "包含「不存在的公司」" in reason and "系统跟踪的公司" in reason


def test_an_empty_table_is_unavailable_not_an_error(engine: Engine) -> None:
    assert _run(engine, "{}").status == "unavailable"


def test_bad_arguments_are_rejected(engine: Engine) -> None:
    assert _run(engine, '{"days": 90}').status == "unavailable"
    assert _run(engine, '{"keyword": ""}').status == "unavailable"


def test_repository_search_returns_total_beyond_the_limit(engine: Engine) -> None:
    _seed(engine)
    repo = NewsEventRepository(engine)
    found, total = repo.search(start=NOW - timedelta(days=10), end=NOW, keyword=None, limit=2)
    assert (len(found), total) == (2, 4)
