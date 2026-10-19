"""流式断句（规格 §6）。纯函数。

在 `。！？；` 和换行处断句；Markdown 表格每行、列表每项因为各占一行，自然各算一句。
`[` 还没闭合时不断句，避免把 `[1` 和 `]` 切开。小数点不是断句符，`3.1%` 不会被切开。

每一句都保留原有字符（包括结尾的换行），所有句子首尾相接就是模型的原文。
"""

_BOUNDARIES = frozenset("。！？；\n!?;")


class SentenceSplitter:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> list[str]:
        """追加一段文字，返回其中已经完整的句子；没写完的留着等下一段。"""

        self._buffer += chunk
        sentences: list[str] = []
        start = 0
        depth = 0
        for index, char in enumerate(self._buffer):
            if char == "[":
                depth += 1
            elif char == "]":
                depth = max(0, depth - 1)
            elif char in _BOUNDARIES and depth == 0:
                sentences.append(self._buffer[start : index + 1])
                start = index + 1
        self._buffer = self._buffer[start:]
        return sentences

    def flush(self) -> list[str]:
        """文字结束：把剩下的当作最后一句。"""

        rest, self._buffer = self._buffer, ""
        return [rest] if rest else []


def split_sentences(text: str) -> list[str]:
    splitter = SentenceSplitter()
    return [*splitter.feed(text), *splitter.flush()]
