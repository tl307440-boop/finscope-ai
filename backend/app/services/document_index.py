import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from app.core.exceptions import AppError
from app.repositories.document_chunks import ChunkRepository, StoredChunk
from app.repositories.documents import DocumentRepository, OwnedFile
from app.services.pdf_chunks import TextChunk, chunk_pages
from app.services.pdf_text import PdfTextError, extract_pages, has_extractable_text
from app.services.rrf import fuse_rankings
from app.services.text_embed import (
    STORED_EMBEDDING_DIM,
    embed_text,
    search_document,
    search_tokens,
    vector_literal,
)
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

RRF_K = 60
SEARCH_LIMIT = 8
CANDIDATES = 20
INDEX_NOTICE = "当前使用全文检索和本地哈希向量 local-hashing-v1，没有调用外部语义模型。"


@dataclass(frozen=True)
class IndexOutcome:
    status: str
    chunk_count: int
    message: str


@dataclass(frozen=True)
class SearchHit:
    document_id: UUID
    document_title: str
    chunk_id: UUID
    page_start: int
    page_end: int
    snippet: str


def index_owned_document(
    documents: DocumentRepository,
    chunks: ChunkRepository,
    *,
    owner_id: UUID,
    document_id: UUID,
    root: Path,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
    embedding_dim: int,
) -> IndexOutcome:
    current = documents.get_owned(owner_id, document_id)
    if current is None:
        raise AppError(code="NOT_FOUND", message="找不到这份文件", status_code=404)
    if current.parse_status == "parsed":
        return IndexOutcome("parsed", chunks.count_for(document_id), "这份文件已经解析")
    if current.parse_status == "parsing":
        raise AppError(code="CONFLICT", message="这份文件正在解析", status_code=409)
    if current.parse_status != "queued":
        raise AppError(
            code="NOT_INDEXABLE",
            message=current.parse_error or "这份文件不能建立索引",
            status_code=422,
        )
    claimed = documents.claim_owned(owner_id, document_id)
    if claimed is None:
        raise AppError(code="CONFLICT", message="这份文件正在解析", status_code=409)
    return _index_claimed(
        chunks,
        claimed,
        root=root,
        target_tokens=target_tokens,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        embedding_dim=embedding_dim,
    )


def index_next_queued(engine: Engine, root: Path) -> None:
    documents = DocumentRepository(engine)
    resolved = str(root.resolve())
    separator = "\\" if "\\" in resolved else "/"
    prefix = resolved if resolved.endswith(("\\", "/")) else resolved + separator
    claimed = documents.claim_next_queued(prefix)
    if claimed is None:
        return
    try:
        from app.core.config import get_settings

        settings = get_settings()
        _index_claimed(
            ChunkRepository(engine),
            claimed,
            root=root,
            target_tokens=settings.chunk_target_tokens,
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            embedding_dim=settings.embedding_dim,
        )
    except Exception:
        logger.warning("document index failed error_type=unexpected")
        ChunkRepository(engine).finish(claimed.id, "failed", "无法完成解析")


def search_owned_documents(
    chunks: ChunkRepository,
    *,
    owner_id: UUID,
    query: str,
    as_of: datetime,
    limit: int = SEARCH_LIMIT,
) -> list[SearchHit]:
    tokens = search_tokens(query)[:32]
    lexical = " ".join(tokens)
    if not lexical:
        raise AppError(code="VALIDATION_ERROR", message="请输入要检索的文字", status_code=422)
    bounded = min(max(limit, 1), SEARCH_LIMIT)
    by_text = chunks.search_text(
        owner_id=owner_id,
        query=lexical,
        as_of=as_of,
        limit=CANDIDATES,
    )
    embedding = embed_text(query)
    by_vector = (
        []
        if embedding is None
        else chunks.search_vector(
            owner_id=owner_id,
            embedding=vector_literal(embedding),
            as_of=as_of,
            limit=CANDIDATES,
        )
    )
    by_vector = [item for item in by_vector if _shares_token(item.content, tokens)]
    found = {str(item.chunk_id): item for item in by_text}
    for item in by_vector:
        found.setdefault(str(item.chunk_id), item)
    ranked = fuse_rankings(
        [[str(item.chunk_id) for item in by_text], [str(item.chunk_id) for item in by_vector]],
        k=RRF_K,
    )
    hits: list[SearchHit] = []
    seen_hash: set[tuple[UUID, str]] = set()
    for chunk_id in ranked:
        match = found.get(chunk_id)
        if match is None:
            continue
        key = (match.document_id, match.content_hash)
        if key in seen_hash:
            continue
        seen_hash.add(key)
        hits.append(_hit(match, query))
        if len(hits) >= bounded:
            break
    return hits


def _index_claimed(
    chunks: ChunkRepository,
    claimed: OwnedFile,
    *,
    root: Path,
    target_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
    embedding_dim: int,
) -> IndexOutcome:
    try:
        if embedding_dim != STORED_EMBEDDING_DIM:
            raise PdfTextError("向量维度与已固定的索引不一致")
        data = _read_stored(root, claimed.storage_path)
        pages = extract_pages(data)
        if not has_extractable_text(pages):
            chunks.finish(claimed.id, "ocr_required", "没有可抽取的文字，不会建立索引")
            return IndexOutcome("ocr_required", 0, "没有可抽取的文字，不会建立索引")
        prepared = chunk_pages(
            pages,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
        )
        stored, embeddings, search_texts = _vectors(prepared)
        if not stored:
            chunks.finish(claimed.id, "ocr_required", "没有可抽取的文字，不会建立索引")
            return IndexOutcome("ocr_required", 0, "没有可抽取的文字，不会建立索引")
        count = chunks.replace(
            claimed.id,
            stored,
            embeddings,
            search_texts,
            page_count=len(pages),
        )
    except PdfTextError as exc:
        chunks.finish(claimed.id, "failed", str(exc)[:200])
        return IndexOutcome("failed", 0, str(exc))
    except OSError:
        chunks.finish(claimed.id, "failed", "文件不在当前环境，无法解析")
        return IndexOutcome("failed", 0, "文件不在当前环境，无法解析")
    return IndexOutcome("parsed", count, "已按页建立索引，可以检索")


def _read_stored(root: Path, storage_path: str | None) -> bytes:
    if storage_path is None:
        raise PdfTextError("文件不在当前环境，无法解析")
    base = root.resolve()
    path = Path(storage_path).resolve()
    if not path.is_relative_to(base):
        raise PdfTextError("文件不在当前环境，无法解析")
    if not path.is_file():
        raise PdfTextError("文件不在当前环境，无法解析")
    return path.read_bytes()


def _vectors(
    chunks: list[TextChunk],
) -> tuple[list[TextChunk], list[str], list[str]]:
    kept: list[TextChunk] = []
    embeddings: list[str] = []
    search_texts: list[str] = []
    for chunk in chunks:
        vector = embed_text(chunk.content)
        if vector is None:
            continue
        kept.append(chunk)
        embeddings.append(vector_literal(vector))
        search_texts.append(search_document(chunk.content))
    reindexed = [
        TextChunk(
            chunk_index=index,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
            char_start=chunk.char_start,
            char_end=chunk.char_end,
            section_title=chunk.section_title,
            content=chunk.content,
            token_count=chunk.token_count,
            content_hash=chunk.content_hash,
        )
        for index, chunk in enumerate(kept)
    ]
    return reindexed, embeddings, search_texts


def _hit(item: StoredChunk, query: str) -> SearchHit:
    return SearchHit(
        document_id=item.document_id,
        document_title=item.document_title,
        chunk_id=item.chunk_id,
        page_start=item.page_start,
        page_end=item.page_end,
        snippet=_snippet(item.content, query),
    )


def _shares_token(content: str, tokens: list[str]) -> bool:
    haystack = set(search_tokens(content))
    return any(token in haystack for token in tokens)


def _snippet(content: str, query: str, limit: int = 180) -> str:
    folded = content.strip()
    for token in search_document(query).split():
        position = folded.lower().find(token.lower())
        if position >= 0:
            start = max(0, position - 40)
            return folded[start : start + limit]
    return folded[:limit]


def default_as_of() -> datetime:
    return utc_now()
