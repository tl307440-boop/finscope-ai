"""语料检索评测集的装载（知识库设计 §9）。CI 评测与真实模型冒烟脚本共用。

评测集是 `tests/eval/corpus_eval.json`：合成文档 + 带固定关键词的题目。装载时建两个测试用户
（a 拥有一份私有文档，b 用来查泄露）、按发布方建来源，所有数据都带 `t-eval-` 前缀，用完 `cleanup`。
只能对测试库用。
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from app.core.security import PasswordService
from app.repositories.accounts import AccountRepository
from app.repositories.corpus import USER_UPLOAD_SOURCE, CorpusQuery, CorpusRepository, NewDocument
from app.services.corpus_ingest import content_hash, corpus_chunks
from app.services.corpus_text import detect_language
from app.services.pdf_text import PageText

EVAL_FILE = Path(__file__).resolve().parents[2] / "tests" / "eval" / "corpus_eval.json"
PREFIX = "t-eval-"
_SOURCE_KINDS = {"official": "central_bank", "filing": "exchange", "reference": "reference"}


@dataclass(frozen=True)
class Loaded:
    engine: Engine
    spec: dict[str, Any]
    users: dict[str, UUID]
    # corpus_documents.id → 评测集里的文档 id
    names: dict[UUID, str]

    def as_of(self, question: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(question.get("as_of", self.spec["as_of"]))


def read_spec(path: Path = EVAL_FILE) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return spec


def load(engine: Engine, spec: dict[str, Any]) -> Loaded:
    cleanup(engine)
    users = {name: _user(engine, name) for name in ("a", "b")}
    repo = CorpusRepository(engine)
    sources: dict[str, UUID] = {}
    names = {_ingest(engine, repo, doc, users, sources): doc["id"] for doc in spec["documents"]}
    return Loaded(engine, spec, users, names)


def cleanup(engine: Engine) -> None:
    like = {"p": f"{PREFIX}%"}
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM corpus_documents WHERE source_id IN "
                "(SELECT id FROM corpus_sources WHERE code LIKE :p) OR owner_user_id IN "
                "(SELECT id FROM users WHERE username LIKE :p)"
            ),
            like,
        )
        connection.execute(text("DELETE FROM corpus_sources WHERE code LIKE :p"), like)
        connection.execute(
            text(
                "DELETE FROM documents WHERE owner_id IN "
                "(SELECT id FROM users WHERE username LIKE :p)"
            ),
            like,
        )
        connection.execute(text("DELETE FROM users WHERE username LIKE :p"), like)


def query_for(loaded: Loaded, question: dict[str, Any], *, min_rank: float) -> CorpusQuery:
    return CorpusQuery(
        user_id=loaded.users[question["asker"]],
        as_of=loaded.as_of(question),
        keywords_zh=tuple(question.get("keywords_zh", ())),
        keywords_en=tuple(question.get("keywords_en", ())),
        kind=question.get("kind"),
        symbol=question.get("symbol"),
        report_type=question.get("report_type"),
        since=_day(question.get("since")),
        until=_day(question.get("until")),
        min_rank=min_rank,
    )


def documents_of_chunks(engine: Engine, chunk_ids: list[str]) -> dict[str, UUID]:
    if not chunk_ids:
        return {}
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT id::text, document_id FROM corpus_chunks WHERE id::text = ANY(:ids)"),
            {"ids": chunk_ids},
        ).all()
    return {row[0]: row[1] for row in rows}


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _user(engine: Engine, name: str) -> UUID:
    username = f"{PREFIX}{name}-{uuid4().hex[:6]}"
    return (
        AccountRepository(engine)
        .create_user(
            username=username,
            email=f"{username}@example.com",
            password_hash=PasswordService("eval").hash(uuid4().hex),
            role="researcher",
        )
        .id
    )


def _source(engine: Engine, sources: dict[str, UUID], publisher: str, kind: str) -> UUID:
    known = sources.get(publisher)
    if known is not None:
        return known
    source_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO corpus_sources (id, code, publisher, kind, method, allowed_hosts,
                    frequency, store_full_text, terms_note, enabled)
                VALUES (:id, :code, :publisher, :kind, 'fixture', ARRAY[]::text[],
                    'on_demand', true, '评测用合成文本', true)
                """
            ),
            {
                "id": source_id,
                "code": f"{PREFIX}{len(sources)}",
                "publisher": publisher,
                "kind": _SOURCE_KINDS[kind],
            },
        )
    sources[publisher] = source_id
    return source_id


def _ingest(
    engine: Engine,
    repo: CorpusRepository,
    doc: dict[str, Any],
    users: dict[str, UUID],
    sources: dict[str, UUID],
) -> UUID:
    published = datetime.fromisoformat(doc["published_at"])
    pages = [PageText(page_number=n, text=body) for n, body in enumerate(doc["pages"], 1)]
    chunks = corpus_chunks(
        pages,
        title=doc["title"],
        publisher=doc["publisher"],
        symbol=doc.get("symbol"),
        period=doc.get("period"),
        report_type=doc.get("report_type"),
    )
    fields: dict[str, Any] = {
        "kind": doc["kind"],
        "title": doc["title"],
        "doc_type": doc["doc_type"],
        "language": detect_language("\n".join(doc["pages"])),
        "published_at": published,
        "content_hash": content_hash(chunks),
        "symbol": doc.get("symbol"),
        "period": doc.get("period"),
        "report_type": doc.get("report_type"),
    }
    if doc["kind"] == "user":
        owner = users[doc["owner"]]
        upload = _upload(engine, owner, doc["title"], published)
        return repo.replace_user_document(
            NewDocument(
                source_id=USER_UPLOAD_SOURCE,
                canonical_url=f"user-document:{upload}",
                authority="user",
                owner_user_id=owner,
                user_document_id=upload,
                **fields,
            ),
            chunks,
        )
    saved = repo.save(
        NewDocument(
            source_id=_source(engine, sources, doc["publisher"], doc["kind"]),
            canonical_url=f"https://eval.invalid/{doc['id']}",
            authority=doc["kind"],
            **fields,
        ),
        chunks,
    )
    if saved is None:
        raise RuntimeError(f"评测文档没有写入：{doc['id']}")
    return saved


def _upload(engine: Engine, owner: UUID, title: str, at: datetime) -> UUID:
    document_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO documents (id, owner_id, title, storage_path, sha256, mime_type,
                    page_count, size_bytes, uploaded_at, parse_status)
                VALUES (:id, :owner, :title, NULL, :sha, 'application/pdf', 1, 10, :at, 'parsed')
                """
            ),
            {"id": document_id, "owner": owner, "title": title, "sha": uuid4().hex * 2, "at": at},
        )
    return document_id
