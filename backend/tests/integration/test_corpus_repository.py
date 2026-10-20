"""语料库读写（规格 §10–§12）：版本、可见性、中英文全文检索、过滤、每篇限块、时间衰减。

测试数据用独有的来源代码和用户名前缀，结束时清掉。
"""

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
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
    USER_UPLOAD_SOURCE,
    CorpusQuery,
    CorpusRepository,
    NewChunk,
    NewDocument,
)
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import CorpusSearchArgs, CorpusSearchTool
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

SOURCE_CODE = "t-corpus-src"
NOW = datetime(2026, 10, 20, 4, 0, tzinfo=UTC)


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


@pytest.fixture
def engine() -> Iterator[Engine]:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    created = create_db_engine(get_settings().database_url)
    _cleanup(created)
    yield created
    _cleanup(created)
    created.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM corpus_documents WHERE source_id IN "
                "(SELECT id FROM corpus_sources WHERE code = :c) OR owner_user_id IN "
                "(SELECT id FROM users WHERE username LIKE 't-corpus-%')"
            ),
            {"c": SOURCE_CODE},
        )
        connection.execute(text("DELETE FROM corpus_sources WHERE code = :c"), {"c": SOURCE_CODE})
        connection.execute(
            text(
                "DELETE FROM documents WHERE owner_id IN "
                "(SELECT id FROM users WHERE username LIKE 't-corpus-%')"
            )
        )
        connection.execute(text("DELETE FROM users WHERE username LIKE 't-corpus-%'"))


@pytest.fixture
def source(engine: Engine) -> UUID:
    source_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO corpus_sources (id, code, publisher, kind, method, allowed_hosts,
                    frequency, store_full_text, terms_note, enabled)
                VALUES (:id, :code, '美联储', 'central_bank', 'rss',
                    ARRAY['www.federalreserve.gov'], 'hourly', true, '测试', true)
                """
            ),
            {"id": source_id, "code": SOURCE_CODE},
        )
    return source_id


@pytest.fixture
def make_user(engine: Engine) -> Callable[[], UUID]:
    def create() -> UUID:
        username = f"t-corpus-{uuid4().hex[:8]}"
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

    return create


def _doc(
    source: UUID, url: str, title: str, *, language: str = "en", **extra: object
) -> NewDocument:
    body = f"{url}{title}{extra}"
    fields: dict[str, object] = {
        "source_id": source,
        "canonical_url": url,
        "kind": "official",
        "title": title,
        "doc_type": "statement",
        "language": language,
        "published_at": NOW - timedelta(days=1),
        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        "authority": "official",
    }
    fields.update(extra)
    return NewDocument(**fields)  # type: ignore[arg-type]


def _chunk(content: str, header: str = "") -> NewChunk:
    return NewChunk(
        content=content,
        char_start=0,
        char_end=len(content),
        token_count=len(content.split()),
        context_header=header,
    )


def _query(user: UUID, **kwargs: object) -> CorpusQuery:
    return CorpusQuery(user_id=user, as_of=NOW, **kwargs)  # type: ignore[arg-type]


def test_english_search_uses_stemming_and_returns_latest_version(
    engine: Engine, source: UUID, make_user: Callable[[], UUID]
) -> None:
    repo = CorpusRepository(engine)
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260917a.htm"
    first = _doc(source, url, "Federal Reserve issues FOMC statement")
    assert repo.save(first, [_chunk("The Committee decided to maintain the target range.")])
    assert repo.save(first, [_chunk("ignored")]) is None  # 同哈希：不出新版本
    second = _doc(source, url, "Federal Reserve issues FOMC statement", content_hash="b" * 64)
    assert repo.save(second, [_chunk("The Committee decided to lower the target range.")])
    user = make_user()
    found = repo.search(_query(user, keywords_en=("lowering rates", "target ranges")))
    assert [p.content for p in found] == ["The Committee decided to lower the target range."]
    assert found[0].url == url and found[0].publisher == "美联储"
    # 中文关键词查不到英文文档：要求模型给英文关键词（R21）。
    assert repo.search(_query(user, keywords_zh=("目标区间",))) == []


def test_chinese_bigrams_match_anywhere_in_long_chunks(
    engine: Engine, source: UUID, make_user: Callable[[], UUID]
) -> None:
    repo = CorpusRepository(engine)
    filler = "，".join(["前三季度营业收入同比增长"] * 30)
    content = f"{filler}。本期归属于上市公司股东的净利润预计亏损，主要原因是存货跌价准备。"
    repo.save(
        _doc(source, "https://www.cninfo.com.cn/a.pdf", "2026年前三季度业绩预告", language="zh"),
        [_chunk(content)],
    )
    user = make_user()
    # 旧索引只留前 32 个词；这里的命中词在块的后半段。
    found = repo.search(_query(user, keywords_zh=("存货跌价",)))
    assert len(found) == 1
    assert repo.search(_query(user, keywords_zh=("商誉减值",))) == []
    # 中文文档里的英文缩写也能用英文关键词查到。
    repo.save(
        _doc(source, "https://www.cninfo.com.cn/b.pdf", "期权周报", language="zh"),
        [_chunk("沪深300期权成交量 PCR 回落。")],
    )
    assert [p.title for p in repo.search(_query(user, keywords_en=("PCR",)))] == ["期权周报"]


def test_user_documents_are_private_and_replaced_whole(
    engine: Engine, make_user: Callable[[], UUID]
) -> None:
    owner, stranger = make_user(), make_user()
    document_id = _upload(engine, owner)
    repo = CorpusRepository(engine)
    upload = NewDocument(
        source_id=USER_UPLOAD_SOURCE,
        canonical_url=f"user-document:{document_id}",
        kind="user",
        title="我的调研纪要",
        doc_type="upload",
        language="zh",
        published_at=NOW - timedelta(days=400),
        content_hash="c" * 64,
        authority="user",
        owner_user_id=owner,
        user_document_id=document_id,
    )
    repo.replace_user_document(upload, [_chunk("管理层表示产能爬坡顺利。")])
    repo.replace_user_document(upload, [_chunk("管理层表示良率提升。")])
    mine = repo.search(_query(owner, keywords_zh=("良率",)))
    assert [p.content for p in mine] == ["管理层表示良率提升。"]
    assert mine[0].url == "" and mine[0].publisher == "你上传的文档"
    assert repo.search(_query(owner, keywords_zh=("产能爬坡",))) == []  # 旧副本整份替换
    assert repo.search(_query(stranger, keywords_zh=("良率",))) == []
    # 原文件删除时副本跟着删除。
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM documents WHERE id = :id"), {"id": document_id})
    assert repo.search(_query(owner, keywords_zh=("良率",))) == []


def test_filters_time_window_and_per_document_cap(
    engine: Engine, source: UUID, make_user: Callable[[], UUID]
) -> None:
    repo = CorpusRepository(engine)
    many = [_chunk(f"Inflation remains elevated, item {index}.") for index in range(5)]
    repo.save(_doc(source, "https://www.federalreserve.gov/a", "Minutes"), many)
    repo.save(
        _doc(
            source,
            "https://www.federalreserve.gov/future",
            "Future speech on inflation",
            published_at=NOW + timedelta(days=1),
        ),
        [_chunk("Inflation outlook.")],
    )
    repo.save(
        _doc(
            source,
            "https://www.federalreserve.gov/old",
            "Old speech on inflation",
            published_at=NOW - timedelta(days=200),
            doc_type="speech",
        ),
        [_chunk("Inflation was transitory.")],
    )
    user = make_user()
    found = repo.search(_query(user, keywords_en=("inflation",)))
    titles = [p.title for p in found]
    assert "Future speech on inflation" not in titles  # 晚于 as_of 的不给（不前视）
    assert titles.count("Minutes") == 2  # 同一篇最多 2 块
    # 时间衰减：200 天前的讲话排在昨天的纪要后面。
    assert titles.index("Old speech on inflation") > titles.index("Minutes")
    recent = repo.search(_query(user, keywords_en=("inflation",), since=date(2026, 10, 1)))
    assert {p.title for p in recent} == {"Minutes"}
    old = repo.search(_query(user, keywords_en=("inflation",), until=date(2026, 5, 1)))
    assert {p.title for p in old} == {"Old speech on inflation"}
    assert repo.search(_query(user, keywords_en=("inflation",), kind="filing")) == []
    assert repo.search(_query(user, keywords_en=("inflation",), min_rank=10.0)) == []


def test_corpus_search_tool_returns_dated_passages_for_the_asker_only(
    engine: Engine, make_user: Callable[[], UUID]
) -> None:
    owner, stranger = make_user(), make_user()
    document_id = _upload(engine, owner)
    CorpusRepository(engine).replace_user_document(
        NewDocument(
            source_id=USER_UPLOAD_SOURCE,
            canonical_url=f"user-document:{document_id}",
            kind="user",
            title="我的调研纪要",
            doc_type="upload",
            language="zh",
            # 世界时 10 月 19 日 18 点，上海已经是 20 日。
            published_at=datetime(2026, 10, 19, 18, 0, tzinfo=UTC),
            content_hash="e" * 64,
            authority="user",
            owner_user_id=owner,
            user_document_id=document_id,
        ),
        [
            NewChunk(
                content="管理层表示良率提升到 92%。",
                char_start=0,
                char_end=14,
                token_count=14,
                page_start=3,
                page_end=3,
            )
        ],
    )
    tool = CorpusSearchTool()
    args = CorpusSearchArgs(keywords_zh=[" 良率 "])

    result = tool.run(ToolContext(engine=engine, user_id=owner, now=NOW), args)
    assert (result.status, result.title, result.as_of) == ("ok", "良率", date(2026, 10, 20))
    [passage] = result.passages
    assert (passage.title, passage.corpus_kind, passage.published, passage.page_start) == (
        "我的调研纪要",
        "user",
        date(2026, 10, 20),
        3,
    )
    assert passage.content == "管理层表示良率提升到 92%。"

    hidden = tool.run(ToolContext(engine=engine, user_id=stranger, now=NOW), args)
    assert (hidden.status, hidden.passages, hidden.source) == ("unavailable", (), "本系统语料库")


def _upload(engine: Engine, owner: UUID) -> UUID:
    document_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO documents (id, owner_id, title, storage_path, sha256, mime_type,
                    page_count, size_bytes, uploaded_at, parse_status)
                VALUES (:id, :owner, '我的调研纪要', NULL, :sha, 'application/pdf',
                    1, 10, :at, 'parsed')
                """
            ),
            {"id": document_id, "owner": owner, "sha": "d" * 64, "at": NOW},
        )
    return document_id
