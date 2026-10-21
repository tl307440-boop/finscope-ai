"""智谱文本嵌入：请求格式、响应校验（维度、顺序、归一）、错误说明、配置开关、测试连接、迁移。"""

import json
import math
import re
from pathlib import Path

import httpx
import pytest
from app.core.config import Settings
from app.providers.corpus_embedding import (
    INDEXED_DIMS,
    MAX_BATCH,
    MAX_INPUT_CHARS,
    EmbeddingConfig,
    embed_texts,
    embedding_config,
    parse_embeddings,
    passage_text,
)
from app.providers.errors import ProviderError
from app.services import integration_probe
from app.services.runtime_settings import MANAGED

CONFIG = EmbeddingConfig(
    base_url="https://open.bigmodel.cn/api/paas/v4", api_key="sk-test", model="embedding-3", dims=4
)
MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic/versions/20261024_0035_corpus_embeddings.py"
)


def _payload(*vectors: list[object], shuffle: bool = False) -> bytes:
    items = [
        {"index": index, "object": "embedding", "embedding": vector}
        for index, vector in enumerate(vectors)
    ]
    if shuffle:
        items.reverse()
    return json.dumps({"model": "embedding-3", "object": "list", "data": items}).encode()


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


# ---------------------------------------------------------------- 请求与响应
def test_request_carries_model_texts_dimensions_and_bearer_key() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_payload([3, 4, 0, 0], [0, 0, 0, 2]))

    vectors = embed_texts(CONFIG, ["降准", "流动性"], client=_client(httpx.MockTransport(handle)))
    (request,) = seen
    assert str(request.url) == "https://open.bigmodel.cn/api/paas/v4/embeddings"
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {
        "model": "embedding-3",
        "input": ["降准", "流动性"],
        "dimensions": 4,
    }
    assert vectors == [[0.6, 0.8, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def test_results_are_put_back_in_input_order_and_unit_length() -> None:
    vectors = parse_embeddings(_payload([1, 1, 1, 1], [0, 5, 0, 0], shuffle=True), count=2, dims=4)
    assert vectors[1] == [0.0, 1.0, 0.0, 0.0]
    assert math.isclose(sum(value * value for value in vectors[0]), 1.0)


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        b'{"data": "x"}',
        _payload([1, 0, 0, 0]),  # 少一条
        _payload([1, 0, 0], [0, 1, 0]),  # 维度不对：不截断、不补零
        _payload([0, 0, 0, 0], [1, 0, 0, 0]),  # 零向量没法归一
        _payload([1, 0, 0, "x"], [1, 0, 0, 0]),
        _payload([1, 0, 0, True], [1, 0, 0, 0]),
        json.dumps({"data": [{"index": 0, "embedding": [1, 0, 0, 0]}] * 2}).encode(),  # 下标重复
        b'{"data": [{"index": 0, "embedding": [1e999, 0, 0, 0]}, '
        b'{"index": 1, "embedding": [1, 0, 0, 0]}]}',
    ],
)
def test_malformed_responses_are_structured_errors(payload: bytes) -> None:
    with pytest.raises(ProviderError) as caught:
        parse_embeddings(payload, count=2, dims=4)
    assert caught.value.code == "UPSTREAM_PAYLOAD"


@pytest.mark.parametrize(
    ("status", "body", "code", "words"),
    [
        (401, b'{"error":{"code":"1001","message":"x"}}', "UPSTREAM_AUTH", "密钥"),
        (429, b'{"error":{"code":"1113","message":"x"}}', "RATE_LIMITED", "余额不足"),
        (429, b'{"error":{"code":"1302","message":"x"}}', "RATE_LIMITED", "限流"),
        (400, b'{"error":{"code":"1301","message":"secret detail"}}', "UPSTREAM_REJECTED", "1301"),
        (503, b"", "UPSTREAM_UNAVAILABLE", "暂时不可用"),
        (404, b"", "UPSTREAM_ERROR", "404"),
    ],
)
def test_http_failures_say_why_without_echoing_the_upstream_text(
    status: int, body: bytes, code: str, words: str
) -> None:
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(status, content=body)))
    with pytest.raises(ProviderError) as caught:
        embed_texts(CONFIG, ["降准"], client=client)
    assert caught.value.code == code
    assert words in caught.value.message
    assert "secret detail" not in caught.value.message and "sk-test" not in caught.value.message


def test_timeouts_and_connection_failures_are_structured() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, code in ((timeout, "UPSTREAM_TIMEOUT"), (broken, "UPSTREAM_DISCONNECTED")):
        with pytest.raises(ProviderError) as caught:
            embed_texts(CONFIG, ["降准"], client=_client(httpx.MockTransport(handler)))
        assert caught.value.code == code


def test_nothing_is_sent_for_an_empty_batch_and_bad_batches_are_refused() -> None:
    def fail(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("不应发请求")

    client = _client(httpx.MockTransport(fail))
    assert embed_texts(CONFIG, [], client=client) == []
    with pytest.raises(ValueError):
        embed_texts(CONFIG, ["x"] * (MAX_BATCH + 1), client=client)
    with pytest.raises(ValueError):
        embed_texts(CONFIG, ["x", "  "], client=client)


def test_passage_text_joins_title_header_and_body_and_reports_the_cut() -> None:
    assert passage_text("标题", " ", "正文") == ("标题\n正文", 0)
    text, cut = passage_text("标题", "块头", "字" * MAX_INPUT_CHARS)
    assert len(text) == MAX_INPUT_CHARS and cut == len("标题\n块头\n")
    assert passage_text("", "", "  ") == ("", 0)


# ---------------------------------------------------------------- 配置
def test_vectors_are_off_without_a_key_or_with_an_unindexed_model() -> None:
    assert embedding_config(Settings()) is None  # 默认没有密钥：不会悄悄花钱
    assert (
        embedding_config(Settings(corpus_embedding_api_key="k", corpus_embedding_model="x")) is None
    )
    assert (
        embedding_config(
            Settings(corpus_embedding_api_key="k", corpus_embedding_api_base_url="http://x.test")
        )
        is None
    )
    config = embedding_config(Settings(corpus_embedding_api_key=" k "))
    assert config is not None
    assert (config.api_key, config.model_id) == ("k", "embedding-3@1024")
    assert config.base_url == "https://open.bigmodel.cn/api/paas/v4"


def test_only_the_key_is_editable_in_the_ui_and_it_is_secret() -> None:
    (managed,) = [item for item in MANAGED if item.key == "CORPUS_EMBEDDING_API_KEY"]
    assert (managed.field, managed.secret, managed.kind) == (
        "corpus_embedding_api_key",
        True,
        "text",
    )
    assert "不会发给智谱" in managed.help
    assert not {"CORPUS_EMBEDDING_MODEL", "CORPUS_EMBEDDING_API_BASE_URL"} & {
        item.key for item in MANAGED
    }


def test_probe_embeds_a_tiny_text_and_reports_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[EmbeddingConfig, list[str]]] = []

    def accept(config: EmbeddingConfig, texts: list[str], **_kwargs: object) -> list[list[float]]:
        calls.append((config, texts))
        return [[1.0] * config.dims]

    monkeypatch.setattr(integration_probe, "embed_texts", accept)
    result = integration_probe.probe(
        "CORPUS_EMBEDDING_API_KEY", "sk-new", sec_user_agent="", llm_base_url=""
    )
    assert result.ok
    ((config, texts),) = calls
    assert (config.api_key, config.dims, texts) == ("sk-new", 1024, ["测试"])

    def reject(*_args: object, **_kwargs: object) -> list[list[float]]:
        raise ProviderError(code="UPSTREAM_AUTH", message="智谱拒绝了这个密钥，请在集成设置里核对")

    monkeypatch.setattr(integration_probe, "embed_texts", reject)
    result = integration_probe.probe(
        "CORPUS_EMBEDDING_API_KEY", "sk-bad", sec_user_agent="", llm_base_url=""
    )
    assert not result.ok and "拒绝" in result.message


# ---------------------------------------------------------------- 迁移
def test_migration_indexes_exactly_the_model_the_code_writes() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20261024_0035"' in source
    assert 'down_revision: str | None = "20261023_0034"' in source
    model, dims = next(iter(INDEXED_DIMS.items()))
    assert f'MODEL_ID = "{model}@{dims}"' in source and f"DIMS = {dims}" in source
    assert re.search(r"halfvec\(\{DIMS\}\)\) halfvec_cosine_ops", source)
    assert "WHERE embedding_model = '{MODEL_ID}'" in source
    assert "ON DELETE CASCADE" in source
    assert "DROP TABLE IF EXISTS corpus_embeddings" in source
