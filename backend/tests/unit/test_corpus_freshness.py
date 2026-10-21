"""检索新鲜度衰减：纯函数的正常与边界，以及检索排序（用假引擎，不需要数据库）。"""

from datetime import UTC, date, datetime, timedelta
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.repositories.corpus import (
    FRESHNESS_CANDIDATES,
    MAX_PASSAGES,
    CorpusQuery,
    CorpusRepository,
)
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import CorpusSearchArgs, CorpusSearchTool
from app.services.corpus_freshness import (
    ANNOUNCEMENT_HALF_LIFE_DAYS,
    FLASH_HALF_LIFE_DAYS,
    POLICY_HALF_LIFE_DAYS,
    decayed_score,
    half_life_days,
    has_time_intent,
    strip_time_words,
)
from sqlalchemy import Engine

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)  # 上海 12:00


# ---------------------------------------------------------------- 纯函数
def _policy(rank: float, days_ago: int, **extra: Any) -> float:
    kwargs: dict[str, Any] = {"kind": "official", "doc_type": "policy"}
    kwargs.update(extra)
    return decayed_score(rank, TODAY - timedelta(days=days_ago), TODAY, **kwargs)


def test_the_publication_day_keeps_the_full_score() -> None:
    assert _policy(0.8, 0) == 0.8
    assert decayed_score(0.8, TODAY, TODAY, kind="filing", doc_type="announcement") == 0.8
    assert decayed_score(0.8, TODAY, TODAY, kind="official", doc_type="flash") == 0.8


@pytest.mark.parametrize(
    ("kind", "doc_type", "life"),
    [
        ("official", "policy", 180),
        ("official", "statement", 180),
        ("official", "regulation", 180),
        ("official", "news", 30),
        ("filing", "announcement", 30),
        ("reference", "research", 30),
        ("official", "flash", 3),
        ("filing", "newsflash", 3),
    ],
)
def test_one_half_life_halves_and_two_quarter(kind: str, doc_type: str, life: int) -> None:
    assert half_life_days(kind, doc_type) == life
    one = decayed_score(0.6, TODAY - timedelta(days=life), TODAY, kind=kind, doc_type=doc_type)
    two = decayed_score(0.6, TODAY - timedelta(days=2 * life), TODAY, kind=kind, doc_type=doc_type)
    assert one == pytest.approx(0.3)
    assert two == pytest.approx(0.15)


def test_half_lives_follow_the_category_table() -> None:
    assert (POLICY_HALF_LIFE_DAYS, ANNOUNCEMENT_HALF_LIFE_DAYS, FLASH_HALF_LIFE_DAYS) == (
        180,
        30,
        3,
    )
    # 同样 30 天前：政策还剩 89%，公告一半，快讯几乎没有。
    assert _policy(1.0, 30) == pytest.approx(0.5 ** (30 / 180))
    assert _policy(1.0, 30, kind="filing", doc_type="announcement") == pytest.approx(0.5)
    assert _policy(1.0, 30, doc_type="flash") == pytest.approx(0.5**10)


def test_a_future_date_counts_as_zero_days_old() -> None:
    assert _policy(0.7, -5) == 0.7
    assert decayed_score(0.7, TODAY + timedelta(days=400), TODAY, kind="filing") == 0.7


def test_user_documents_and_other_kinds_never_decay() -> None:
    ancient = date(2020, 1, 1)
    assert half_life_days("user", "upload") is None
    assert decayed_score(0.5, ancient, TODAY, kind="user", doc_type="upload") == 0.5
    # 用户文档即使 doc_type 写成快讯，也不衰减。
    assert decayed_score(0.5, ancient, TODAY, kind="user", doc_type="flash") == 0.5
    assert half_life_days("reference", "guide") is None
    assert decayed_score(0.5, ancient, TODAY, kind="reference", doc_type="guide") == 0.5


def test_an_unknown_official_type_is_treated_as_policy() -> None:
    assert half_life_days("official", "something-new") == POLICY_HALF_LIFE_DAYS


@pytest.mark.parametrize(
    "words",
    [
        ["最近"],
        ["近期 监管"],
        ["最新 政策"],
        ["今年 降息"],
        ["本周"],
        ["这周"],
        ["本月"],
        ["近日"],
        ["刚刚"],
        ["今天"],
        ["昨天"],
        ["新发布 规定"],
        ["前几天"],
        ["latest inflation"],
        ["Recently"],
        ["this week"],
        ["降息", "最新"],
    ],
)
def test_time_words_turn_the_intent_on(words: list[str]) -> None:
    assert has_time_intent(words)


@pytest.mark.parametrize(
    "words",
    [[], [""], ["降息"], ["美联储", "FOMC"], ["inflation"], ["recentralization"], ["tomorrow"]],
)
def test_no_time_word_means_no_intent(words: list[str]) -> None:
    assert not has_time_intent(words)


def test_time_words_are_stripped_from_the_match_terms() -> None:
    assert strip_time_words(["最近 降息"]) == ("降息",)
    assert strip_time_words(["最近几天 监管", "本周"]) == ("监管",)
    assert strip_time_words(["Latest inflation data", "this week"]) == ("inflation data",)
    assert strip_time_words(["最新"]) == ()
    assert strip_time_words(["降息"]) == ("降息",)


# ---------------------------------------------------------------- 假引擎
class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, engine: "_Engine") -> None:
        self._engine = engine

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        return None

    def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self._engine.statements.append(str(statement))
        self._engine.params.append(dict(params))
        return _Result(self._engine.rows)


class _Engine:
    """数据库按原始相关度从高到低给行（SQL 里 `ORDER BY rank DESC`），这里照做。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    def connect(self) -> _Connection:
        return _Connection(self)


def _row(
    title: str,
    rank: float,
    days_ago: int,
    *,
    kind: str = "official",
    doc_type: str = "policy",
    chunk: UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": chunk or uuid4(),
        "document_id": uuid4(),
        "title": title,
        "publisher": "测试来源",
        "kind": kind,
        "doc_type": doc_type,
        "canonical_url": f"https://www.example.test/{title}",
        "published_at": NOW - timedelta(days=days_ago),
        "content": f"{title} 的正文",
        "rank": rank,
        "section_title": None,
        "page_start": None,
        "page_end": None,
        "symbol": None,
        "period": None,
    }


def _search(
    rows: list[dict[str, Any]], *keywords: str, min_rank: float = 0.0
) -> tuple[list[Any], _Engine]:
    engine = _Engine(rows)
    found = CorpusRepository(cast(Engine, engine)).search(
        CorpusQuery(user_id=uuid4(), as_of=NOW, keywords_zh=keywords, min_rank=min_rank)
    )
    return found, engine


def _titles(found: list[Any]) -> list[str]:
    return [item.title for item in found]


def test_without_a_time_word_the_order_is_the_relevance_order() -> None:
    rows = [_row("旧政策", 0.30, 700), _row("新政策", 0.10, 1)]
    found, engine = _search(rows, "降息")
    assert _titles(found) == ["旧政策", "新政策"]
    assert [item.score for item in found] == [item.rank for item in found] == [0.30, 0.10]
    assert "power(" not in engine.statements[0]  # 衰减不在 SQL 里
    assert MAX_PASSAGES in engine.params[0].values()  # 只取 8 条，不扩大候选
    assert FRESHNESS_CANDIDATES not in engine.params[0].values()


def test_a_time_word_reorders_by_the_decayed_score_and_keeps_old_passages() -> None:
    # 旧政策原始相关度是 3 倍，但 540 天 = 3 个半衰期，衰减后只剩 1/8。
    rows = [_row("旧政策", 0.30, 540), _row("新政策", 0.10, 1)]
    found, engine = _search(rows, "最近 降息")
    assert _titles(found) == ["新政策", "旧政策"]  # 旧的没被丢掉，只是排后了
    assert [item.rank for item in found] == [0.10, 0.30]  # rank 仍是原始相关度
    assert found[0].score == pytest.approx(0.10 * 0.5 ** (1 / 180))
    assert found[1].score == pytest.approx(0.30 * 0.5**3)
    assert FRESHNESS_CANDIDATES in engine.params[0].values()  # 扩大候选再重排
    # 时间词不参与全文匹配：绑定参数里只剩「降息」的二字词。
    assert not any("最近" in str(value) for value in engine.params[0].values())
    assert any("降息" in str(value) for value in engine.params[0].values())


def test_the_time_travel_filter_stays_in_the_query() -> None:
    _found, engine = _search([_row("新政策", 0.1, 1)], "最新 降息")
    assert "d.published_at <= :as_of" in engine.statements[0]
    assert NOW in engine.params[0].values()


def test_the_gate_uses_the_raw_relevance_not_the_decayed_one() -> None:
    old = _row("旧政策", 0.30, 540)  # 衰减后 0.0375，低于阈值
    fresh = _row("新政策", 0.05, 1)
    # 最高的原始相关度 0.30 过了 0.2：衰减后分数低于阈值的旧片段也留着。
    found, _ = _search([old, fresh], "最近 降息", min_rank=0.2)
    assert _titles(found) == ["新政策", "旧政策"]
    # 最高的原始相关度 0.15 没过 0.2：即使有很新的片段，也整体不给。
    found, _ = _search(
        [_row("旧政策", 0.15, 540), _row("新政策", 0.14, 0)], "最近 降息", min_rank=0.2
    )
    assert found == []
    # 没有时间词时闸门行为一样。
    assert _search([_row("旧政策", 0.15, 540)], "降息", min_rank=0.2)[0] == []


def test_user_documents_do_not_decay_and_can_stay_first() -> None:
    rows = [
        _row("我的纪要", 0.30, 800, kind="user", doc_type="upload"),
        _row("新公告", 0.28, 1, kind="filing", doc_type="announcement"),
        _row("旧公告", 0.29, 60, kind="filing", doc_type="announcement"),  # 两个半衰期 → 0.0725
    ]
    found, _ = _search(rows, "最新 业绩")
    assert _titles(found) == ["我的纪要", "新公告", "旧公告"]
    assert found[0].score == 0.30


def test_equal_scores_prefer_the_newer_document_then_a_stable_order() -> None:
    rows = [
        _row("同分旧", 0.2, 3, kind="user", doc_type="upload", chunk=UUID(int=1)),
        _row("同分新乙", 0.2, 0, kind="user", doc_type="upload", chunk=UUID(int=3)),
        _row("同分新甲", 0.2, 0, kind="user", doc_type="upload", chunk=UUID(int=2)),
    ]
    found, _ = _search(rows, "今天 降息")
    assert _titles(found) == ["同分新甲", "同分新乙", "同分旧"]


def test_only_the_top_passages_come_back_after_reranking() -> None:
    # 候选里最旧的 12 条原始相关度更高、最新的 12 条更低：重排后取前 8 条，来自最新的。
    old = [_row(f"旧{index}", 0.5 - index * 0.001, 900) for index in range(12)]
    new = [_row(f"新{index}", 0.2 - index * 0.001, 1) for index in range(12)]
    found, _ = _search([*old, *new], "近期 降息")
    assert len(found) == MAX_PASSAGES
    assert all(title.startswith("新") for title in _titles(found))


def test_a_query_made_only_of_time_words_still_runs() -> None:
    found, engine = _search([_row("新政策", 0.1, 1)], "最新")
    assert _titles(found) == ["新政策"]
    assert any("最新" in str(value) for value in engine.params[0].values())  # 保持原样去查


def test_empty_results_stay_empty() -> None:
    assert _search([], "最近 降息")[0] == []


def test_the_search_tool_returns_the_decayed_order_only_for_time_intent() -> None:
    rows = [_row("旧政策", 0.30, 540), _row("新政策", 0.10, 1)]

    def run(*keywords: str) -> list[str]:
        ctx = ToolContext(engine=cast(Engine, _Engine(rows)), user_id=uuid4(), now=NOW)
        result = CorpusSearchTool().run(ctx, CorpusSearchArgs(keywords_zh=list(keywords)))
        assert result.status == "ok"
        return [passage.title for passage in result.passages]

    assert run("降息") == ["旧政策", "新政策"]
    assert run("最近 降息") == ["新政策", "旧政策"]
    assert run("降息", "最新") == ["新政策", "旧政策"]
