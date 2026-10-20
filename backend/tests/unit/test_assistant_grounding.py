from datetime import date

import pytest
from app.services.assistant.evidence import EvidenceBook
from app.services.assistant.grounding import Grounder, check
from app.services.assistant.results import Column, Passage, ToolResult

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
    # 数字和年月日之间带空格也按日期核对，不当成裸数字。
    assert check("2026 年 9 月 1 日的读数是 3.1%。", book).ok
    assert check("9 月的读数是 3.1%。", book).ok and check("9 月 1 日是 3.1%。", book).ok
    assert check("2026 年 9 月读数 3.1%。", book).ok
    assert not check("3 月的读数是 3.1%。", book).ok
    assert not check("2026 年 9 月 2 日的读数是 3.1%。", book).ok


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


def _percentile_book() -> EvidenceBook:
    """真实的期权 PCR 工具返回的列名：「成交量 PCR 5 年分位」，数字和汉字之间有空格。"""

    return _book(
        ToolResult(
            tool="custom_indicator",
            title="沪深300期权认沽认购比",
            columns=(
                Column("basket", "合约组", "", "text"),
                Column("trade_date", "交易日", "", "date"),
                Column("volume_pcr", "成交量 PCR", "", "number"),
                Column("percentile", "成交量 PCR 5 年分位", "%", "number"),
            ),
            rows=(
                {
                    "basket": "沪深300ETF期权",
                    "trade_date": "2026-10-16",
                    "volume_pcr": "0.8512",
                    "percentile": "32.1",
                },
            ),
            as_of=AS_OF,
            source="本系统自定义分析（交易所期权成交 / 指数估值数据）",
        )
    )


@pytest.mark.parametrize(
    "text",
    [
        "成交量 PCR 的 5 年分位为 32.1%。",
        "成交量 PCR 的5年分位为32.1%。",
        "成交量 PCR 处于近 5 年的 32.1% 分位。",
        "沪深 300 期权成交量 PCR 为 0.8512。",
    ],
)
def test_window_named_in_a_column_label_is_not_a_value_even_with_spaces(text: str) -> None:
    assert check(text, _percentile_book()).ok


@pytest.mark.parametrize(
    "text",
    [
        "近 3 年分位为 32.1%。",  # 资料里的窗口是 5 年
        "5 个月分位为 32.1%。",  # 单位不同
        "成交量 PCR 为 5。",  # 光秃秃的 5 仍然是数值
        "5 年均值是 32.1%。",  # 资料里是「5 年分位」，不是均值
    ],
)
def test_window_allowance_is_narrow(text: str) -> None:
    assert not check(text, _percentile_book()).ok


def test_letters_adjacent_to_digits_are_not_numbers() -> None:
    book = _cpi()
    assert check("看 Q3 的 M2 和 DGS10，最新 3.1%。", book).ok


def test_no_numbers_passes_even_without_evidence() -> None:
    assert check("目前没有查到相关数据，无法判断。", EvidenceBook()).ok
    assert not check("利率是 4.5%。", EvidenceBook()).ok


def test_unmatched_are_deduplicated_and_in_text_order() -> None:
    result = check("先是 7，再是 8，又是 7。", _cpi())
    assert result.unmatched == ("7", "8")


def _scenario_book() -> EvidenceBook:
    """「若预期市盈率从 30 倍降到 20 倍」：两个假设和它们算出的 -33.3333%。"""

    book = _cpi()
    book.add(
        ToolResult(
            tool="calc",
            title="计算：涨跌幅（情景假设）",
            columns=(Column("value", "结果", "%", "number"),),
            rows=({"value": "-33.3333"},),
            as_of=date(2026, 10, 19),
            source="计算（基于 假设「当前市盈率30倍」、假设「压缩后市盈率」）",
            hypothetical=True,
            assumptions=(("压缩后市盈率", "20"), ("当前市盈率30倍", "30")),
        )
    )
    return book


@pytest.mark.parametrize(
    "text",
    [
        "若预期市盈率从 30 倍降到 20 倍，股价对应下跌 33.3%。",
        "假设市盈率压缩到 20 倍，跌幅约 33.33%。",
        "如果估值回到 20 倍，对应 -33.3333%。",
        "在这个情景下，30 倍到 20 倍意味着下跌 33.3%。",
        "倘若压缩到 20 倍，CPI 同比仍是 3.1%。",
    ],
)
def test_hypothetical_numbers_pass_only_in_marked_sentences(text: str) -> None:
    assert check(text, _scenario_book()).ok


@pytest.mark.parametrize(
    "text",
    [
        "预期市盈率从 30 倍降到 20 倍，股价下跌 33.3%。",
        "股价下跌 33.3%。",
        "当前市盈率 30 倍。",  # 假设名里的数字不能当序列名放行
        "若干机构给出 20 倍估值。",  # 「若干」不是假设
    ],
)
def test_hypothetical_numbers_are_rejected_as_facts(text: str) -> None:
    assert not check(text, _scenario_book()).ok


def test_hypothetical_evidence_does_not_widen_dates() -> None:
    # 只有假设的计算以今天为截止日；今天不是任何事实资料的日期，不能借它放行。
    assert not check("2026-10-19 若市盈率为 20 倍。", _scenario_book()).ok
    assert check("2026-10-17 若市盈率为 20 倍。", _scenario_book()).ok


def test_marker_does_not_let_unrelated_numbers_through() -> None:
    result = check("若市盈率为 20 倍，利润增长 15%。", _scenario_book())
    assert not result.ok and result.unmatched == ("15%",)


def _passage(key: str, content: str) -> Passage:
    return Passage(
        key=key,
        title="2026年前三季度业绩预告",
        publisher="你上传的文档",
        corpus_kind="user",
        published=date(2026, 10, 16),
        content=content,
    )


def _cited_book() -> EvidenceBook:
    book = _cpi()
    book.add(
        ToolResult(
            tool="corpus_search",
            title="业绩预告",
            columns=(),
            rows=(),
            as_of=date(2026, 10, 16),
            source="本系统语料库",
            passages=(
                _passage("a", "预计2026年前三季度归母净利润12.5亿元至13亿元，同比增长30%。"),
                _passage("b", "公司于10月12日召开董事会，审议通过议案共5项。"),
            ),
        )
    )
    return book


@pytest.mark.parametrize(
    "text",
    [
        "公司预计前三季度归母净利润 12.5 亿元至 13 亿元，同比增长 30%[1]。",
        "预告覆盖 2026 年前三季度[1]。",
        "董事会在 10月12日 召开，审议了 5 项议案[2]。",
        "利润区间 12.5 亿元至 13 亿元[1]，董事会审议 5 项议案[2]。",
        "预告显示利润增长 30%[1]，美国 CPI 同比为 3.1%。",  # 数据工具的数字照常放行
    ],
)
def test_numbers_from_a_cited_passage_pass(text: str) -> None:
    assert check(text, _cited_book()).ok


@pytest.mark.parametrize(
    ("text", "unmatched"),
    [
        ("预计净利润 12.5 亿元。", ("12.5",)),  # 没写出处
        ("预计净利润 12.5 亿元[2]。", ("12.5",)),  # 挂错出处
        ("审议通过 5 项议案[1]。", ("5",)),
        ("董事会在 10月12日 召开[1]。", ("10月12日",)),
        ("董事会在 10月12日 召开。", ("10月12日",)),
    ],
)
def test_numbers_from_passages_need_the_right_citation(
    text: str, unmatched: tuple[str, ...]
) -> None:
    result = check(text, _cited_book())
    assert not result.ok and result.unmatched == unmatched


def test_citation_numbers_must_exist() -> None:
    result = check("利润增长 30%[3]。", _cited_book())
    assert result.bad_citations == (3,)
    assert check("没有资料[1]。", _cpi()).bad_citations == (1,)


def test_same_passage_keeps_one_number_across_searches() -> None:
    book = _cited_book()
    again = book.add(
        ToolResult(
            tool="corpus_search",
            title="再查一次",
            columns=(),
            rows=(),
            as_of=date(2026, 10, 16),
            source="本系统语料库",
            passages=(_passage("c", "新的一段。"), _passage("a", "（同一段）")),
        )
    )
    assert again.sources == (3, 1)
    assert book.source_numbers() == frozenset({1, 2, 3})
    assert book.source(1) is not None and "12.5" in book.source(1).content  # type: ignore[union-attr]


def test_grounder_can_be_reused_across_sentences() -> None:
    grounder = Grounder(_cpi())
    assert grounder.check("最新 3.1%。").ok
    assert not grounder.check("另一个 9.9%。").ok
    assert grounder.check("上月 2.9%。").ok
