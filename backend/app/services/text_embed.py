import hashlib
import math
import re
import unicodedata

_ATOM = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\u4e00-\u9fffA-Za-z0-9]+")

LOCAL_EMBEDDING_MODEL = "local-hashing-v1"
STORED_EMBEDDING_DIM = 1536


def estimate_tokens(text: str) -> int:
    return sum(_cost(match.group()) for match in _ATOM.finditer(_normalize(text)))


def search_tokens(text: str) -> list[str]:
    """中文按二字词、英文按单词，供全文检索和哈希向量共用。"""
    parts = [match.group() for match in _ATOM.finditer(_normalize(text)) if _cost(match.group())]
    tokens: list[str] = []
    index = 0
    while index < len(parts):
        if _cjk(parts[index]):
            end = index
            while end < len(parts) and _cjk(parts[end]):
                end += 1
            chars = parts[index:end]
            if len(chars) == 1:
                tokens.append(chars[0])
            else:
                tokens.extend(chars[pos] + chars[pos + 1] for pos in range(len(chars) - 1))
            index = end
            continue
        tokens.append(parts[index])
        index += 1
    return tokens


def search_document(text: str) -> str:
    return " ".join(search_tokens(text)[:32])


def embed_text(text: str, *, dimensions: int = STORED_EMBEDDING_DIM) -> list[float] | None:
    if dimensions != STORED_EMBEDDING_DIM:
        raise ValueError("向量维度与已固定的索引不一致")
    values = [0.0] * dimensions
    for token in search_tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        values[bucket] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return None
    return [value / norm for value in values]


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _cjk(token: str) -> bool:
    return len(token) == 1 and "\u4e00" <= token <= "\u9fff"


def _cost(piece: str) -> int:
    if _cjk(piece):
        return 1
    if piece.isalnum():
        return 1
    return 0
