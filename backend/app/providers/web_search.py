"""联网检索的两家适配器（知识库设计 §5.5）。

只解析当次响应。调用方不得把结果写入语料库或回答记录。
中文问题优先博查，英文问题优先 Brave；一家没有密钥或失败就换另一家。
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
BOCHA_URL = "https://api.bochaai.com/v1/web-search"


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    published: date | None
    provider: str


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


def brave_request(query: str) -> tuple[str, dict[str, str]]:
    return f"{BRAVE_URL}?{urlencode({'q': query, 'count': '8'})}", {
        "Accept": "application/json",
        "X-Subscription-Token": "",
    }


def bocha_body(query: str) -> bytes:
    return json.dumps({"query": query, "count": 8, "summary": False}, ensure_ascii=False).encode()


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
