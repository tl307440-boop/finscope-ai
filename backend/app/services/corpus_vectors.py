"""语料向量（规格 §12 3b、§13）：定时给新块补向量；检索时把检索词转成向量。

- 没配密钥时什么都不做，检索就是纯全文检索。
- 用户上传的文档不补向量（见 `CorpusEmbeddingRepository.pending`），不发给外部服务。
- 一批被对方拒绝（HTTP 400，比如内容审核）时逐条重试，仍被拒的块记原因跳过；
  密钥、余额、限流、超时这类问题直接停下，下一轮再来，已写入的批次保留。
- 检索时转向量失败（超时、限流、欠费）只记日志、返回 None，检索退回纯全文，不让问答失败。
"""

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence

import httpx
from sqlalchemy import Engine

from app.core.config import Settings
from app.providers.corpus_embedding import (
    MAX_BATCH,
    MAX_INPUT_CHARS,
    QUERY_TIMEOUT,
    EmbeddingConfig,
    embed_texts,
    embedding_config,
    passage_text,
)
from app.providers.errors import ProviderError
from app.repositories.corpus import QueryVector
from app.repositories.corpus_embeddings import (
    ChunkVector,
    CorpusEmbeddingRepository,
    PendingChunk,
)
from app.services.runtime_settings import effective_settings
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

Embed = Callable[..., list[list[float]]]
QUERY_CACHE_SIZE = 256


def embed_pending(
    engine: Engine,
    settings: Settings,
    *,
    limit: int | None = None,
    repository: CorpusEmbeddingRepository | None = None,
    embed: Embed = embed_texts,
    client: httpx.Client | None = None,
) -> int:
    """给最多 `limit` 块补向量，返回这次写入了向量的块数（被拒跳过的不算）。"""

    config = embedding_config(settings)
    if config is None:
        return 0
    repo = repository if repository is not None else CorpusEmbeddingRepository(engine)
    pending = repo.pending(config.model_id, limit or settings.corpus_embed_per_run)
    saved = 0
    for start in range(0, len(pending), MAX_BATCH):
        rows = _embed_batch(config, pending[start : start + MAX_BATCH], embed, client)
        repo.save(config.model_id, rows, utc_now())
        saved += sum(1 for row in rows if row.vector is not None)
    logger.info(
        "corpus embed done model=%s pending=%s saved=%s", config.model_id, len(pending), saved
    )
    return saved


def _embed_batch(
    config: EmbeddingConfig,
    batch: Sequence[PendingChunk],
    embed: Embed,
    client: httpx.Client | None,
) -> list[ChunkVector]:
    prepared = [(item, *passage_text(item.title, item.header, item.content)) for item in batch]
    empty = [
        ChunkVector(item.chunk_id, None, 0, "没有可向量化的文字")
        for item, text, _ in prepared
        if not text
    ]
    usable = [(item, text, cut) for item, text, cut in prepared if text]
    if not usable:
        return empty
    try:
        vectors = embed(config, [text for _, text, _ in usable], client=client)
    except ProviderError as exc:
        if exc.code != "UPSTREAM_REJECTED":
            raise
        return empty + [_one(config, item, text, cut, embed, client) for item, text, cut in usable]
    return empty + [
        ChunkVector(item.chunk_id, vector, cut)
        for (item, _, cut), vector in zip(usable, vectors, strict=True)
    ]


def _one(
    config: EmbeddingConfig,
    item: PendingChunk,
    text: str,
    cut: int,
    embed: Embed,
    client: httpx.Client | None,
) -> ChunkVector:
    try:
        (vector,) = embed(config, [text], client=client)
    except ProviderError as exc:
        if exc.code != "UPSTREAM_REJECTED":
            raise
        logger.warning("corpus embed skipped chunk=%s reason=%s", item.chunk_id, exc.message)
        return ChunkVector(item.chunk_id, None, cut, exc.message[:200])
    return ChunkVector(item.chunk_id, vector, cut)


class QueryEmbedder:
    """检索词 → 向量，进程内按 (模型, 文本) 缓存。

    每次调用都读当前生效的配置（带 5 秒缓存）：界面里刚填的密钥不用重启就生效。
    """

    def __init__(
        self,
        settings_for: Callable[[Engine], Settings] = effective_settings,
        embed: Embed = embed_texts,
        cache_size: int = QUERY_CACHE_SIZE,
    ) -> None:
        self._settings_for = settings_for
        self._embed = embed
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[str, str], QueryVector] = OrderedDict()
        self._lock = threading.Lock()

    def __call__(self, engine: Engine, text: str) -> QueryVector | None:
        cleaned = " ".join(text.split())[:MAX_INPUT_CHARS]
        if not cleaned:
            return None
        config = embedding_config(self._settings_for(engine))
        if config is None:
            return None
        key = (config.model_id, cleaned)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        try:
            (vector,) = self._embed(config, [cleaned], timeout=QUERY_TIMEOUT)
        except ProviderError as exc:
            logger.warning("corpus query embedding failed code=%s", exc.code)
            return None
        found = QueryVector(model_id=config.model_id, values=tuple(vector))
        with self._lock:
            self._cache[key] = found
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return found
