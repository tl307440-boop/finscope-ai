import pytest
from app.services.assistant.sentences import SentenceSplitter, split_sentences


def test_splits_on_chinese_punctuation_and_newlines_keeping_every_character() -> None:
    text = (
        "美国 CPI 同比为 3.1%。上月为 2.9%！\n| 日期 | 值 |\n| 2026-09 | 3.1 |\n- 要点一\n- 要点二"
    )
    pieces = split_sentences(text)
    assert pieces == [
        "美国 CPI 同比为 3.1%。",
        "上月为 2.9%！",
        "\n",
        "| 日期 | 值 |\n",
        "| 2026-09 | 3.1 |\n",
        "- 要点一\n",
        "- 要点二",
    ]
    assert "".join(pieces) == text


def test_decimal_points_do_not_split() -> None:
    assert split_sentences("读数 3.1% 和 0.25。") == ["读数 3.1% 和 0.25。"]


def test_open_bracket_defers_the_cut_until_it_closes() -> None:
    splitter = SentenceSplitter()
    assert splitter.feed("最新 3.1%[") == []
    assert splitter.feed("1]。下一句") == ["最新 3.1%[1]。"]
    assert splitter.flush() == ["下一句"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
def test_result_does_not_depend_on_how_the_stream_is_chunked(size: int) -> None:
    text = "第一句 3.1%[1]。第二句；第三句！\n第四句"
    expected = split_sentences(text)
    splitter = SentenceSplitter()
    pieces: list[str] = []
    for index in range(0, len(text), size):
        pieces.extend(splitter.feed(text[index : index + size]))
    pieces.extend(splitter.flush())
    assert pieces == expected


def test_flush_on_empty_buffer_returns_nothing_and_resets() -> None:
    splitter = SentenceSplitter()
    assert splitter.flush() == []
    splitter.feed("没写完")
    assert splitter.flush() == ["没写完"]
    assert splitter.flush() == []
