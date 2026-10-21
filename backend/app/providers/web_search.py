"""联网检索的两家适配器（知识库设计 §5.5）。

只解析当次响应。调用方不得把结果写入语料库或回答记录。
中文问题优先博查，英文问题优先 Brave；一家没有密钥或失败就换另一家。
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BOCHA_URL = "https://api.bochaai.com/v1/web-search"

# 检索词里出现这些词，就按对应范围限定发布时间。先匹配短的范围：「今天」比「最近」更具体。
_WINDOWS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "oneDay",
        ("今天", "今日", "刚刚", "刚才", "today", "just now", "breaking"),
    ),
    (
        "oneWeek",
        (
            "前几天",
            "这几天",
            "近几天",
            "最近几天",
            "近日",
            "昨天",
            "昨日",
            "前天",
            "本周",
            "这周",
            "上周",
            "this week",
            "last week",
            "yesterday",
            "few days",
        ),
    ),
    (
        "oneMonth",
        (
            "最近",
            "近期",
            "这个月",
            "本月",
            "上个月",
            "recent",
            "lately",
            "this month",
            "last month",
        ),
    ),
)


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published: date | None
    provider: str


class SearchServiceError(Exception):
    """配置过的搜索服务全都没能给出结果（密钥、额度、网络），和「查了但没有结果」不同。

    `reason` 不含密钥和地址，可以直接给模型转述、给用户看。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# 提示模型怎么说：服务出错不能说成「网上没有这条消息」，也不能拿记忆顶替。
_TELL_THE_USER = "如实告诉用户检索服务出了问题、需要管理员处理，不要说查不到，也不要凭记忆作答。"
NO_KEY_REASON = "联网检索没有配置密钥，不是网上没有这条消息。" + _TELL_THE_USER


def infer_freshness(query: str) -> str | None:
    """从检索词里的时间词推断发布时间范围；没有时间词就不限。不信任模型自己填的范围。"""

    text = query.lower()
    for window, words in _WINDOWS:
        if any(word in text for word in words):
            return window
    return None


def describe_status(status: int) -> str:
    """搜索服务返回的 HTTP 状态码，翻成能给人看的原因。博查余额不足返回 403。"""

    if status in (401, 403):
        return "密钥无效或额度不足"
    if status == 402:
        return "额度不足"
    if status == 429:
        return "请求过于频繁或额度用完"
    if status >= 500:
        return "服务暂时故障"
    return f"返回 HTTP {status}"


def service_down_reason(failures: Sequence[tuple[str, str]]) -> str:
    detail = "；".join(f"{name}：{why}" for name, why in failures)
    return f"联网检索服务暂时不可用（{detail}），不是网上没有这条消息。{_TELL_THE_USER}"


def brave_hits(payload: bytes) -> tuple[SearchHit, ...]:
    web = _object(_loads(payload).get("web"))
    return tuple(
        _hit(
            item,
            "Brave",
            item.get("title"),
            item.get("description"),
            item.get("page_age") or item.get("age"),
        )
        for item in _list(web.get("results"))
    )


def bocha_hits(payload: bytes) -> tuple[SearchHit, ...]:
    data = _object(_loads(payload).get("data"))
    pages = _object(data.get("webPages"))
    return tuple(
        _hit(item, "博查", item.get("name"), item.get("snippet"), item.get("datePublished"))
        for item in _list(pages.get("value"))
    )


def brave_request(query: str, freshness: str | None = None) -> tuple[str, dict[str, str]]:
    params = {"q": query, "count": "8"}
    brave_freshness = {"oneDay": "pd", "oneWeek": "pw", "oneMonth": "pm", "oneYear": "py"}.get(
        freshness or ""
    )
    if brave_freshness:
        params["freshness"] = brave_freshness
    return f"{BRAVE_URL}?{urlencode(params)}", {
        "Accept": "application/json",
        "X-Subscription-Token": "",
    }


def bocha_body(query: str, freshness: str | None = None) -> bytes:
    body: dict[str, Any] = {"query": query, "count": 8, "summary": False}
    if freshness:
        body["freshness"] = freshness
    return json.dumps(body, ensure_ascii=False).encode()


def _hit(item: Any, provider: str, title: Any, snippet: Any, when: Any) -> SearchHit:
    record = item if isinstance(item, dict) else {}
    url = record.get("url")
    return SearchHit(
        title=_str(title)[:180],
        url=_str(url),
        snippet=_str(snippet)[:500],
        published=_day(when),
        provider=provider,
    )


def _day(value: Any) -> date | None:
    text = _str(value)
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _loads(payload: bytes) -> dict[str, Any]:
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
