"""语料向量（迁移 0035）：待补清单的范围、空向量记原因、混合检索在真库上的 SQL 与过滤。

向量是手造的 1024 维单位向量，不调外部服务。测试数据用独有的来源代码和用户名前缀，结束时清掉。
"""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.repositories.accounts import AccountRepository
from app.repositories.corpus import (
    CorpusQuery,
    CorpusRepository,
    NewChunk,
    NewDocument,
    QueryVector,
)
from app.repositories.corpus_embeddings import ChunkVector, CorpusEmbeddingRepository
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

SOURCE_CODE = "t-vector-src"
MODEL = "embedding-3@1024"
NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM corpus_documents WHERE source_id IN "
                "(SELECT id FROM corpus_sources WHERE code = :c)"
            ),
            {"c": SOURCE_CODE},
        )
        connection.execute(text("DELETE FROM corpus_sources WHERE code = :c"), {"c": SOURCE_CODE})
        connection.execute(text("DELETE FROM users WHERE username LIKE 't-vector-%'"))


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    created = create_db_engine(get_settings().database_url)
    _cleanup(created)
    yield created
    _cleanup(created)
    created.dispose()


@pytest.fixture
def source(engine: Engine) -> UUID:
    source_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO corpus_sources (id, code, publisher, kind, method, allowed_hosts,
                    frequency, store_full_text, terms_note, enabled)
                VALUES (:id, :code, '中国人民银行', 'central_bank', 'html',
                    ARRAY['www.pbc.gov.cn'], 'hourly', true, '测试', true)
                """
            ),
            {"id": source_id, "code": SOURCE_CODE},
        )
    return source_id


def _user(engine: Engine) -> UUID:
    username = f"t-vector-{uuid4().hex[:8]}"
    return (
        AccountRepository(engine)
        .create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("test").hash("correct-password"),
            role="researcher",
        )
        .id
    )


def _save(
    engine: Engine,
    source: UUID,
    title: str,
    content: str,
    *,
    days_ago: int = 1,
    url: str | None = None,
    owner: UUID | None = None,
) -> UUID:
    document = NewDocument(
        source_id=source,
        canonical_url=url or f"https://www.pbc.gov.cn/{uuid4().hex}.html",
        kind="user" if owner else "official",
        title=title,
        doc_type="press",
        language="zh",
        published_at=NOW - timedelta(days=days_ago),
        content_hash=hashlib.sha256(f"{title}{content}".encode()).hexdigest(),
        authority="user" if owner else "official",
        owner_user_id=owner,
    )
    chunk = NewChunk(content=content, char_start=0, char_end=len(content), token_count=10)
    document_id = CorpusRepository(engine).save(document, [chunk])
    assert document_id is not None
    with engine.connect() as connection:
        chunk_id: UUID = connection.execute(
            text("SELECT id FROM corpus_chunks WHERE document_id = :d"), {"d": document_id}
        ).scalar_one()
    return chunk_id


def _axis(position: int) -> list[float]:
    values = [0.0] * 1024
    values[position] = 1.0
    return values


def test_pending_lists_only_the_latest_public_chunks_newest_first(
    engine: Engine, source: UUID
) -> None:
    old = _save(engine, source, "旧闻", "去年的公开市场操作", days_ago=300)
    new = _save(engine, source, "新闻", "今天的公开市场操作", days_ago=1)
    url = "https://www.pbc.gov.cn/versioned.html"
    _save(engine, source, "改版前", "第一版正文", url=url, days_ago=2)
    latest = _save(engine, source, "改版后", "第二版正文", url=url, days_ago=2)
    private = _save(engine, source, "我的笔记", "用户自己的资料", owner=_user(engine))
    repo = CorpusEmbeddingRepository(engine)

    pending = [item.chunk_id for item in repo.pending(MODEL, 50)]
    mine = [chunk for chunk in pending if chunk in {old, new, latest, private}]
    assert mine == [new, latest, old]  # 用户文档不发给外部服务；旧版本不补
    assert private not in pending

    repo.save(MODEL, [ChunkVector(new, _axis(0)), ChunkVector(old, None, 0, "内容审核")], NOW)
    remaining = {item.chunk_id for item in repo.pending(MODEL, 50)}
    assert new not in remaining and old not in remaining and latest in remaining
    assert latest in {item.chunk_id for item in repo.pending("other-model@1024", 50)}


def test_a_skipped_chunk_must_say_why(engine: Engine, source: UUID) -> None:
    chunk = _save(engine, source, "标题", "正文")
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO corpus_embeddings (chunk_id, embedding_model, embedded_at) "
                "VALUES (:c, :m, now())"
            ),
            {"c": chunk, "m": MODEL},
        )


def test_hybrid_search_finds_a_synonym_and_respects_time_and_threshold(
    engine: Engine, source: UUID
) -> None:
    rrr = _save(
        engine, source, "人民银行决定下调存款准备金率", "下调金融机构存款准备金率0.5个百分点"
    )
    other = _save(engine, source, "人民银行开展逆回购操作", "以利率招标方式开展逆回购操作")
    CorpusEmbeddingRepository(engine).save(
        MODEL, [ChunkVector(rrr, _axis(0)), ChunkVector(other, _axis(1))], NOW
    )
    user = _user(engine)
    near_rrr = QueryVector(
        MODEL, tuple(0.96 if i == 0 else 0.28 if i == 1 else 0.0 for i in range(1024))
    )
    repo = CorpusRepository(engine)

    # 「降准」两个字不在正文里：全文检索查不到，语义检索把它找回来；逆回购相似度不够被挡掉。
    assert repo.search(CorpusQuery(user_id=user, as_of=NOW, keywords_zh=("降准",))) == []
    found = repo.search(
        CorpusQuery(
            user_id=user, as_of=NOW, keywords_zh=("降准",), vector=near_rrr, min_similarity=0.55
        )
    )
    assert [item.title for item in found] == ["人民银行决定下调存款准备金率"]
    assert found[0].similarity == pytest.approx(0.96, abs=0.01)
    # 时间旅行：发布时间晚于 as_of 的片段，向量检索同样看不到。
    early = CorpusQuery(
        user_id=user,
        as_of=NOW - timedelta(days=2),
        keywords_zh=("降准",),
        vector=near_rrr,
    )
    assert repo.search(early) == []
