"""东方财富财经新闻：7x24 快讯滚动列表与关键词新闻搜索的公开接口适配。

只做 URL 构造与响应解析，不发请求，便于离线测试。两个接口都是东财网页自己匿名调用的
JSON / JSONP 接口，不带登录态、不破解签名；调用方负责限速与缓存。

只取标题、发布时间、媒体名和链接。摘要和正文的版权归原媒体和东方财富，这里不取、不存：
用户要看内容就点链接去原文。时间是上海时间，解析后换成 UTC。
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.providers.errors import ProviderError

FLASH_URL = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
FLASH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://kuaixun.eastmoney.com/)",
    "Referer": "https://kuaixun.eastmoney.com/",
}
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://so.eastmoney.com/)",
    "Referer": "https://so.eastmoney.com/",
}
FLASH_PUBLISHER = "东方财富快讯"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TAGS = re.compile(r"<[^>]+>")
_ARTICLE = "https://finance.eastmoney.com/a/{code}.html"
MAX_TITLE_CHARS = 120


@dataclass(frozen=True)
class NewsItem:
    code: str
    title: str
    published_at: datetime  # UTC
    publisher: str
    url: str


def flash_url(page_size: int = 50) -> str:
    query = urlencode(
        {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(page_size),
            "req_trace": "finscope",
        }
    )
    return f"{FLASH_URL}?{query}"


def search_url(keyword: str, page_size: int = 20) -> str:
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    query = urlencode({"cb": "cb", "param": json.dumps(param, ensure_ascii=False)})
    return f"{SEARCH_URL}?{query}"


def parse_flash(payload: bytes) -> tuple[NewsItem, ...]:
    document = _object(_loads(payload), "快讯")
    if str(document.get("code")) != "1":
        raise _error("快讯")
    data = _object(document.get("data"), "快讯")
    items: list[NewsItem] = []
    for raw in _list(data.get("fastNewsList")):
        row = raw if isinstance(raw, dict) else {}
        code, title = _text(row.get("code")), _clean(row.get("title"))
        moment = _moment(row.get("showTime"))
        if not code or not title or moment is None:
            continue  # 没有标题或时间的条目丢掉，不拿摘要凑数
        items.append(NewsItem(code, title, moment, FLASH_PUBLISHER, _ARTICLE.format(code=code)))
    return tuple(items)


def parse_search(payload: bytes) -> tuple[NewsItem, ...]:
    text = payload.decode("utf-8", errors="replace").strip()
    start, end = text.find("("), text.rfind(")")
    if start < 0 or end <= start:
        raise _error("新闻搜索")
    document = _object(_loads(text[start + 1 : end].encode("utf-8")), "新闻搜索")
    if document.get("code") not in (0, "0"):
        raise _error("新闻搜索")
    result = _object(document.get("result"), "新闻搜索")
    items: list[NewsItem] = []
    for raw in _list(result.get("cmsArticleWebOld")):
        row = raw if isinstance(raw, dict) else {}
        code, title = _text(row.get("code")), _clean(row.get("title"))
        moment, url = _moment(row.get("date")), _https(_text(row.get("url")))
        if not code or not title or moment is None or not url:
            continue
        publisher = _clean(row.get("mediaName")) or "东方财富"
        items.append(NewsItem(code, title, moment, publisher, url))
    return tuple(items)


def _moment(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        local = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_SHANGHAI)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _https(url: str) -> str:
    """只收东方财富自己的文章页；http 升成 https，其他域名的链接不要。"""

    fixed = "https://" + url.removeprefix("http://") if url.startswith("http://") else url
    if fixed.startswith("https://") and _host(fixed).endswith("eastmoney.com"):
        return fixed
    return ""


def _host(url: str) -> str:
    return url.removeprefix("https://").split("/", 1)[0].lower()


def _clean(value: Any) -> str:
    return _TAGS.sub("", _text(value)).strip()[:MAX_TITLE_CHARS]


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _loads(payload: bytes) -> object:
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("新闻") from exc
    return decoded


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(label)
    return value


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _error(label: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_FORMAT", message=f"{label}接口返回了无法识别的内容", status_code=502
    )
