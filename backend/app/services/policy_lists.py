"""政策类栏目列表的解析：深交所「本所要闻」、上交所「监管动态」、国务院「最新政策」、证监会要闻。

只吃列表页的字节，返回 `FeedItem`（标题、链接、发布日），不联网、不执行页面脚本：

- 深交所：标题和链接写在每条记录内联 `<script>` 的 `var curHref = '…'; var curTitle = '…';` 里
  （页面靠 `document.write` 再拼成 `<a>`），日期在 `<span class="time">`。这里只读脚本里的字符串，
  不运行它。页面模板一变就解析不出条目，返回空，不会写入错的数据。
- 上交所：`<dd><span>日期</span><a href title>标题</a></dd>`。
- 国务院：「最新政策」页用同目录的静态 JSON（`ZUIXINZHENGCE.json`，从新到旧）渲染列表，直接读它。
- 证监会：「证监会要闻」栏目自己的列表页是 2021 年冻结的旧版，当前条目由脚本加载；首页是静态的，
  上面列着最近的要闻链接，但链接旁没有日期，发布时间留空，由正文页的 `PubDate` 补上。

列表只有日期没有时刻时，发布时间先取当天 0 点（上海时间）；正文页元数据里有精确时间且是同一天时，
同步时改用精确时间（`page_published_at`）。没有日期、没有标题或链接不在栏目里的条目丢弃，
不用抓取时间顶替。
"""

import json
import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from app.services.rss_feed import FeedItem

SZSE_HOSTS = frozenset({"www.szse.cn"})
SSE_HOSTS = frozenset({"www.sse.com.cn"})
GOV_HOSTS = frozenset({"www.gov.cn"})
CSRC_HOSTS = frozenset({"www.csrc.gov.cn"})
SZSE_NEWS_PAGE = "https://www.szse.cn/aboutus/trends/news/index.html"
SSE_SUPERVISION_PAGE = "https://www.sse.com.cn/disclosure/credibility/supervision/dynamic/"
GOV_POLICY_LIST = "https://www.gov.cn/zhengce/zuixin/ZUIXINZHENGCE.json"
CSRC_HOME = "https://www.csrc.gov.cn/"
# 国务院的 JSON 一次给一千多条（几年的量），每轮只看最新的这么多条。
GOV_LIST_LIMIT = 50

_SZSE_PREFIX = "https://www.szse.cn/"
_SSE_PREFIX = "https://www.sse.com.cn/"
_GOV_POLICY_PREFIX = "https://www.gov.cn/zhengce/"
_CSRC_NEWS_PATH = re.compile(r"/csrc/c100028/c\d+/content\.shtml")
_PAGE_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T-]+(\d{2}):(\d{2})(?::(\d{2}))?)?")
_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
# 行首的 `var`：同一段脚本里有一行被注释掉的旧标题（`//var curTitle = …`），不能取它。
_CUR_HREF = re.compile(r"^[ \t]*var[ \t]+curHref[ \t]*=[ \t]*'([^'\n]*)'", re.MULTILINE)
_CUR_TITLE = re.compile(r"^[ \t]*var[ \t]+curTitle[ \t]*=[ \t]*'((?:[^'\\\n]|\\.)*)'", re.MULTILINE)
_SHANGHAI_OFFSET = timedelta(hours=8)


def parse_szse_news_list(payload: bytes, page_url: str) -> tuple[FeedItem, ...]:
    """深交所「本所要闻」第一页。"""

    parser = _SzseNews()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    return _items(parser.found, page_url, _SZSE_PREFIX)


def parse_sse_list(payload: bytes, page_url: str) -> tuple[FeedItem, ...]:
    """上交所「监管动态」第一页（同模板的其他栏目也适用）。"""

    parser = _SseList()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    return _items(parser.found, page_url, _SSE_PREFIX)


def parse_gov_policy_list(payload: bytes, page_url: str) -> tuple[FeedItem, ...]:
    """国务院「最新政策」的 JSON：`[{TITLE, URL, DOCRELPUBTIME}, …]`，只取 `/zhengce/` 下的条目。"""

    try:
        rows = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return ()
    if not isinstance(rows, list):
        return ()
    found = [
        (
            str(row.get("URL") or ""),
            str(row.get("TITLE") or ""),
            str(row.get("DOCRELPUBTIME") or ""),
        )
        for row in rows[:GOV_LIST_LIMIT]
        if isinstance(row, dict)
    ]
    return _items(found, page_url, _GOV_POLICY_PREFIX)


def parse_csrc_home_news(payload: bytes, page_url: str) -> tuple[FeedItem, ...]:
    """证监会首页上的「证监会要闻」链接（栏目 c100028），发布时间留空。"""

    parser = _Links()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    items: list[FeedItem] = []
    seen: set[str] = set()
    for href, text in parser.links:
        url = urljoin(page_url, href.strip())
        parts = urlsplit(url)
        title = " ".join(unescape(text).split())
        if (
            url in seen
            or parts.scheme != "https"
            or parts.hostname not in CSRC_HOSTS
            or not _CSRC_NEWS_PATH.fullmatch(parts.path)
            or not title
        ):
            continue
        seen.add(url)
        items.append(FeedItem(title=title, url=url, published_at=None))
    return tuple(items)


def page_published_at(payload: bytes, meta_name: str) -> datetime | None:
    """正文页 `<meta name=… content=…>` 里的发布时间（上海时间）转成 UTC；没有或看不懂返回 None。

    国务院写成 `2026-10-21-18:17:00`，证监会写成 `2026-10-19 19:03:25`；只有日期时取当天 0 点。
    """

    parser = _Meta(meta_name)
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    found = _PAGE_TIME.search(parser.content or "")
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(part or 0) for part in found.groups())
    try:
        local = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None
    return local - _SHANGHAI_OFFSET


def day_start_in_shanghai(day: date) -> datetime:
    """只有日期时，发布时间取当天 0 点（上海时间），以 UTC 存。"""

    return datetime(day.year, day.month, day.day, tzinfo=UTC) - _SHANGHAI_OFFSET


def _items(found: list[tuple[str, str, str]], page_url: str, prefix: str) -> tuple[FeedItem, ...]:
    items: list[FeedItem] = []
    seen: set[str] = set()
    for href, title, stamp in found:
        url = urljoin(page_url, href.strip())
        published = _day(stamp)
        clean = " ".join(unescape(title).split())
        if url in seen or not url.startswith(prefix) or not clean or published is None:
            continue
        seen.add(url)
        items.append(FeedItem(title=clean, url=url, published_at=day_start_in_shanghai(published)))
    return tuple(items)


def _day(text: str) -> date | None:
    found = _DAY.search(text)
    if found is None:
        return None
    year, month, day = (int(part) for part in found.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text)))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)


class _Meta(HTMLParser):
    """记下第一个 `name` 等于目标（不分大小写）的 `<meta>` 的 content。"""

    def __init__(self, name: str) -> None:
        super().__init__(convert_charrefs=True)
        self._name = name.lower()
        self.content: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta" or self.content is not None:
            return
        attr = {key: value or "" for key, value in attrs}
        if attr.get("name", "").lower() == self._name:
            self.content = attr.get("content", "")


def _js_string(value: str) -> str:
    return value.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


class _SzseNews(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, str]] = []
        self._depth = 0  # 在 `ul.newslist` 里面的嵌套层数
        self._script: list[str] | None = None
        self._time: list[str] | None = None
        # 不能叫 _pending：2026 年的 HTMLParser 安全补丁用这个名字做内部缓冲，
        # 子类再写成 None 的话，feed("") 会在缓冲上 append 而崩溃。
        self._record: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = {
            part for key, value in attrs if key == "class" and value for part in value.split()
        }
        if tag == "ul":
            if self._depth or "newslist" in classes:
                self._depth += 1
            return
        if not self._depth:
            return
        if tag == "script":
            self._script = []
        elif tag == "span" and "time" in classes:
            self._time = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul" and self._depth:
            self._depth -= 1
            if not self._depth:
                self._record = None
        elif tag == "script" and self._script is not None:
            self._remember("".join(self._script))
            self._script = None
        elif tag == "span" and self._time is not None:
            stamp = "".join(self._time)
            self._time = None
            if self._record is not None:
                self.found.append((self._record[0], self._record[1], stamp))
                self._record = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._time is not None:
            self._time.append(data)

    def _remember(self, script: str) -> None:
        href = _CUR_HREF.search(script)
        title = _CUR_TITLE.search(script)
        # 没有拿到完整的一对就清掉上一条：缺日期或缺标题的记录不能和下一条串起来。
        self._record = (
            (href.group(1), _js_string(title.group(1))) if href is not None and title else None
        )


class _SseList(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, str]] = []
        self._in_item = False
        self._date: list[str] | None = None
        self._text: list[str] | None = None
        self._stamp = ""
        self._href = ""
        self._title = ""
        self._label = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "dd":
            self._in_item = True
            self._date, self._text = None, None
            self._stamp = self._href = self._title = self._label = ""
        elif not self._in_item:
            return
        elif tag == "span" and not self._stamp:
            self._date = []
        elif tag == "a" and not self._href:
            self._href = attr.get("href", "")
            self._title = attr.get("title", "")
            self._text = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_item:
            return
        if tag == "span" and self._date is not None:
            self._stamp = "".join(self._date)
            self._date = None
        elif tag == "a" and self._text is not None:
            self._label = "".join(self._text)
            self._text = None
        elif tag == "dd":
            self._in_item = False
            title = self._title or self._label
            if self._href and title.strip() and self._stamp:
                self.found.append((self._href, title, self._stamp))

    def handle_data(self, data: str) -> None:
        if self._date is not None:
            self._date.append(data)
        elif self._text is not None:
            self._text.append(data)
