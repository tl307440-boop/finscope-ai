"""OpenAI 兼容 `/chat/completions` 的带工具调用客户端（规格 §5）。

- 请求：`temperature: 0`、`max_tokens: 1800`、`stream: true`，
  单次不重试（重试由编排器按步数预算决定）。
- 流式解析：`tool_calls` 按 `index` 累积成完整调用，`content` 累积成正文；
  两者同时出现以工具调用为准。
  只看 `choices[0].delta` 的 `content` 与 `tool_calls[]`，其余字段一律忽略。
- 接口不支持流式（直接回了普通 JSON）时自动按非流式解析。
- 错误统一抛 `ModelCallError(kind)`，kind 为 timeout / http / malformed / empty；
  **异常文本不带密钥、不带响应原文**。
- 能力探测：发一个只含无参工具 `ping` 的请求并要求调用它，结果按 (base_url, model) 缓存在内存。
"""

import json
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

MAX_TOKENS = 1800
CONNECT_SECONDS = 5.0
FIRST_BYTE_SECONDS = 20.0
SINGLE_CALL_SECONDS = 60.0
PROBE_SECONDS = 20.0


class ModelCallError(Exception):
    """kind ∈ timeout / http / malformed / empty；http 时 `status` 是状态码（不含响应内容）。"""

    def __init__(self, kind: str, status: int | None = None) -> None:
        super().__init__(kind)
        self.kind = kind
        self.status = status


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # 模型给的原始 JSON 文本，交给工具注册表校验


@dataclass(frozen=True)
class ModelReply:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Mapping[str, int] = field(default_factory=dict)


class ToolModel(Protocol):
    @property
    def model_name(self) -> str: ...

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        timeout: float,
    ) -> ModelReply: ...


class OpenAIToolModel:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = f"{base_url.strip().rstrip('/')}/chat/completions"
        self._key = api_key.strip()
        self._model = model.strip()
        self._client = client or httpx.Client()

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        timeout: float,
        tool_choice: Any = "auto",
    ) -> ModelReply:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": MAX_TOKENS,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        budget = max(1.0, min(timeout, SINGLE_CALL_SECONDS))
        started = time.monotonic()
        try:
            with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._key}"},
                json=payload,
                timeout=httpx.Timeout(
                    budget,
                    connect=min(CONNECT_SECONDS, budget),
                    read=min(FIRST_BYTE_SECONDS, budget),
                ),
            ) as response:
                if response.status_code >= 400:
                    raise ModelCallError("http", response.status_code)
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("application/json"):
                    reply = _parse_full(json.loads(response.read()))
                else:
                    reply = _parse_stream(response.iter_lines(), started, budget)
        except ModelCallError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelCallError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ModelCallError("http") from exc
        except (ValueError, TypeError, KeyError, httpx.InvalidURL, httpx.StreamError) as exc:
            raise ModelCallError("malformed") from exc
        if not reply.content.strip() and not reply.tool_calls:
            raise ModelCallError("empty")
        return reply


def _parse_stream(lines: Any, started: float, budget: float) -> ModelReply:
    parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    usage: dict[str, int] = {}
    for raw in lines:
        if time.monotonic() - started > budget:
            raise ModelCallError("timeout")
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if isinstance(chunk.get("usage"), dict):
            usage = _usage(chunk["usage"])
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
        if not isinstance(delta, dict):
            continue
        text = delta.get("content")
        if isinstance(text, str):
            parts.append(text)
        _accumulate(calls, delta.get("tool_calls"))
    return _build(calls, "".join(parts), usage)


def _accumulate(calls: dict[int, dict[str, str]], fragments: Any) -> None:
    if not isinstance(fragments, list):
        return
    for position, fragment in enumerate(fragments):
        if not isinstance(fragment, dict):
            continue
        index = fragment.get("index")
        slot = calls.setdefault(index if isinstance(index, int) else position, {})
        if isinstance(fragment.get("id"), str) and fragment["id"]:
            slot["id"] = fragment["id"]
        function = fragment.get("function")
        if isinstance(function, dict):
            if isinstance(function.get("name"), str) and function["name"]:
                slot["name"] = slot.get("name", "") + function["name"]
            if isinstance(function.get("arguments"), str):
                slot["arguments"] = slot.get("arguments", "") + function["arguments"]


def _parse_full(body: Any) -> ModelReply:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("no choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("no message")
    calls: dict[int, dict[str, str]] = {}
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        _accumulate(
            calls,
            [
                {"index": index, **item}
                for index, item in enumerate(raw_calls)
                if isinstance(item, dict)
            ],
        )
    content = message.get("content")
    usage = _usage(body["usage"]) if isinstance(body.get("usage"), dict) else {}
    return _build(calls, content if isinstance(content, str) else "", usage)


def _build(calls: dict[int, dict[str, str]], content: str, usage: dict[str, int]) -> ModelReply:
    tool_calls = tuple(
        ToolCall(
            id=slot.get("id") or f"call_{index}",
            name=slot["name"],
            arguments=slot.get("arguments", ""),
        )
        for index, slot in sorted(calls.items())
        if slot.get("name")
    )
    if _DSML.search(content):
        content, written = split_dsml(content)
        tool_calls = tool_calls or written
    # 同时出现工具调用与正文时以工具调用为准，正文丢弃。
    return ModelReply(content="" if tool_calls else content, tool_calls=tool_calls, usage=usage)


# DeepSeek 有时不走 `tool_calls` 字段，而是把调用按它自己的 DSML 标记写进正文：
# <｜DSML｜function_calls><｜DSML｜invoke name="calc">
# <｜DSML｜parameter name="op" string="true">diff</｜DSML｜parameter>…
# 竖线有全角、半角，也见过两条；标记一律不能进正文。
_BAR = r"[｜|]+"
_DSML = re.compile(rf"<\s*/?\s*{_BAR}\s*DSML\s*{_BAR}")
_DSML_TAG = re.compile(rf"<\s*/?\s*{_BAR}\s*DSML\s*{_BAR}[^>]*>")
_DSML_INVOKE = re.compile(rf'<\s*{_BAR}\s*DSML\s*{_BAR}\s*invoke\s+name\s*=\s*"([^"]+)"[^>]*>')
_DSML_PARAM = re.compile(
    rf'<\s*{_BAR}\s*DSML\s*{_BAR}\s*parameter\s+name\s*=\s*"([^"]+)"'
    r'(?:\s+string\s*=\s*"(true|false)")?[^>]*>(.*?)'
    rf"(?=<\s*/?\s*{_BAR}\s*DSML\s*{_BAR}|$)",
    re.DOTALL,
)


def split_dsml(content: str) -> tuple[str, tuple[ToolCall, ...]]:
    """把正文里的 DSML 调用拆出来：返回（去掉标记后的正文，解析出的调用）。

    标记之后如果还有正文（模型写完调用又接着写回答），保留下来；解析不出参数的调用丢弃。
    """

    first = _DSML.search(content)
    if first is None:
        return content, ()
    tags = list(_DSML_TAG.finditer(content))
    tail = content[tags[-1].end() :] if tags else ""
    # 最后一个标记之后紧跟的是参数值或残缺的标记，不是正文。
    if tags and not tags[-1].group().lstrip("<").lstrip().startswith("/"):
        tail = ""
    calls: list[ToolCall] = []
    invokes = list(_DSML_INVOKE.finditer(content))
    for index, invoke in enumerate(invokes):
        end = invokes[index + 1].start() if index + 1 < len(invokes) else len(content)
        args: dict[str, Any] = {}
        for param in _DSML_PARAM.finditer(content, invoke.end(), end):
            name, is_string, raw = param.group(1), param.group(2), param.group(3).strip()
            if is_string == "true":
                args[name] = raw
                continue
            try:
                args[name] = json.loads(raw)
            except ValueError:
                args[name] = raw
        calls.append(
            ToolCall(
                id=f"dsml_{index}",
                name=invoke.group(1).strip(),
                arguments=json.dumps(args, ensure_ascii=False),
            )
        )
    text = (content[: first.start()] + tail).strip()
    return text, tuple(calls)


def _usage(raw: Mapping[str, Any]) -> dict[str, int]:
    """只留数字字段；若服务商带前缀缓存命中数（各家字段名不同），一并留下，上线后据此确认是否生效。"""

    found: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            found[str(key)] = value
        elif isinstance(value, dict):
            for inner_key, inner in value.items():
                if isinstance(inner, int) and not isinstance(inner, bool):
                    found[f"{key}.{inner_key}"] = inner
    return found


_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "测试用：调用即可，没有参数。",
        "parameters": {"type": "object", "properties": {}},
    },
}
_REJECTED_TOOLS = frozenset({400, 404, 422})
_support: dict[tuple[str, str], bool] = {}
_support_lock = threading.Lock()


def supports_tools(model: OpenAIToolModel, base_url: str) -> bool:
    """探测接口是否支持工具调用。结果按 (base_url, model) 缓存；探测本身失败不缓存，下次再试。"""

    key = (base_url.strip().rstrip("/"), model.model_name)
    with _support_lock:
        if key in _support:
            return _support[key]
    try:
        reply = model.complete(
            [
                {"role": "system", "content": "按要求调用工具，不要回答文字。"},
                {"role": "user", "content": "请调用 ping 工具。"},
            ],
            [_PROBE_TOOL],
            timeout=PROBE_SECONDS,
            tool_choice={"type": "function", "function": {"name": "ping"}},
        )
    except ModelCallError as exc:
        if exc.kind == "http" and exc.status in _REJECTED_TOOLS:
            # 接口明确拒绝带 tools 的请求：按不支持处理并缓存。
            # 429、5xx、超时都是暂时的，不缓存，下一个回合再探测。
            with _support_lock:
                _support[key] = False
        return False
    supported = any(call.name == "ping" for call in reply.tool_calls)
    with _support_lock:
        _support[key] = supported
    return supported


def reset_support_cache() -> None:
    with _support_lock:
        _support.clear()
