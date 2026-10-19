"""助手的 SSE 协议（规格 §4）。纯函数，不碰数据库和网络。

兼容旧前端：只用 `data:` 行，每行一个 JSON；`delta` / `done` 两个键保持原样，
新增的 `type` 只是多出来的字段。心跳是 SSE 注释行，旧前端只认 `data:` 开头，会直接忽略。
"""

import json
from typing import Any

# 超过心跳间隔没有事件时发这一行，代理按读超时计时，有字节经过就不会切断。
HEARTBEAT = ": ping\n\n"
DEFAULT_CHUNK = 24
TURN_FAILED_MESSAGE = "这条回答没有生成成功，请重新提问。"


def encode(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def delta_events(text: str, size: int = DEFAULT_CHUNK) -> list[str]:
    """正文切成片段。空文本也给一条空 delta，旧前端据此知道正文为空。"""

    parts = [text[index : index + size] for index in range(0, len(text), size)] or [""]
    return [encode({"type": "delta", "delta": part}) for part in parts]


def done_event(status: str) -> str:
    return encode({"type": "done", "done": True, "status": status})


def error_event(code: str, message: str) -> str:
    return encode({"type": "error", "code": code, "message": message})


def replay_events(text: str, status: str, size: int = DEFAULT_CHUNK) -> list[str]:
    """把一条已经定稿的回答还原成事件序列；同一回答每次还原的字节完全相同。"""

    if status == "failed":
        return [error_event("TURN_FAILED", TURN_FAILED_MESSAGE), done_event(status)]
    return [*delta_events(text, size), done_event(status)]
