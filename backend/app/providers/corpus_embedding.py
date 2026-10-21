"""智谱文本嵌入（`POST {base}/embeddings`，OpenAI 兼容格式），给语料库语义检索用（规格 §13）。

- 密钥只在服务端；请求只带文本，不带用户信息。
- 返回维度必须等于配置的维度，否则报错，不截断、不补零；向量统一做 L2 归一。
- 只认迁移里建过索引的模型和维度（`INDEXED_DIMS`），其余配置一律当作没开向量检索。
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.providers.errors import ProviderError

# 迁移 20261024_0035 只给这个模型、这个维度建了 HNSW 索引；换模型或维度要新迁移。
INDEXED_DIMS = {"embedding-3": 1024}
# 智谱单次最多 64 条；规格定 32 条一批，单批超时 60 秒。
MAX_BATCH = 32
# embedding-3 单条上限 3072 tokens。中文一字约一个 token，留足余量。
MAX_INPUT_CHARS = 2400
BATCH_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
QUERY_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str
    model: str
    dims: int

    @property
    def model_id(self) -> str:
        """写进 `corpus_embeddings.embedding_model`，与迁移里部分索引的条件一致。"""

        return f"{self.model}@{self.dims}"


def embedding_config(settings: Settings) -> EmbeddingConfig | None:
    """没有密钥、模型没建过索引、地址不是 https 时返回 None：只做全文检索。"""

    key = settings.corpus_embedding_api_key.strip()
    model = settings.corpus_embedding_model.strip()
    base = settings.corpus_embedding_api_base_url.strip().rstrip("/")
    dims = INDEXED_DIMS.get(model)
    if not key or dims is None or not base.startswith("https://"):
        return None
    return EmbeddingConfig(base_url=base, api_key=key, model=model, dims=dims)


def passage_text(title: str, header: str, content: str) -> tuple[str, int]:
    """块的向量化文本（标题、块头、正文）和被截掉的字数。"""

    joined = "\n".join(part.strip() for part in (title, header, content) if part.strip())
    if len(joined) <= MAX_INPUT_CHARS:
        return joined, 0
    return joined[:MAX_INPUT_CHARS], len(joined) - MAX_INPUT_CHARS


def embed_texts(
    config: EmbeddingConfig,
    texts: Sequence[str],
    *,
    client: httpx.Client | None = None,
    timeout: httpx.Timeout = BATCH_TIMEOUT,
) -> list[list[float]]:
    if not texts:
        return []
    if len(texts) > MAX_BATCH:
        raise ValueError(f"一批最多 {MAX_BATCH} 条")
    if any(not text.strip() for text in texts):
        raise ValueError("不能向量化空文本")
    http = client if client is not None else httpx.Client(timeout=timeout)
    try:
        response = http.post(
            f"{config.base_url}/embeddings",
            json={"model": config.model, "input": list(texts), "dimensions": config.dims},
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            code="UPSTREAM_TIMEOUT", message="智谱向量接口超时", status_code=504
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            code="UPSTREAM_DISCONNECTED", message="连不上智谱向量接口", status_code=502
        ) from exc
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        raise status_error(response.status_code, response.content)
    return parse_embeddings(response.content, count=len(texts), dims=config.dims)


def status_error(status: int, payload: bytes) -> ProviderError:
    """按 HTTP 状态和智谱错误码说明原因；不回显对方的报错正文。"""

    code = _error_code(payload)
    if status in {401, 403}:
        return ProviderError(
            code="UPSTREAM_AUTH",
            message="智谱拒绝了这个密钥，请在集成设置里核对",
            status_code=status,
        )
    if status == 429:
        message = "智谱账户余额不足或资源包已用完" if code == "1113" else "智谱限流，稍后再试"
        return ProviderError(code="RATE_LIMITED", message=message, status_code=429)
    if status == 400:
        suffix = f"（错误码 {code}）" if code else ""
        return ProviderError(
            code="UPSTREAM_REJECTED", message=f"智谱拒绝了这批文本{suffix}", status_code=400
        )
    if status >= 500:
        return ProviderError(
            code="UPSTREAM_UNAVAILABLE", message="智谱向量接口暂时不可用", status_code=status
        )
    return ProviderError(
        code="UPSTREAM_ERROR", message=f"智谱向量接口返回 HTTP {status}", status_code=status
    )


def parse_embeddings(payload: bytes, *, count: int, dims: int) -> list[list[float]]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error() from exc
    data = decoded.get("data") if isinstance(decoded, dict) else None
    if not isinstance(data, list) or len(data) != count:
        raise _payload_error()
    by_index: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, dict):
            raise _payload_error()
        index = item.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index in by_index:
            raise _payload_error()
        by_index[index] = _unit_vector(item.get("embedding"), dims)
    if sorted(by_index) != list(range(count)):
        raise _payload_error()
    return [by_index[index] for index in range(count)]


def _unit_vector(raw: Any, dims: int) -> list[float]:
    if not isinstance(raw, list) or len(raw) != dims:
        raise _payload_error()
    values: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _payload_error()
        number = float(value)
        if not math.isfinite(number):
            raise _payload_error()
        values.append(number)
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise _payload_error()
    return [value / norm for value in values]


def _error_code(payload: bytes) -> str:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    error = decoded.get("error") if isinstance(decoded, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code)[:16] if isinstance(code, str | int) and not isinstance(code, bool) else ""


def _payload_error() -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message="无法解析智谱向量接口的响应", status_code=502
    )
