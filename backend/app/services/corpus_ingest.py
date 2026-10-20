"""把解析好的文本写进语料库（知识库设计 §5）。

第 3a-1 期只有用户文档：「我的文档」解析成功后另写一份副本（`kind=user`），
按语料库自己的分块参数切块、带块头，只对上传者可见。抓取类来源（公告、美联储）在 3a-2 / 3a-3 接入。
"""

import hashlib
from zoneinfo import ZoneInfo

from app.repositories.corpus import USER_UPLOAD_SOURCE, CorpusRepository, NewChunk, NewDocument
from app.repositories.documents import OwnedFile
from app.services.corpus_text import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
    context_header,
    detect_language,
)
from app.services.pdf_chunks import chunk_pages
from app.services.pdf_text import PageText

USER_PUBLISHER = "你上传的文档"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def mirror_user_document(repo: CorpusRepository, upload: OwnedFile, pages: list[PageText]) -> int:
    """写入（或整份替换）一份用户文档的副本，返回块数。`pages` 是已经并入表格行的页文本。"""

    pieces = chunk_pages(
        pages,
        target_tokens=CHUNK_TARGET_TOKENS,
        max_tokens=CHUNK_MAX_TOKENS,
        overlap_tokens=CHUNK_OVERLAP_TOKENS,
    )
    if not pieces:
        repo.drop_user_document(upload.id)
        return 0
    published = upload.uploaded_at.astimezone(_SHANGHAI).date()
    chunks = [
        NewChunk(
            content=piece.content,
            char_start=piece.char_start,
            char_end=piece.char_end,
            token_count=piece.token_count,
            section_title=piece.section_title,
            page_start=piece.page_start,
            page_end=piece.page_end,
            context_header=context_header(
                title=upload.title,
                publisher=USER_PUBLISHER,
                published=published,
                section=piece.section_title,
            ),
        )
        for piece in pieces
    ]
    body = "\n".join(piece.content for piece in pieces)
    repo.replace_user_document(
        NewDocument(
            source_id=USER_UPLOAD_SOURCE,
            canonical_url=f"user-document:{upload.id}",
            kind="user",
            title=upload.title,
            doc_type="upload",
            language=detect_language(body),
            published_at=upload.uploaded_at,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            authority="user",
            owner_user_id=upload.owner_id,
            user_document_id=upload.id,
        ),
        chunks,
    )
    return len(chunks)
