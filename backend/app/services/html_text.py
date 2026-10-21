"""从 HTML 里取出正文。标准库解析，去掉 script / style / nav。不新增依赖。"""

from html.parser import HTMLParser

_SKIP = {"script", "style", "nav", "noscript"}
_BREAK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"}


def article_text(
    data: bytes, *, element_id: str | None = None, css_class: str | None = None
) -> str:
    """取指定容器里的可见文字。容器不存在就返回空，避免把导航菜单当正文。"""

    parser = _Article(element_id=element_id, css_class=css_class)
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


class _Article(HTMLParser):
    def __init__(self, *, element_id: str | None, css_class: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self._element_id = element_id
        self._css_class = css_class
        self.parts: list[str] = []
        self._capture = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag in _SKIP:
            self._skip += 1
        if self._capture:
            self._capture += 1
            if tag in _BREAK:
                self.parts.append("\n")
            return
        classes = set(attr.get("class", "").split())
        matched = (self._element_id is not None and attr.get("id") == self._element_id) or (
            self._css_class is not None and self._css_class in classes
        )
        if matched:
            self._capture = 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if self._capture:
            self._capture -= 1

    def handle_data(self, data: str) -> None:
        if self._capture and not self._skip:
            self.parts.append(data)
