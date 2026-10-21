"""交易所栏目列表页的解析：深交所「本所要闻」、上交所「监管动态」。

只吃列表页的字节，返回 `FeedItem`（标题、链接、发布日），不联网、不执行页面脚本。
两个栏目的列表页都是服务端直接给出的静态 HTML：

- 深交所：标题和链接写在每条记录内联 `<script>` 的 `var curHref = '…'; var curTitle = '…';` 里
  （页面靠 `document.write` 再拼成 `<a>`），日期在 `<span class="time">`。这里只读脚本里的字符串，
  不运行它。页面模板一变就解析不出条目，返回空，不会写入错的数据。
- 上交所：`<dd><span>日期</span><a href title>标题</a></dd>`。

栏目页只有日期没有时刻，发布时间取当天 0 点（上海时间）：当天发布的文章当天就能被检索到，
代价是回测同一天内的时点时可能略早于真实发布时间。没有日期、没有标题或链接不在本站的条目丢弃，
不用抓取时间顶替。
"""

import re
from datetime import UTC, date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from app.services.rss_feed import FeedItem

SZSE_HOSTS = frozenset({"www.szse.cn"})
SSE_HOSTS = frozenset({"www.sse.com.cn"})
SZSE_NEWS_PAGE = "https://www.szse.cn/aboutus/trends/news/index.html"
SSE_SUPERVISION_PAGE = "https://www.sse.com.cn/disclosure/credibility/supervision/dynamic/"

_SZSE_PREFIX = "https://www.szse.cn/"
_SSE_PREFIX = "https://www.sse.com.cn/"
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


def _js_string(value: str) -> str:
    return value.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\")


class _SzseNews(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str, str]] = []
        self._depth = 0  # 在 `ul.newslist` 里面的嵌套层数
        self._script: list[str] | None = None
        self._time: list[str] | None = None
        self._pending: tuple[str, str] | None = None

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
                self._pending = None
        elif tag == "script" and self._script is not None:
            self._remember("".join(self._script))
            self._script = None
        elif tag == "span" and self._time is not None:
            stamp = "".join(self._time)
            self._time = None
            if self._pending is not None:
                self.found.append((self._pending[0], self._pending[1], stamp))
                self._pending = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._time is not None:
            self._time.append(data)

    def _remember(self, script: str) -> None:
        href = _CUR_HREF.search(script)
        title = _CUR_TITLE.search(script)
        # 没有拿到完整的一对就清掉上一条：缺日期或缺标题的记录不能和下一条串起来。
        self._pending = (
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
