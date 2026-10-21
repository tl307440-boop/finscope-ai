"""从网页 HTML 里取正文、标题和发布日期。只用标准库，不新增依赖。

做法是「文字密度」：把页面切成块，保留够长且大部分不是链接的块，导航菜单、栏目列表、
页脚版权行因为短、或几乎全是链接、或带备案字样被丢掉。它不是通用的正文抽取器：
由脚本渲染的页面、需要登录的页面取不到正文，调用方要把空结果如实告诉用户。
"""

import codecs
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

MIN_CJK_BLOCK_CHARS = 12
MIN_LATIN_BLOCK_CHARS = 40
MAX_LINK_SHARE = 0.5
MAX_PARSE_BYTES = 1_500_000
_HEAD_BYTES = 4096
_SKIP = {"script", "style", "noscript", "nav", "footer", "aside", "svg", "iframe", "select"}
_BREAK = {
    "p", "div", "br", "tr", "td", "th", "li", "ul", "ol", "table", "section", "article",
    "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6",
}  # fmt: skip
_HEADINGS = {"h1", "h2", "h3"}
# 出现在正文之后的栏目标题：到这里文章就结束了，后面是推荐列表。
_TAIL_HEADINGS = (
    "相关阅读",
    "相关新闻",
    "相关推荐",
    "推荐阅读",
    "延伸阅读",
    "热门推荐",
    "更多精彩",
)
_BOILERPLATE = (
    "版权所有", "备案号", "ICP备", "网站标识码", "公网安备", "Copyright", "All Rights Reserved",
    "联系我们", "免责声明", "网站地图",
)  # fmt: skip
_META_DATES = (
    "article:published_time", "pubdate", "publishdate", "publish-date", "datepublished",
    "og:release_date", "dc.date", "firstpublishedtime",
)  # fmt: skip
_DATE = r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})"
# 正文里的日期必须带「发布时间」「来源」这类标签才算发布日期：
# 页头常有「今天是某年某月某日」，不能拿来当发布日期。
_LABELLED_DATE = re.compile(
    r"(?:发布时间|发布日期|发文日期|成文日期|更新时间|发布于|时间|日期"
    r"|来源[：:][^\n]{0,30}?|published(?: on)?|posted(?: on)?|date)\s*[：:]?\s*" + _DATE,
    re.IGNORECASE,
)
_PLAIN_DATE = re.compile(_DATE)
HEAD_CHARS = 1500
_CHARSET = re.compile(rb"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ArticleText:
    title: str
    text: str
    published: date | None
    truncated: bool


def decode_html(data: bytes) -> str:
    """BOM → 页面声明的编码 → UTF-8 → GB18030（中文站常见）。解不开的字符换成 �。"""

    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    declared = _CHARSET.search(data[:_HEAD_BYTES])
    if declared is not None:
        name = declared.group(1).decode("ascii", errors="ignore")
        try:
            return data.decode(codecs.lookup(name).name, errors="replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace")


def extract_article(data: bytes, *, max_chars: int, today: date) -> ArticleText:
    """`today` 用来丢掉晚于今天的「发布日期」（页面写错或是活动日期）。"""

    parser = _Page(max_chars=max_chars)
    parser.feed(decode_html(data[:MAX_PARSE_BYTES]))
    parser.close()
    parser.flush()
    text = "\n".join(parser.blocks)
    return ArticleText(
        title=parser.title(),
        text=text,
        published=_published(parser.metas, parser.head, today),
        truncated=parser.truncated,
    )


class _Page(HTMLParser):
    def __init__(self, *, max_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self._max = max_chars
        self.blocks: list[str] = []
        self.metas: dict[str, str] = {}
        self.truncated = False
        self.head = ""  # 过滤之前页面开头的文字，只用来找带标签的发布日期
        self._size = 0
        self._stopped = False
        self._skip = 0
        self._link = 0
        self._heading = ""
        self._in_title = False
        self._page_title: list[str] = []
        self._h1: str | None = None
        self._buffer: list[str] = []
        self._link_chars = 0

    def title(self) -> str:
        chosen = self._h1 or " ".join("".join(self._page_title).split())
        return chosen[:120]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta":
            key = (attr.get("name") or attr.get("property") or attr.get("itemprop") or "").lower()
            if key and attr.get("content"):
                self.metas.setdefault(key, attr["content"])
            return
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self.flush()
            self._skip += 1
            return
        if tag in _BREAK:
            self.flush()
            self._heading = tag if tag in _HEADINGS else ""
        if tag == "a":
            self._link += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIP:
            self.flush()
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a":
            self._link = max(0, self._link - 1)
        if tag in _BREAK:
            self.flush()
            self._heading = ""

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._page_title.append(data)
            return
        if self._skip or self._stopped:
            return
        self._buffer.append(data)
        if self._link:
            self._link_chars += len(" ".join(data.split()))

    def flush(self) -> None:
        text = " ".join("".join(self._buffer).replace("\u3000", " ").split())
        link_chars = self._link_chars
        heading = self._heading
        self._buffer = []
        self._link_chars = 0
        if not text or self._stopped:
            return
        if len(self.head) < HEAD_CHARS:
            self.head += text + "\n"
        if heading == "h1" and self._h1 is None and link_chars * 2 < len(text):
            self._h1 = text[:120]
        if heading and any(text.startswith(word) for word in _TAIL_HEADINGS) and self._size > 200:
            self._stopped = True
            return
        if not self._keep(text, link_chars, heading):
            return
        separator = 1 if self.blocks else 0  # 块之间用换行连接，也要算进长度
        room = self._max - self._size - separator
        if room <= 0:  # 已经满了还有正文：说明被截断了
            self.truncated = self._stopped = True
            return
        if len(text) > room:
            text = text[:room]
            self.truncated = self._stopped = True
        self.blocks.append(text)
        self._size += separator + len(text)

    @staticmethod
    def _keep(text: str, link_chars: int, heading: str) -> bool:
        if link_chars > len(text) * MAX_LINK_SHARE:
            return False
        if any(word in text for word in _BOILERPLATE):
            return False
        return _long_enough(text) or (bool(heading) and len(text) >= 4)


def _long_enough(text: str) -> bool:
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    minimum = MIN_CJK_BLOCK_CHARS if cjk * 10 >= len(text) * 3 else MIN_LATIN_BLOCK_CHARS
    return len(text) >= minimum


def _published(metas: dict[str, str], head: str, today: date) -> date | None:
    for key in _META_DATES:
        found = _date_in(_PLAIN_DATE, metas.get(key, ""), today)
        if found is not None:
            return found
    return _date_in(_LABELLED_DATE, head, today)


def _date_in(pattern: re.Pattern[str], text: str, today: date) -> date | None:
    match = pattern.search(text)
    if match is None:
        return None
    try:
        found = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
    return found if found <= today else None
