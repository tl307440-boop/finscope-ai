"""语料语义检索：补向量任务、检索词转向量、混合检索的融合与阈值、工具接线、调度登记。

数据库用假件：按语句内容分辨全文检索、向量检索和 `SET LOCAL`，真实 SQL 由集成测试覆盖。
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.providers.corpus_embedding import MAX_BATCH, MAX_INPUT_CHARS, EmbeddingConfig
from app.providers.errors import ProviderError
from app.repositories.corpus import (
    CorpusQuery,
    CorpusRepository,
    Passage,
    QueryVector,
    fuse_passages,
    semantic_query_text,
)
from app.repositories.corpus_embeddings import ChunkVector, PendingChunk
from app.services import scheduler
from app.services.assistant.tools import default_registry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import CorpusSearchArgs, CorpusSearchTool
from app.services.corpus_vectors import QueryEmbedder, embed_pending
from app.workers import jobs
from sqlalchemy import Engine

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
KEYED = Settings(corpus_embedding_api_key="sk-test", corpus_enabled=True)
VECTOR = QueryVector(model_id="embedding-3@1024", values=(1.0,) + (0.0,) * 1023)


# ---------------------------------------------------------------- 补向量任务
class _Repo:
    def __init__(self, pending: list[PendingChunk]) -> None:
        self._pending = pending
        self.asked: list[tuple[str, int]] = []
        self.saved: list[list[ChunkVector]] = []

    def pending(self, model_id: str, limit: int) -> list[PendingChunk]:
        self.asked.append((model_id, limit))
        return self._pending[:limit]

    def save(self, model_id: str, rows: Sequence[ChunkVector], now: datetime) -> None:
        assert model_id == "embedding-3@1024" and now.tzinfo is not None
        self.saved.append(list(rows))


def _chunks(count: int, content: str = "正文") -> list[PendingChunk]:
    return [PendingChunk(uuid4(), f"标题{i}", "", content) for i in range(count)]


class _Embed:
    """假的嵌入接口：`reject` 里的文本让整批 400；单条请求时只拒绝那一条。"""

    def __init__(self, reject: str = "", error: ProviderError | None = None) -> None:
        self.batches: list[list[str]] = []
        self._reject, self._error = reject, error

    def __call__(
        self, config: EmbeddingConfig, texts: list[str], **_kwargs: object
    ) -> list[list[float]]:
        self.batches.append(list(texts))
        if self._error is not None:
            raise self._error
        if self._reject and any(self._reject in text for text in texts):
            raise ProviderError(
                code="UPSTREAM_REJECTED", message="智谱拒绝了这批文本（错误码 1301）"
            )
        return [[1.0] + [0.0] * (config.dims - 1) for _ in texts]


def _run(repo: _Repo, embed: _Embed, settings: Settings = KEYED, **kwargs: Any) -> int:
    return embed_pending(
        cast(Engine, None), settings, repository=cast(Any, repo), embed=embed, **kwargs
    )


def test_pending_chunks_are_embedded_in_batches_of_32_newest_first() -> None:
    repo, embed = _Repo(_chunks(70)), _Embed()
    assert _run(repo, embed, limit=70) == 70
    assert repo.asked == [("embedding-3@1024", 70)]
    assert [len(batch) for batch in embed.batches] == [MAX_BATCH, MAX_BATCH, 6]
    assert embed.batches[0][0] == "标题0\n正文"
    assert all(
        row.vector is not None and row.skip_reason is None for rows in repo.saved for row in rows
    )


def test_the_per_run_limit_comes_from_settings_when_the_job_does_not_say() -> None:
    repo = _Repo(_chunks(5))
    _run(repo, _Embed(), Settings(corpus_embedding_api_key="k", corpus_embed_per_run=3))
    assert repo.asked == [("embedding-3@1024", 3)]


def test_without_a_key_nothing_is_read_or_sent() -> None:
    repo, embed = _Repo(_chunks(3)), _Embed()
    assert _run(repo, embed, Settings()) == 0
    assert repo.asked == [] and embed.batches == []


def test_a_rejected_batch_is_retried_one_by_one_and_only_the_bad_chunk_is_skipped() -> None:
    chunks = [*_chunks(2), PendingChunk(uuid4(), "敏感", "", "被拒的内容"), *_chunks(1)]
    repo, embed = _Repo(chunks), _Embed(reject="被拒")
    assert _run(repo, embed) == 3
    (rows,) = repo.saved
    skipped = [row for row in rows if row.vector is None]
    assert [row.chunk_id for row in skipped] == [chunks[2].chunk_id]
    assert skipped[0].skip_reason is not None and "1301" in skipped[0].skip_reason
    assert [len(batch) for batch in embed.batches] == [4, 1, 1, 1, 1]


def test_empty_chunks_are_skipped_without_asking_the_service() -> None:
    chunks = [PendingChunk(uuid4(), "", " ", "  "), *_chunks(1)]
    repo, embed = _Repo(chunks), _Embed()
    assert _run(repo, embed) == 1
    assert embed.batches == [["标题0\n正文"]]
    assert repo.saved[0][0].skip_reason == "没有可向量化的文字"


def test_long_chunks_are_cut_and_the_cut_is_recorded() -> None:
    repo, embed = _Repo([PendingChunk(uuid4(), "标题", "", "字" * MAX_INPUT_CHARS)]), _Embed()
    _run(repo, embed)
    assert len(embed.batches[0][0]) == MAX_INPUT_CHARS
    assert repo.saved[0][0].truncated_chars == len("标题\n")


@pytest.mark.parametrize("code", ["UPSTREAM_AUTH", "RATE_LIMITED", "UPSTREAM_TIMEOUT"])
def test_key_quota_and_timeout_problems_stop_the_run_and_keep_earlier_batches(code: str) -> None:
    class _FailSecond(_Embed):
        def __call__(
            self, config: EmbeddingConfig, texts: list[str], **kwargs: object
        ) -> list[list[float]]:
            if self.batches:
                self.batches.append(list(texts))
                raise ProviderError(code=code, message="停")
            return super().__call__(config, texts, **kwargs)

    repo = _Repo(_chunks(40))
    with pytest.raises(ProviderError) as caught:
        _run(repo, _FailSecond(), limit=40)
    assert caught.value.code == code
    assert [len(rows) for rows in repo.saved] == [MAX_BATCH]


# ---------------------------------------------------------------- 检索词转向量
def test_query_vectors_are_cached_per_model_and_text() -> None:
    embed = _Embed()
    embedder = QueryEmbedder(settings_for=lambda _engine: KEYED, embed=embed)
    first = embedder(cast(Engine, None), " 降准   流动性 ")
    again = embedder(cast(Engine, None), "降准 流动性")
    assert first is not None and first is again
    assert first.model_id == "embedding-3@1024" and len(first.values) == 1024
    assert embed.batches == [["降准 流动性"]]


def test_query_vector_is_none_without_a_key_or_when_the_service_fails() -> None:
    embed = _Embed()
    assert QueryEmbedder(lambda _e: Settings(), embed)(cast(Engine, None), "降准") is None
    assert embed.batches == []
    failing = _Embed(error=ProviderError(code="RATE_LIMITED", message="余额不足"))
    assert QueryEmbedder(lambda _e: KEYED, failing)(cast(Engine, None), "降准") is None
    assert QueryEmbedder(lambda _e: KEYED, embed)(cast(Engine, None), "   ") is None


def test_the_query_cache_drops_the_oldest_entry() -> None:
    embed = _Embed()
    embedder = QueryEmbedder(lambda _e: KEYED, embed, cache_size=2)
    for text in ("甲", "乙", "丙", "甲"):
        embedder(cast(Engine, None), text)
    assert [batch[0] for batch in embed.batches] == ["甲", "乙", "丙", "甲"]


def test_time_words_are_left_out_of_the_query_vector() -> None:
    assert semantic_query_text(("最近 降准", "流动性"), ()) == "降准 流动性"
    assert semantic_query_text(("最近",), ()) == "最近"
    assert semantic_query_text((), ("rate cut",)) == "rate cut"


# ---------------------------------------------------------------- 融合
def _passage(
    title: str,
    *,
    rank: float = 0.0,
    similarity: float | None = None,
    document: UUID | None = None,
    days_ago: int = 1,
) -> Passage:
    return Passage(
        chunk_id=uuid4(),
        document_id=document or uuid4(),
        title=title,
        publisher="美联储",
        kind="official",
        doc_type="speech",
        url="https://www.federalreserve.gov/x",
        published_at=NOW - timedelta(days=days_ago),
        content=title,
        rank=rank,
        score=rank,
        similarity=similarity,
    )


def test_a_chunk_found_by_both_routes_ranks_first_and_keeps_both_scores() -> None:
    both = _passage("两路都有", rank=0.2)
    words_only = _passage("只有全文", rank=0.5)
    # 与「只有全文」的融合分相同：新的排前面，所以让它旧一天。
    meaning_only = _passage("只有语义", similarity=0.9, days_ago=2)
    semantic = [meaning_only, replace(both, similarity=0.7)]
    fused = fuse_passages([words_only, both], semantic)
    assert [item.title for item in fused] == ["两路都有", "只有全文", "只有语义"]
    assert (fused[0].rank, fused[0].similarity) == (0.2, 0.7)
    assert fused[1].similarity is None
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 62)


def test_fusion_keeps_at_most_two_chunks_per_document() -> None:
    document = uuid4()
    lexical = [_passage(f"块{i}", rank=1 - i / 10, document=document) for i in range(3)]
    assert [item.title for item in fuse_passages(lexical, [])] == ["块0", "块1"]


# ---------------------------------------------------------------- 混合检索（假数据库）
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

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self._engine.statements.append(sql)
        if sql.startswith("SET LOCAL"):
            return _Result([])
        if "corpus_embeddings" in sql:
            self._engine.vector_params.append(dict(params or {}))
            return _Result(self._engine.semantic)
        return _Result(self._engine.lexical)


class _Engine:
    def __init__(self, lexical: list[dict[str, Any]], semantic: list[dict[str, Any]]) -> None:
        self.lexical, self.semantic = lexical, semantic
        self.statements: list[str] = []
        self.vector_params: list[dict[str, Any]] = []

    def connect(self) -> _Connection:
        return _Connection(self)


def _row(
    title: str, *, rank: float = 0.0, similarity: float | None = None, days_ago: int = 1
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": uuid4(),
        "document_id": uuid4(),
        "title": title,
        "publisher": "美联储",
        "kind": "official",
        "doc_type": "speech",
        "canonical_url": "https://www.federalreserve.gov/x",
        "published_at": NOW - timedelta(days=days_ago),
        "content": title,
        "rank": rank,
        "section_title": None,
        "page_start": None,
        "page_end": None,
        "symbol": None,
        "period": None,
    }
    if similarity is not None:
        row["similarity"] = similarity
    return row


def _search(engine: _Engine, *keywords: str, **extra: Any) -> list[Passage]:
    query = CorpusQuery(user_id=uuid4(), as_of=NOW, keywords_zh=keywords, **extra)
    return CorpusRepository(cast(Engine, engine)).search(query)


def test_without_a_vector_the_search_never_touches_the_vector_table() -> None:
    engine = _Engine([_row("全文命中", rank=0.3)], [_row("不该出现", similarity=0.99)])
    assert [item.title for item in _search(engine, "降准")] == ["全文命中"]
    assert not any("corpus_embeddings" in sql or "SET LOCAL" in sql for sql in engine.statements)


def test_hybrid_search_adds_synonym_matches_and_drops_weak_ones() -> None:
    engine = _Engine(
        [_row("降准公告", rank=0.3)],
        [_row("下调存款准备金率", similarity=0.82), _row("无关讲话", similarity=0.31)],
    )
    found = _search(engine, "降准", vector=VECTOR, min_similarity=0.55)
    assert sorted(item.title for item in found) == ["下调存款准备金率", "降准公告"]
    assert engine.statements[1] == "SET LOCAL hnsw.ef_search = 200"
    sql = engine.statements[2]
    assert "e.embedding_model = 'embedding-3@1024'" in sql
    assert "e.embedding::halfvec(1024) <=> CAST(" in sql and "AS halfvec(1024))" in sql
    assert "d.published_at <= :as_of" in sql and "owner_user_id" in sql  # 时点与可见性同样过滤
    (params,) = engine.vector_params
    vector_literal = next(value for key, value in params.items() if key.startswith("qv"))
    assert vector_literal.startswith("[1.00000000,0.00000000")


def test_semantic_hits_alone_answer_when_the_words_do_not_match() -> None:
    engine = _Engine([], [_row("Policy rate lowered", similarity=0.7)])
    assert [item.title for item in _search(engine, "降息", vector=VECTOR, min_similarity=0.55)] == [
        "Policy rate lowered"
    ]


def test_nothing_is_returned_when_both_routes_miss_their_thresholds() -> None:
    engine = _Engine([_row("弱相关", rank=0.01)], [_row("弱相似", similarity=0.4)])
    assert _search(engine, "降准", vector=VECTOR, min_rank=0.05, min_similarity=0.55) == []


def test_a_weak_lexical_route_does_not_block_a_strong_semantic_one() -> None:
    engine = _Engine([_row("弱相关", rank=0.01)], [_row("强相似", similarity=0.9)])
    found = _search(engine, "降准", vector=VECTOR, min_rank=0.05, min_similarity=0.55)
    assert [item.title for item in found] == ["强相似"]


def test_time_words_reorder_the_fused_list_by_freshness() -> None:
    engine = _Engine(
        [_row("去年的讲话", rank=0.5, days_ago=400), _row("昨天的讲话", rank=0.4, days_ago=1)],
        [],
    )
    found = _search(engine, "最近 加息", vector=VECTOR)
    assert [item.title for item in found] == ["昨天的讲话", "去年的讲话"]


@pytest.mark.parametrize(
    "vector",
    [
        QueryVector(model_id="embedding-3@1024'; DROP TABLE x; --", values=(1.0,) * 1024),
        QueryVector(model_id="embedding-3@512", values=(1.0,) * 1024),
    ],
)
def test_a_malformed_model_id_never_reaches_the_sql(vector: QueryVector) -> None:
    engine = _Engine([], [])
    with pytest.raises(ValueError):
        _search(engine, "降准", vector=vector)
    assert not any("corpus_embeddings" in sql for sql in engine.statements)


# ---------------------------------------------------------------- 工具接线
class _CapturingRepository:
    queries: list[CorpusQuery] = []

    def __init__(self, _engine: object) -> None:
        pass

    def search(self, query: CorpusQuery) -> list[Passage]:
        self.queries.append(query)
        return [_passage("下调存款准备金率", similarity=0.8)]


def test_tool_passes_the_query_vector_and_threshold_to_the_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.assistant.tools import corpus as corpus_tool

    _CapturingRepository.queries = []
    monkeypatch.setattr(corpus_tool, "CorpusRepository", _CapturingRepository)
    texts: list[str] = []

    def embed_query(_engine: Engine, text: str) -> QueryVector | None:
        texts.append(text)
        return VECTOR

    tool = CorpusSearchTool(min_similarity=0.6, embed_query=embed_query)
    ctx = ToolContext(engine=cast(Engine, None), user_id=uuid4(), now=NOW)
    result = tool.run(ctx, CorpusSearchArgs(keywords_zh=["最近 降准"], keywords_en=["RRR"]))
    assert result.status == "ok"
    assert texts == ["降准 RRR"]
    (query,) = _CapturingRepository.queries
    assert (query.vector, query.min_similarity) == (VECTOR, 0.6)


def test_tool_falls_back_to_words_only_when_no_vector_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.assistant.tools import corpus as corpus_tool

    _CapturingRepository.queries = []
    monkeypatch.setattr(corpus_tool, "CorpusRepository", _CapturingRepository)
    ctx = ToolContext(engine=cast(Engine, None), user_id=uuid4(), now=NOW)
    CorpusSearchTool(embed_query=lambda _e, _t: None).run(
        ctx, CorpusSearchArgs(keywords_zh=["降准"])
    )
    CorpusSearchTool().run(ctx, CorpusSearchArgs(keywords_zh=["降准"]))
    assert [query.vector for query in _CapturingRepository.queries] == [None, None]


def test_registry_hands_the_embedder_to_corpus_search_only_when_the_corpus_is_on() -> None:
    def embed_query(_engine: Engine, _text: str) -> QueryVector | None:
        return None

    assert "corpus_search" not in default_registry(corpus_embed_query=embed_query).names
    registry = default_registry(
        corpus_min_rank=0.0, corpus_min_similarity=0.6, corpus_embed_query=embed_query
    )
    assert "corpus_search" in registry.names


# ---------------------------------------------------------------- 调度与任务
def test_embedding_job_is_scheduled_only_with_the_corpus_on_and_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "_provider_id", lambda _engine, **_kwargs: UUID(int=7))
    engine = cast(Engine, None)
    now = datetime(2026, 10, 21, 8, 37, tzinfo=UTC)
    assert scheduler.build_corpus_embed_tasks(engine, Settings(corpus_enabled=True), now) == []
    assert (
        scheduler.build_corpus_embed_tasks(engine, Settings(corpus_embedding_api_key="k"), now)
        == []
    )
    (task,) = scheduler.build_corpus_embed_tasks(engine, KEYED, now)
    assert (task.job_type, task.dedupe_key, task.scope) == (
        "corpus_embed",
        "corpus-embed:2026-10-21T16:30",
        {"limit": "320"},
    )
    assert task.max_attempts == 1 and task.unavailable_reason is None


def test_embedding_trigger_runs_every_ten_minutes_and_can_be_switched_off() -> None:
    plans = {plan.job_id: plan for plan in scheduler.build_triggers(Settings())}
    assert "minute='*/10'" in str(plans["embed_corpus_chunks"].trigger)
    off = scheduler.build_triggers(Settings(scheduler_enable_corpus_embed=False))
    assert "embed_corpus_chunks" not in {plan.job_id for plan in off}


def test_worker_runs_the_embedding_job_with_the_scoped_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int | None] = []

    def fake(_engine: Engine, _settings: Settings, *, limit: int | None = None) -> int:
        calls.append(limit)
        return 12

    monkeypatch.setattr(jobs, "embed_pending", fake)
    monkeypatch.setattr(jobs, "effective_settings", lambda _engine: KEYED)
    job = cast(Any, type("Job", (), {"job_type": "corpus_embed", "scope": {"limit": "64"}})())
    assert jobs.execute_job(cast(Engine, None), job) == 12
    job.scope = {"limit": "x"}
    jobs.execute_job(cast(Engine, None), job)
    assert calls == [64, None]
