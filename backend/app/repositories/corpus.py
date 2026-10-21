"""语料库读写（规格 §10–§12，知识库设计 §4、§6）。

写：同一来源、同一地址，内容哈希没变就跳过，变了记新版本，旧版本保留；
用户文档是「我的文档」的副本，每次重新解析整份替换。
读：只取每个地址的最新版本；先按可见性、时间、类型过滤，再按全文相关度排序，
同一篇最多 2 块，取前 8 块；最高相关度低于阈值时返回空，不拿低相关片段凑数。
检索词里带时间意图（最近、最新、今年……）时，在通过阈值的候选里再按发布时间的新旧加权重排
（`corpus_freshness`）；阈值比的是原始相关度，没有时间词时不做任何衰减。
带查询向量时是混合检索（规格 §12 3b）：全文前 40、向量前 40 各自过阈值，再按 RRF 融合；
两路都没有过阈值的结果才算没查到。没有向量时行为与纯全文检索完全一样。
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

from app.services.corpus_freshness import decayed_score, has_time_intent, strip_time_words
from app.services.corpus_text import en_query_terms, index_text, ts_config, zh_query_terms
from app.services.rrf import rrf_scores
from app.services.text_embed import vector_literal
from app.utils.market_time import SHANGHAI
from app.utils.time import utc_now

USER_UPLOAD_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a1")
CNINFO_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a2")
SEC_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a3")
FED_SPEECHES_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a4")
FED_PRESS_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a5")
FED_TESTIMONY_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a6")
NBS_RELEASE_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a7")
SZSE_NEWS_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a8")
SSE_SUPERVISION_SOURCE = UUID("00000000-0000-4000-8000-00000000c0a9")
GOV_POLICY_SOURCE = UUID("00000000-0000-4000-8000-00000000c0aa")
CSRC_NEWS_SOURCE = UUID("00000000-0000-4000-8000-00000000c0ab")
MAX_PASSAGES = 8
PER_DOCUMENT = 2
# 带时间意图时先按原始相关度取这么多候选，再按衰减后的分数重排取前 MAX_PASSAGES。
FRESHNESS_CANDIDATES = MAX_PASSAGES * 5
SEMANTIC_CANDIDATES = 40
# HNSW 先取近邻再套过滤条件（as_of、可见性、类别），放大候选数，过滤后才还剩得下 40 条。
HNSW_EF_SEARCH = 200
_MODEL_ID = re.compile(r"[A-Za-z0-9._-]{1,60}@([0-9]{1,5})")


@dataclass(frozen=True)
class QueryVector:
    """检索词的向量；`model_id` 必须是迁移里建过部分索引的那个，例如 `embedding-3@1024`。"""

    model_id: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class NewChunk:
    content: str
    char_start: int
    char_end: int
    token_count: int
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    context_header: str = ""


@dataclass(frozen=True)
class NewDocument:
    source_id: UUID
    canonical_url: str
    kind: str
    title: str
    doc_type: str
    language: str
    published_at: datetime
    content_hash: str
    authority: str
    owner_user_id: UUID | None = None
    user_document_id: UUID | None = None
    instrument_id: UUID | None = None
    symbol: str | None = None
    period: str | None = None
    report_type: str | None = None
    summary: str | None = None
    license_note: str | None = None


@dataclass(frozen=True)
class CorpusQuery:
    user_id: UUID
    as_of: datetime
    keywords_zh: tuple[str, ...] = ()
    keywords_en: tuple[str, ...] = ()
    kind: str | None = None
    symbol: str | None = None
    report_type: str | None = None
    period: str | None = None
    publisher: str | None = None
    since: date | None = None
    until: date | None = None
    min_rank: float = 0.0
    limit: int = MAX_PASSAGES
    vector: QueryVector | None = None
    min_similarity: float = 0.0


@dataclass(frozen=True)
class Passage:
    chunk_id: UUID
    document_id: UUID
    title: str
    publisher: str
    kind: str
    doc_type: str
    url: str
    published_at: datetime
    content: str
    rank: float
    section_title: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    symbol: str | None = None
    period: str | None = None
    # 排序用的分数：纯全文检索时等于 `rank`（原始相关度），混合检索时是 RRF 分数；
    # 按时间加权后是衰减后的分数。
    score: float = 0.0
    # 与检索词向量的余弦相似度；不是向量召回的片段为 None。
    similarity: float | None = None


@dataclass
class _Params:
    values: dict[str, Any] = field(default_factory=dict)

    def bind(self, name: str, value: Any) -> str:
        key = f"{name}{len(self.values)}"
        self.values[key] = value
        return f":{key}"


class CorpusRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    # ------------------------------------------------------------------ 写
    def save(self, document: NewDocument, chunks: Sequence[NewChunk]) -> UUID | None:
        """登记一篇文档；内容哈希与最新版本相同返回 None（没有写入）。"""

        with self.engine.begin() as connection:
            latest = connection.execute(
                text(
                    """
                    SELECT version, content_hash FROM corpus_documents
                    WHERE source_id = :source AND canonical_url = :url
                    ORDER BY version DESC LIMIT 1
                    """
                ),
                {"source": document.source_id, "url": document.canonical_url},
            ).first()
            if latest is not None and latest.content_hash == document.content_hash:
                return None
            version = 1 if latest is None else int(latest.version) + 1
            return _insert(connection, document, chunks, version)

    def replace_user_document(self, document: NewDocument, chunks: Sequence[NewChunk]) -> UUID:
        if document.user_document_id is None or document.owner_user_id is None:
            raise ValueError("用户文档副本必须带原文件和上传者")
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM corpus_documents WHERE user_document_id = :doc"),
                {"doc": document.user_document_id},
            )
            return _insert(connection, document, chunks, 1)

    def latest_passages(
        self, *, source_id: UUID, url: str, limit: int = MAX_PASSAGES
    ) -> list[Passage]:
        """某篇公告最新版本的前几块，按原文顺序。还没抓过就返回空。"""

        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.id, c.document_id, d.title, s.publisher, d.kind, d.doc_type,
                        d.canonical_url, d.published_at, c.content, 0 AS rank, c.section_title,
                        c.page_start, c.page_end, d.symbol, d.period
                    FROM corpus_chunks c
                    JOIN corpus_documents d ON d.id = c.document_id
                    JOIN corpus_sources s ON s.id = d.source_id
                    WHERE d.source_id = :source AND d.canonical_url = :url
                        AND d.version = (
                            SELECT max(version) FROM corpus_documents
                            WHERE source_id = :source AND canonical_url = :url
                        )
                    ORDER BY c.chunk_index
                    LIMIT :limit
                    """
                ),
                {"source": source_id, "url": url, "limit": limit},
            ).mappings()
            return [_passage(row) for row in rows]

    def drop_user_document(self, user_document_id: UUID) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM corpus_documents WHERE user_document_id = :doc"),
                {"doc": user_document_id},
            )

    # ------------------------------------------------------------------ 读
    def search(self, query: CorpusQuery) -> list[Passage]:
        keywords_zh, keywords_en = query.keywords_zh, query.keywords_en
        fresh = has_time_intent([*keywords_zh, *keywords_en])
        if fresh:
            # 时间词只用来决定要不要按新旧加权，不参与全文匹配；全是时间词时保持原样。
            plain_zh, plain_en = strip_time_words(keywords_zh), strip_time_words(keywords_en)
            if plain_zh or plain_en:
                keywords_zh, keywords_en = plain_zh, plain_en
        zh_terms = zh_query_terms([*keywords_zh, *keywords_en])
        en_terms = en_query_terms(keywords_en)
        lexical_terms = bool(zh_terms or en_terms)
        if not lexical_terms and query.vector is None:
            return []
        limit = max(1, min(query.limit, MAX_PASSAGES))
        candidates = FRESHNESS_CANDIDATES if fresh or query.vector is not None else limit
        params = _Params()
        filters = self._filters(params, query)
        with self.engine.connect() as connection:
            rows: Sequence[Any] = []
            if lexical_terms:
                rows = self._lexical(connection, params, filters, zh_terms, en_terms, candidates)
            semantic = (
                []
                if query.vector is None
                else self._semantic(connection, params, filters, query.vector)
            )
        # 行已按原始相关度从高到低排好：阈值闸门看的是最高的原始相关度，和有没有衰减无关。
        lexical = (
            [_passage(row) for row in rows]
            if rows and float(rows[0]["rank"]) >= query.min_rank
            else []
        )
        if query.vector is None:
            passages = lexical
        else:
            kept = [
                item
                for item in semantic
                if item.similarity is not None and item.similarity >= query.min_similarity
            ]
            passages = fuse_passages(lexical, kept)
        if not passages:
            return []
        if fresh:
            passages = _by_freshness(passages, query.as_of)
        return passages[:limit]

    @staticmethod
    def _filters(params: "_Params", query: CorpusQuery) -> list[str]:
        filters = [
            "d.status = 'indexed'",
            f"d.published_at <= {params.bind('as_of', query.as_of)}",
            f"(d.owner_user_id IS NULL OR d.owner_user_id = {params.bind('user', query.user_id)})",
            """d.version = (
                SELECT max(v.version) FROM corpus_documents AS v
                WHERE v.source_id = d.source_id AND v.canonical_url = d.canonical_url
            )""",
        ]
        for column, value in (
            ("d.kind", query.kind),
            ("d.symbol", query.symbol),
            ("d.report_type", query.report_type),
            ("d.period", query.period),
        ):
            if value is not None:
                filters.append(f"{column} = {params.bind('f', value)}")
        if query.publisher is not None:
            filters.append(f"s.publisher = {params.bind('publisher', query.publisher)}")
        if query.since is not None:
            filters.append(f"d.published_at >= {params.bind('since', query.since)}")
        if query.until is not None:
            # `:name::date` 会让 SQLAlchemy 认不出绑定参数，所以用 CAST。
            until = params.bind("until", query.until)
            filters.append(f"d.published_at < (CAST({until} AS date) + 1)")
        return filters

    @staticmethod
    def _lexical(
        connection: Connection,
        params: "_Params",
        filters: list[str],
        zh_terms: Sequence[str],
        en_terms: Sequence[str],
        limit: int,
    ) -> Sequence[Any]:
        zh = _any_of(params, "simple", zh_terms)
        en = _any_of(params, "english", en_terms)
        statement = f"""
            WITH q AS (SELECT {zh} AS zq, {en} AS eq),
            hits AS (
                SELECT c.id, c.document_id, c.content, c.section_title, c.page_start, c.page_end,
                       d.title, d.kind, d.doc_type, d.canonical_url, d.published_at,
                       d.symbol, d.period, s.publisher,
                       ts_rank_cd(c.tsv, CASE WHEN d.language = 'en' THEN q.eq ELSE q.zq END, 32)
                         AS rank
                FROM corpus_chunks AS c
                JOIN corpus_documents AS d ON d.id = c.document_id
                JOIN corpus_sources AS s ON s.id = d.source_id
                CROSS JOIN q
                WHERE {" AND ".join(filters)}
                  AND ((d.language = 'en' AND c.tsv @@ q.eq)
                       OR (d.language <> 'en' AND c.tsv @@ q.zq))
            ),
            ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY document_id ORDER BY rank DESC, id
                ) AS nth
                FROM hits
            )
            SELECT * FROM ranked
            WHERE nth <= {PER_DOCUMENT}
            ORDER BY rank DESC, published_at DESC, id
            LIMIT {params.bind("limit", limit)}
        """
        return connection.execute(text(statement), params.values).mappings().all()

    @staticmethod
    def _semantic(
        connection: Connection, params: "_Params", filters: list[str], vector: QueryVector
    ) -> list[Passage]:
        """向量近邻，按相似度从高到低，每篇最多 PER_DOCUMENT 块。

        模型标识和维度直接写进 SQL：查询必须和部分索引的表达式、条件逐字一致才走得到索引，
        绑定参数会让规划器认不出来。写进去之前先按格式校验，不接受任意字符串。
        """

        matched = _MODEL_ID.fullmatch(vector.model_id)
        if matched is None or int(matched.group(1)) != len(vector.values):
            raise ValueError("查询向量的模型标识或维度不对")
        dims = len(vector.values)
        distance = (
            f"e.embedding::halfvec({dims}) <=> "
            f"CAST({params.bind('qv', vector_literal(list(vector.values)))} AS halfvec({dims}))"
        )
        statement = f"""
            SELECT c.id, c.document_id, c.content, c.section_title, c.page_start, c.page_end,
                   d.title, d.kind, d.doc_type, d.canonical_url, d.published_at,
                   d.symbol, d.period, s.publisher, 0 AS rank, 1 - ({distance}) AS similarity
            FROM corpus_embeddings AS e
            JOIN corpus_chunks AS c ON c.id = e.chunk_id
            JOIN corpus_documents AS d ON d.id = c.document_id
            JOIN corpus_sources AS s ON s.id = d.source_id
            WHERE e.embedding_model = '{vector.model_id}' AND e.embedding IS NOT NULL
              AND {" AND ".join(filters)}
            ORDER BY {distance}
            LIMIT {SEMANTIC_CANDIDATES}
        """
        connection.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
        rows = connection.execute(text(statement), params.values).mappings().all()
        return _per_document([_passage(row) for row in rows])


def semantic_query_text(keywords_zh: Sequence[str], keywords_en: Sequence[str]) -> str:
    """拿去向量化的检索词：关键词用空格连起来；时间词只管排序，不进向量。"""

    words = [word.strip() for word in [*keywords_zh, *keywords_en] if word.strip()]
    plain = strip_time_words(words) if has_time_intent(words) else tuple(words)
    return " ".join(plain or words)


def fuse_passages(lexical: Sequence[Passage], semantic: Sequence[Passage]) -> list[Passage]:
    """两路各自排好序（全文按相关度、向量按相似度），按 RRF（k = 60）融合。

    同一块两路都有时保留全文的相关度和向量的相似度；`score` 是融合分。
    分数一样时新的在前，再一样按块编号，结果是确定的；每篇最多 PER_DOCUMENT 块。
    """

    lexical, semantic = _per_document(lexical), _per_document(semantic)
    scores = rrf_scores(
        [[str(item.chunk_id) for item in lexical], [str(item.chunk_id) for item in semantic]]
    )
    similarity = {str(item.chunk_id): item.similarity for item in semantic}
    merged: dict[str, Passage] = {}
    for item in [*lexical, *semantic]:
        key = str(item.chunk_id)
        if key not in merged:
            merged[key] = replace(item, similarity=similarity.get(key), score=scores[key])
    ordered = sorted(
        merged.values(),
        key=lambda item: (-item.score, -item.published_at.timestamp(), str(item.chunk_id)),
    )
    return _per_document(ordered)


def _per_document(passages: Sequence[Passage]) -> list[Passage]:
    taken: dict[UUID, int] = {}
    kept: list[Passage] = []
    for item in passages:
        count = taken.get(item.document_id, 0)
        if count < PER_DOCUMENT:
            taken[item.document_id] = count + 1
            kept.append(item)
    return kept


def _insert(
    connection: Connection, document: NewDocument, chunks: Sequence[NewChunk], version: int
) -> UUID:
    document_id = uuid.uuid4()
    connection.execute(
        text(
            """
            INSERT INTO corpus_documents (
                id, source_id, canonical_url, version, kind, title, doc_type, language,
                published_at, retrieved_at, content_hash, has_full_text, summary, status,
                authority, owner_user_id, user_document_id, instrument_id, symbol, period,
                report_type, license_note
            ) VALUES (
                :id, :source_id, :canonical_url, :version, :kind, :title, :doc_type, :language,
                :published_at, :retrieved_at, :content_hash, :has_full_text, :summary, :status,
                :authority, :owner_user_id, :user_document_id, :instrument_id, :symbol, :period,
                :report_type, :license_note
            )
            """
        ),
        {
            "id": document_id,
            "source_id": document.source_id,
            "canonical_url": document.canonical_url,
            "version": version,
            "kind": document.kind,
            "title": document.title,
            "doc_type": document.doc_type,
            "language": document.language,
            "published_at": document.published_at,
            "retrieved_at": utc_now(),
            "content_hash": document.content_hash,
            "has_full_text": bool(chunks),
            "summary": document.summary,
            "status": "indexed" if chunks else "metadata_only",
            "authority": document.authority,
            "owner_user_id": document.owner_user_id,
            "user_document_id": document.user_document_id,
            "instrument_id": document.instrument_id,
            "symbol": document.symbol,
            "period": document.period,
            "report_type": document.report_type,
            "license_note": document.license_note,
        },
    )
    config = ts_config(document.language)
    title_text = index_text(document.title, document.language)
    for index, chunk in enumerate(chunks):
        connection.execute(
            text(
                """
                INSERT INTO corpus_chunks (
                    id, document_id, chunk_index, section_title, page_start, page_end,
                    char_start, char_end, context_header, content, token_count, tsv
                ) VALUES (
                    :id, :document_id, :chunk_index, :section_title, :page_start, :page_end,
                    :char_start, :char_end, :context_header, :content, :token_count,
                    setweight(to_tsvector(CAST(:config AS regconfig), :title_text), 'A')
                    || setweight(to_tsvector(CAST(:config AS regconfig), :header_text), 'B')
                    || setweight(to_tsvector(CAST(:config AS regconfig), :content_text), 'D')
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "document_id": document_id,
                "chunk_index": index,
                "section_title": chunk.section_title,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "context_header": chunk.context_header,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "config": config,
                "title_text": title_text,
                "header_text": index_text(chunk.context_header, document.language),
                "content_text": index_text(chunk.content, document.language),
            },
        )
    return document_id


def _any_of(params: _Params, config: str, terms: Sequence[str]) -> str:
    """每个关键词一个 `plainto_tsquery`（组内的词同时出现），关键词之间任一命中即可。"""

    if not terms:
        return "NULL::tsquery"
    parts = [f"plainto_tsquery('{config}', {params.bind('kw', term)})" for term in terms]
    return "(" + " || ".join(parts) + ")"


def _by_freshness(passages: list[Passage], as_of: datetime) -> list[Passage]:
    """按衰减后的分数重排；分数一样时新的在前，再一样按块编号，结果是确定的。"""

    today = _local_day(as_of)
    scored = [
        replace(
            item,
            score=decayed_score(
                item.score,
                _local_day(item.published_at),
                today,
                kind=item.kind,
                doc_type=item.doc_type,
            ),
        )
        for item in passages
    ]
    scored.sort(key=lambda item: (-item.score, -item.published_at.timestamp(), str(item.chunk_id)))
    return scored


def _local_day(moment: datetime) -> date:
    """上海日历日；没有时区的时间按 UTC 算。"""

    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(SHANGHAI).date()


def _passage(row: Any) -> Passage:
    url = str(row["canonical_url"])
    return Passage(
        chunk_id=row["id"],
        document_id=row["document_id"],
        title=row["title"],
        publisher=row["publisher"],
        kind=row["kind"],
        doc_type=row["doc_type"],
        # 用户文档的地址是内部标识，不给链接。
        url=url if url.startswith(("https://", "http://")) else "",
        published_at=row["published_at"],
        content=row["content"],
        rank=float(row["rank"]),
        section_title=row["section_title"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        symbol=row["symbol"],
        period=row["period"],
        score=float(row["rank"]),
        similarity=float(row["similarity"]) if row.get("similarity") is not None else None,
    )
