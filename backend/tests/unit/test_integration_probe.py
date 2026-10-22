import json

from app.services.integration_probe import probe


class _Response:
    def __init__(self, status_code: int, content: bytes = b"{}") -> None:
        self.status_code = status_code
        self.content = content


def test_bocha_probe_accepts_a_key_and_does_not_echo_it(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def post(url: str, *, content: bytes, headers: dict[str, str], timeout: object) -> _Response:
        seen["url"] = url
        seen["authorization"] = headers["Authorization"]
        assert "上证指数".encode() in content
        payload = {
            "data": {
                "webPages": {
                    "value": [{"name": "上证指数", "url": "https://example.com/a", "snippet": "x"}]
                }
            }
        }
        return _Response(200, json.dumps(payload).encode())

    monkeypatch.setattr("app.services.integration_probe.httpx.post", post)
    result = probe("BOCHA_API_KEY", "secret-key", sec_user_agent="", llm_base_url="")
    assert result.ok is True
    assert result.message == "试查「上证指数」，返回了 1 条网页"
    assert seen["authorization"] == "Bearer secret-key"
    assert "secret-key" not in result.message


def test_bocha_probe_reports_a_rejected_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.integration_probe.httpx.post",
        lambda *args, **kwargs: _Response(401),
    )
    result = probe("BOCHA_API_KEY", "bad", sec_user_agent="", llm_base_url="")
    assert result.ok is False
    assert result.message == "密钥被拒绝，请核对是不是整段复制进来的"


def test_qianfan_probe_treats_an_error_code_as_failure(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def post(url: str, *, content: bytes, headers: dict[str, str], timeout: object) -> _Response:
        seen["url"] = url
        body = json.loads(content)
        assert body["search_source"] == "baidu_search_v2"
        assert headers["Authorization"] == "Bearer qf-key"
        return _Response(200, b'{"code": 216003, "message": "denied"}')

    monkeypatch.setattr("app.services.integration_probe.httpx.post", post)
    result = probe("BAIDU_QIANFAN_API_KEY", "qf-key", sec_user_agent="", llm_base_url="")
    assert result.ok is False
    assert result.message == "试查没有完成，对方返回错误码 216003"
    assert "qf-key" not in result.message
    assert "qianfan.baidubce.com" in str(seen["url"])


def test_qianfan_probe_accepts_a_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.integration_probe.httpx.post",
        lambda *args, **kwargs: _Response(200, b'{"references": []}'),
    )
    result = probe("BAIDU_QIANFAN_API_KEY", "qf-key", sec_user_agent="", llm_base_url="")
    assert result.ok is True
    assert result.message == "密钥能用，试查「上证指数」没有返回网页"


def test_model_name_probe_calls_chat_completions(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def post(
        url: str, *, headers: dict[str, str], json: dict[str, object], timeout: object
    ) -> _Response:
        seen["url"] = url
        seen["model"] = json["model"]
        assert headers["Authorization"] == "Bearer sk-live"
        return _Response(200)

    monkeypatch.setattr("app.services.integration_probe.httpx.post", post)
    result = probe(
        "LLM_MODEL",
        "deepseek-chat",
        sec_user_agent="",
        llm_base_url="https://api.deepseek.com/v1",
        llm_api_key="sk-live",
    )
    assert result.ok is True
    assert result.message == "「deepseek-chat」这次测试调用已返回"
    assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert seen["model"] == "deepseek-chat"
    assert "sk-live" not in result.message


def test_model_name_probe_needs_the_saved_key() -> None:
    result = probe(
        "LLM_MODEL", "deepseek-chat", sec_user_agent="", llm_base_url="https://api.example/v1"
    )
    assert result.ok is False
    assert result.message == "还没有大模型 API 密钥，这个名称没法单独测"


def test_model_name_probe_reports_an_unknown_model(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.integration_probe.httpx.post",
        lambda *args, **kwargs: _Response(400),
    )
    result = probe(
        "LLM_MODEL",
        "no-such-model",
        sec_user_agent="",
        llm_base_url="https://api.deepseek.com/v1",
        llm_api_key="sk-live",
    )
    assert result.ok is False
    assert result.message == "「no-such-model」这个名称对方不认"


def test_bool_switch_still_has_no_connection_test() -> None:
    result = probe("ASSISTANT_WEB_SEARCH_ENABLED", "true", sec_user_agent="", llm_base_url="")
    assert result.ok is False
    assert result.message == "这一项没有可用的连接测试"
