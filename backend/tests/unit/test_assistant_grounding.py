from datetime import date

import pytest
from app.services.assistant.evidence import EvidenceBook
from app.services.assistant.grounding import Grounder, check
from app.services.assistant.results import Column, ToolResult

AS_OF = date(2026, 10, 17)


def _series(title: str, values: list[tuple[str, str]], unit: str = "%") -> ToolResult:
    return ToolResult(
        tool="macro_series",
        title=title,
        columns=(Column("d", "观察日", "", "date"), Column("value", "数值", unit, "number")),
        rows=tuple({"d": day, "value": value} for day, value in values),
        as_of=AS_OF,
        source="FRED",
    )


def _book(*results: ToolResult) -> EvidenceBook:
    book = EvidenceBook()
    for result in results:
        book.add(result)
    return book


def _cpi() -> EvidenceBook:
    return _book(_series("美国 CPI 同比", [("2026-08-01", "2.9"), ("2026-09-01", "3.1")]))


def test_numbers_in_evidence_pass_and_extra_numbers_are_flagged() -> None:
    book = _cpi()
    assert check("美国 CPI 同比最新为 3.1%，上月为 2.9%。", book).ok
    result = check("美国 CPI 同比最新为 3.1%，较上月上升 0.2 个百分点。", book)
    assert not result.ok
    assert result.unmatched == ("0.2 个百分点",)


def test_calc_result_makes_a_derived_number_citable() -> None:
    book = _cpi()
    book.add(
        ToolResult(
            tool="calc",
            title="计算：差值",
            columns=(Column("value", "结果", "%", "number"),),
            rows=({"value": "0.2"},),
            as_of=AS_OF,
            source="计算（基于 E1）",
        )
    )
    assert check("较上月上升 0.2 个百分点。", book).ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("3.1", True),
        ("3.10", True),  # 同值
        ("3.1%", True),
        ("3.2%", False),
        ("3", False),  # 不允许把 3.1 写成 3
        ("31", False),
        ("2.9%、3.1%", True),
        ("2.8%", False),
    ],
)
def test_matching_requires_same_value_without_dropping_decimals(text: str, ok: bool) -> None:
    assert check(text, _cpi()).ok is ok


def test_rounding_is_allowed_to_the_written_precision_only() -> None:
    book = _book(_series("某指标", [("2026-09-01", "3.14159")], unit=""))
    assert check("约 3.14。", book).ok
    assert check("约 3.1。", book).ok
    assert not check("约 3.15。", book).ok
    big = _book(_series("收盘", [("2026-09-01", "1680.5")], unit="元"))
    assert check("收盘 1681 元。", big).ok  # 证据 >= 100 允许取整（四舍五入）
    assert not check("收盘 1680 元。", big).ok
    assert check("收盘 1,680.5 元。", big).ok  # 千分位


def test_sign_is_compared_only_when_written() -> None:
    negative = _book(_series("变动", [("2026-09-01", "-0.3")]))
    assert check("较上月下降 0.3 个百分点。", negative).ok  # 不带负号按绝对值
    assert check("变动为 -0.3%。", negative).ok
    assert check("变动为 −0.3%。", negative).ok  # 真正的负号字符
    positive = _book(_series("变动", [("2026-09-01", "0.3")]))
    assert not check("变动为 -0.3%。", positive).ok


def test_unit_conversion_is_not_waved_through() -> None:
    book = _book(_series("社融", [("2026-09-01", "12000")], unit="亿元"))
    assert check("社融 12000 亿元。", book).ok
    assert not check("社融 1.2 万亿元。", book).ok


def test_dates_must_exist_in_evidence_at_the_written_precision() -> None:
    book = _cpi()
    assert check("2026-09-01 的读数是 3.1%。", book).ok
    assert check("2026年9月1日的读数是 3.1%。", book).ok
    assert check("2026年9月的读数是 3.1%。", book).ok
    assert check("2026-09 的读数是 3.1%。", book).ok
    assert check("9月1日的读数是 3.1%。", book).ok
    assert check("9月的读数是 3.1%。", book).ok
    assert check("2026年的数据显示 3.1%。", book).ok
    # as_of 也算已知日期
    assert check("数据截至 2026年10月17日。", book).ok
    bad = check("2026-09-02 的读数是 3.1%。", book)
    assert not bad.ok
    assert "2026-09-02" in bad.unmatched
    assert not check("2025年的读数是 3.1%。", book).ok
    assert not check("2026年9月2日的读数是 3.1%。", book).ok
    assert not check("2026-13-01 的读数是 3.1%。", book).ok  # 不存在的日期


def test_list_ordinals_and_nth_are_ignored_but_leading_decimals_are_not() -> None:
    book = _cpi()
    assert check("1. 美国 CPI 同比为 3.1%。\n2、上月为 2.9%。\n3) 暂无其他。", book).ok
    assert check("（1）先看最新值 3.1%。", book).ok
    assert check("- 1. 要点：3.1%", book).ok
    assert check("第 3 个观察点是 3.1%。", book).ok
    assert check("3.1% 是最新读数。", book).ok  # 行首小数不是序号
    assert not check("3.5% 是最新读数。", book).ok


def test_citations_must_be_registered_sources_and_evidence_refs_are_not_numbers() -> None:
    book = _cpi()
    result = check("最新为 3.1%[1]。", book)
    assert result.bad_citations == (1,)
    assert not result.ok
    assert check("最新为 3.1%[1]。", book, frozenset({1})).ok
    assert check("最新为 3.1%[E1]。", book).ok


def test_identifiers_and_series_names_are_allowed_but_not_values() -> None:
    names = _book(
        _series("美国10年期国债收益率", [("2026-09-01", "4.25")]),
        ToolResult(
            tool="instrument_search",
            title="搜索证券：茅台",
            columns=(
                Column("instrument_id", "证券 id", "", "text", visible=False),
                Column("symbol", "代码", "", "text"),
                Column("name", "名称", "", "text"),
            ),
            rows=(
                {
                    "instrument_id": "11111111-2222-3333-4444-555555555555",
                    "symbol": "600519",
                    "name": "贵州茅台",
                },
            ),
            as_of=AS_OF,
            source="本系统证券库",
        ),
    )
    assert check("美国10年期国债收益率为 4.25%。", names).ok
    assert check("贵州茅台（600519）的 10年期 数据。", names).ok
    assert not check("收益率已连续 10 个月上行。", names).ok  # 「10」不是序列名里的数字
    assert not check("过去10年的均值是 4.25%。", names).ok  # 「10年」不是「10年期」
    assert not check("代码 600520 的数据。", names).ok
    # 隐藏的 id 列不放行
    assert not check("编号 11111111。", names).ok


def test_letters_adjacent_to_digits_are_not_numbers() -> None:
    book = _cpi()
    assert check("看 Q3 的 M2 和 DGS10，最新 3.1%。", book).ok


def test_no_numbers_passes_even_without_evidence() -> None:
    assert check("目前没有查到相关数据，无法判断。", EvidenceBook()).ok
    assert not check("利率是 4.5%。", EvidenceBook()).ok


def test_unmatched_are_deduplicated_and_in_text_order() -> None:
    result = check("先是 7，再是 8，又是 7。", _cpi())
    assert result.unmatched == ("7", "8")


def test_grounder_can_be_reused_across_sentences() -> None:
    grounder = Grounder(_cpi())
    assert grounder.check("最新 3.1%。").ok
    assert not grounder.check("另一个 9.9%。").ok
    assert grounder.check("上月 2.9%。").ok
