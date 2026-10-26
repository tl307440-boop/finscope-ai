"""金十、华尔街见闻、财联社电报的公开快讯。

只做网址和解析，不发请求。三家都是网页自己匿名调用的接口：
金十带公开的网页客户端标识，华尔街见闻不带密钥，财联社用 `/api/cache` 这份
不计算签名的缓存。不取正文，标题最长 120 字。

没有接入、也不能用绕过方式补上的：
- 雪球热帖要前端生成的 `xq_a_token`，纯 HTTP 拿不到，不从浏览器里抽 cookie。
- 韭研公社、开盘啦没有不登录、不带设备令牌的公开列表。
- 通达信行情是二进制行情站，对不上东财板块代码，不拿它填板块涨跌幅。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.providers.eastmoney_news import MAX_TITLE_CHARS, NewsItem
from app.providers.errors import ProviderError

JIN10_URL = "https://flash-api.jin10.com/get_flash_list"
WSCN_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
CLS_URL = "https://www.cls.cn/api/cache"
JIN10_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://www.jin10.com/)",
    "Referer": "https://www.jin10.com/",
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
}
WSCN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://wallstreetcn.com/)",
    "Referer": "https://wallstreetcn.com/",
}
CLS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://www.cls.cn/telegraph)",
    "Referer": "https://www.cls.cn/telegraph",
}
JIN10_PUBLISHER = "金十数据"
WSCN_PUBLISHER = "华尔街见闻"
CLS_PUBLISHER = "财联社电报"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TAGS = re.compile(r"<[^>]+>")
_BRACKET = re.compile(r"【([^】]{2,80})】")


def jin10_url() -> str:
    return f"{JIN10_URL}?{urlencode({'channel': '-8200', 'vip': '0'})}"


def wscn_url(limit: int = 30) -> str:
    query = urlencode({"channel": "global-channel", "client": "pc", "limit": str(limit)})
    return f"{WSCN_URL}?{query}"


def cls_url() -> str:
    query = urlencode({"app": "CailianpressWeb", "os": "web", "sv": "8.7.9", "name": "telegraph"})
    return f"{CLS_URL}?{query}"


def parse_jin10(payload: bytes) -> tuple[NewsItem, ...]:
    document = _object(_loads(payload), "金十")
    if document.get("status") not in (200, "200"):
        raise _error("金十")
    items: list[NewsItem] = []
    for raw in _list(document.get("data")):
        row = raw if isinstance(raw, dict) else {}
        if row.get("type") == 1:
            continue
        raw_extras = row.get("extras")
        extras = raw_extras if isinstance(raw_extras, dict) else {}
        if extras.get("ad") is True:
            continue
        raw_body = row.get("data")
        body = raw_body if isinstance(raw_body, dict) else {}
        if body.get("exclusive_to"):
            continue
        code = _text(row.get("id"))
        title = _headline(body.get("title")) or _headline(body.get("content"))
        moment = _shanghai(row.get("time"))
        if not code or not title or moment is None:
            continue
        items.append(
            NewsItem(f"jin10:{code}", title, moment, JIN10_PUBLISHER, "https://www.jin10.com/flash")
        )
    return tuple(items)


def parse_wscn(payload: bytes) -> tuple[NewsItem, ...]:
    document = _object(_loads(payload), "华尔街见闻")
    if document.get("code") not in (20000, "20000"):
        raise _error("华尔街见闻")
    data = _object(document.get("data"), "华尔街见闻")
    items: list[NewsItem] = []
    for raw in _list(data.get("items")):
        row = raw if isinstance(raw, dict) else {}
        code = _text(row.get("id"))
        title = (
            _headline(row.get("title"))
            or _headline(row.get("content_text"))
            or _headline(row.get("content"))
        )
        moment = _unix(row.get("display_time"))
        url = _own_https(_text(row.get("uri")), "wallstreetcn.com")
        if not url and code:
            url = f"https://wallstreetcn.com/livenews/{code}"
        if not code or not title or moment is None or not url:
            continue
        items.append(NewsItem(f"wscn:{code}", title, moment, WSCN_PUBLISHER, url))
    return tuple(items)


def parse_cls(payload: bytes) -> tuple[NewsItem, ...]:
    document = _object(_loads(payload), "财联社")
    if document.get("errno") not in (0, "0"):
        raise _error("财联社")
    data = _object(document.get("data"), "财联社")
    rows = data.get("roll_data")
    if rows is None:
        rows = data.get("telegraph") or data.get("roll")
    items: list[NewsItem] = []
    for raw in _list(rows):
        row = raw if isinstance(raw, dict) else {}
        code = _text(row.get("id"))
        title = _headline(row.get("title")) or _headline(row.get("brief"))
        moment = _unix(row.get("ctime"))
        if not code or not title or moment is None:
            continue
        items.append(
            NewsItem(
                f"cls:{code}", title, moment, CLS_PUBLISHER, f"https://www.cls.cn/detail/{code}"
            )
        )
    return tuple(items)


def _headline(value: Any) -> str:
    text = _TAGS.sub("", _text(value)).strip()
    found = _BRACKET.search(text)
    if found:
        text = found.group(1).strip()
    return text[:MAX_TITLE_CHARS]


def _shanghai(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        local = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_SHANGHAI)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _unix(value: Any) -> datetime | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        stamp = int(value)
    except (TypeError, ValueError):
        return None
    if stamp < 1_000_000_000:
        return None
    return datetime.fromtimestamp(stamp, UTC)


def _own_https(url: str, host_suffix: str) -> str:
    fixed = "https://" + url.removeprefix("http://") if url.startswith("http://") else url
    host = fixed.removeprefix("https://").split("/", 1)[0].lower()
    if fixed.startswith("https://") and host.endswith(host_suffix):
        return fixed
    return ""


def _loads(payload: bytes) -> object:
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("快讯") from exc
    return decoded


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(label)
    return value


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _error(label: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_FORMAT", message=f"{label}接口返回了无法识别的内容", status_code=502
    )
