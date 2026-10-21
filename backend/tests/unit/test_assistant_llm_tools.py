"""带工具调用的模型客户端：流式拼装、非流式回退、错误分类与能力探测缓存。"""

import json
from collections.abc import Callable

import httpx
import pytest
from app.services.assistant.llm_tools import (
    ModelCallError,
    OpenAIToolModel,
    reset_support_cache,
    supports_tools,
)

SECRET = "sk-very-secret-key"
BASE = "https://llm.example.test/v1"


def sse(*chunks: object, done: bool = True) -> str:
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    if done:
        lines.append("data: [DONE]\n\n")
    return "".join(lines)


def delta(**fields: object) -> dict[str, object]:
    return {"choices": [{"delta": fields}]}


def build(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAIToolModel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIToolModel(base_url=BASE, api_key=SECRET, model="m1", client=client)


def stream_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})


MESSAGES = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _clear_probe_cache() -> None:
    reset_support_cache()


def test_stream_text_is_joined_and_usage_kept() -> None:
    body = sse(
        delta(content="你好，"),
        delta(content="世界"),
        {
            "choices": [],
            "usage": {"prompt_tokens": 10, "prompt_tokens_details": {"cached_tokens": 8}},
        },
    )
    model = build(lambda request: stream_response(body))
    reply = model.complete(MESSAGES, None, timeout=30)
    assert reply.content == "你好，世界"
    assert reply.tool_calls == ()
    assert reply.usage == {"prompt_tokens": 10, "prompt_tokens_details.cached_tokens": 8}


def test_fragmented_tool_calls_are_assembled_by_index() -> None:
    body = sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "macro_series"}}]),
        delta(tool_calls=[{"index": 1, "id": "c2", "function": {"name": "calc", "arguments": ""}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '{"code":'}}]),
        delta(tool_calls=[{"index": 1, "function": {"arguments": '{"op":"diff"}'}}]),
        delta(tool_calls=[{"index": 0, "function": {"arguments": '"CPIAUCSL"}'}}]),
    )
    reply = build(lambda request: stream_response(body)).complete(MESSAGES, [{}], timeout=30)
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("c1", "macro_series", '{"code":"CPIAUCSL"}'),
        ("c2", "calc", '{"op":"diff"}'),
    ]


def test_tool_calls_win_over_content() -> None:
    body = sse(
        delta(content="我先查一下"),
        delta(
            tool_calls=[{"index": 0, "id": "c1", "function": {"name": "calc", "arguments": "{}"}}]
        ),
    )
    reply = build(lambda request: stream_response(body)).complete(MESSAGES, [{}], timeout=30)
    assert reply.content == ""
    assert [c.name for c in reply.tool_calls] == ["calc"]


def test_missing_call_id_gets_a_stable_fallback() -> None:
    body = sse(delta(tool_calls=[{"index": 0, "function": {"name": "calc", "arguments": "{}"}}]))
    reply = build(lambda request: stream_response(body)).complete(MESSAGES, [{}], timeout=30)
    assert reply.tool_calls[0].id == "call_0"


def test_plain_json_response_is_parsed_without_streaming() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "x1", "function": {"name": "calc", "arguments": '{"op":"diff"}'}}
                    ],
                }
            }
        ],
        "usage": {"total_tokens": 7},
    }
    model = build(lambda request: httpx.Response(200, json=payload))
    reply = model.complete(MESSAGES, [{}], timeout=30)
    assert [(c.id, c.name) for c in reply.tool_calls] == [("x1", "calc")]
    assert reply.usage == {"total_tokens": 7}


def test_request_shape_is_deterministic_and_secret_only_in_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return stream_response(sse(delta(content="好")))

    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    build(handler).complete(MESSAGES, tools, timeout=30)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == f"{BASE}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    assert body["temperature"] == 0
    assert body["stream"] is True
    assert body["max_tokens"] == 1800
    assert body["tools"] == tools
    assert body["tool_choice"] == "auto"
    assert SECRET not in request.content.decode()


def test_no_tools_means_no_tool_fields() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return stream_response(sse(delta(content="好")))

    build(handler).complete(MESSAGES, None, timeout=30)
    assert "tools" not in seen[0]
    assert "tool_choice" not in seen[0]


def failing(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


@pytest.mark.parametrize(
    ("handler", "kind", "status"),
    [
        (lambda r: stream_response('{"error":"bad key sk-very-secret-key"}', 401), "http", 401),
        (lambda r: stream_response("oops", 503), "http", 503),
        (failing(httpx.ReadTimeout("slow", request=None)), "timeout", None),  # type: ignore[arg-type]
        (failing(httpx.ConnectError("down")), "http", None),
        (lambda r: stream_response(sse(delta(content="  "))), "empty", None),
        (lambda r: stream_response("data: {not json}\n\n"), "malformed", None),
        (lambda r: httpx.Response(200, json={"choices": []}), "malformed", None),
    ],
)
def test_errors_are_classified_and_leak_nothing(
    handler: Callable[[httpx.Request], httpx.Response], kind: str, status: int | None
) -> None:
    with pytest.raises(ModelCallError) as caught:
        build(handler).complete(MESSAGES, None, timeout=30)
    error = caught.value
    assert (error.kind, error.status) == (kind, status)
    assert SECRET not in str(error) and SECRET not in repr(error)
    assert "oops" not in str(error)


def test_probe_detects_tool_support_and_caches_it() -> None:
    calls = {"n": 0}
    forced: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        forced.append(json.loads(request.content)["tool_choice"])
        return stream_response(
            sse(delta(tool_calls=[{"index": 0, "id": "p", "function": {"name": "ping"}}]))
        )

    model = build(handler)
    assert supports_tools(model, BASE) is True
    assert supports_tools(model, BASE + "/") is True  # 末尾斜杠不影响缓存键
    assert calls["n"] == 1
    assert forced[0] == {"type": "function", "function": {"name": "ping"}}


def test_probe_text_answer_means_unsupported() -> None:
    model = build(lambda r: stream_response(sse(delta(content="pong"))))
    assert supports_tools(model, BASE) is False


@pytest.mark.parametrize("status", [400, 404, 422])
def test_probe_caches_explicit_rejection(status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return stream_response("tools not allowed", status)

    model = build(handler)
    assert supports_tools(model, BASE) is False
    assert supports_tools(model, BASE) is False
    assert calls["n"] == 1


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_probe_does_not_cache_transient_or_auth_errors(status: int) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return stream_response("busy", status)
        return stream_response(
            sse(delta(tool_calls=[{"index": 0, "id": "p", "function": {"name": "ping"}}]))
        )

    model = build(handler)
    assert supports_tools(model, BASE) is False
    assert supports_tools(model, BASE) is True  # 下一次重新探测，不被一次抖动永久拉回规划器模式
    assert calls["n"] == 2


def test_probe_timeout_is_not_cached() -> None:
    model = build(failing(httpx.ReadTimeout("slow", request=None)))  # type: ignore[arg-type]
    assert supports_tools(model, BASE) is False
    ok = build(
        lambda r: stream_response(
            sse(delta(tool_calls=[{"index": 0, "id": "p", "function": {"name": "ping"}}]))
        )
    )
    assert supports_tools(ok, BASE) is True


def test_probe_cache_is_per_model() -> None:
    plain = OpenAIToolModel(
        base_url=BASE,
        api_key=SECRET,
        model="no-tools",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: stream_response("x", 400))),
    )
    capable = build(
        lambda r: stream_response(
            sse(delta(tool_calls=[{"index": 0, "id": "p", "function": {"name": "ping"}}]))
        )
    )
    assert supports_tools(plain, BASE) is False
    assert supports_tools(capable, BASE) is True


# ---------------------------------------------------------------- DeepSeek 把调用写进正文
DSML_CALC = (
    "<｜DSML｜function_calls>\n"
    '<｜DSML｜invoke name="calc">\n'
    '<｜DSML｜parameter name="op" string="true">pct_change</｜DSML｜parameter>\n'
    '<｜DSML｜parameter name="inputs" string="false">'
    '[{"evidence": "E7", "column": "close", "row": -1}]</｜DSML｜parameter>\n'
    "</｜DSML｜invoke>\n"
    "</｜DSML｜function_calls>"
)


def test_dsml_written_into_content_becomes_a_tool_call_and_never_text() -> None:
    reply = build(lambda r: stream_response(sse(delta(content=DSML_CALC)))).complete(
        MESSAGES, [{}], timeout=30
    )
    assert reply.content == ""
    assert [(c.id, c.name) for c in reply.tool_calls] == [("dsml_0", "calc")]
    assert json.loads(reply.tool_calls[0].arguments) == {
        "op": "pct_change",
        "inputs": [{"evidence": "E7", "column": "close", "row": -1}],
    }


def test_dsml_variants_with_double_bars_are_recognised() -> None:
    from app.services.assistant.llm_tools import split_dsml

    text, calls = split_dsml(
        '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="calc">\n'
        '<｜｜DSML｜｜ parameter name="op" string="true">diff</｜｜DSML｜｜ parameter>'
    )
    assert text == ""
    assert [c.name for c in calls] == ["calc"] and json.loads(calls[0].arguments) == {"op": "diff"}


def test_text_around_dsml_is_kept_and_a_cut_off_value_is_dropped() -> None:
    from app.services.assistant.llm_tools import split_dsml

    text, calls = split_dsml("中芯国际业绩改善。" + DSML_CALC + "\n股价近一年下跌。")
    assert text == "中芯国际业绩改善。\n股价近一年下跌。" and len(calls) == 1
    cut, _ = split_dsml('前文。<｜DSML｜invoke name="calc"><｜DSML｜parameter name="op">di')
    assert cut == "前文。"


def test_dsml_without_any_invoke_is_stripped_from_the_answer() -> None:
    body = sse(delta(content="结论在这里。<｜DSML｜function_calls></｜DSML｜function_calls>"))
    reply = build(lambda r: stream_response(body)).complete(MESSAGES, None, timeout=30)
    assert reply.content == "结论在这里。" and reply.tool_calls == ()
