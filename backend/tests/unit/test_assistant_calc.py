from datetime import date

import pytest
from app.services.assistant.calc import CalcArgs, run_calc
from app.services.assistant.evidence import EvidenceBook
from app.services.assistant.results import Column, ToolResult

TODAY = date(2026, 10, 19)


def _series(values: list[str], unit: str = "%") -> ToolResult:
    return ToolResult(
        tool="macro_series",
        title="美国 CPI 同比",
        columns=(Column("d", "观察日", "", "date"), Column("value", "数值", unit, "number")),
        rows=tuple(
            {"d": f"2026-0{index + 1}-01", "value": text} for index, text in enumerate(values)
        ),
        as_of=date(2026, 10, 17),
        source="FRED",
    )


def _book(*results: ToolResult) -> EvidenceBook:
    book = EvidenceBook()
    for result in results:
        book.add(result)
    return book


def _calc(book: EvidenceBook, **payload: object) -> ToolResult:
    return run_calc(CalcArgs.model_validate(payload), book, TODAY)


def _ref(row: int | None = None, evidence: str = "E1", column: str = "value") -> dict[str, object]:
    ref: dict[str, object] = {"evidence": evidence, "column": column}
    if row is not None:
        ref["row"] = row
    return ref


def test_diff_uses_evidence_cells_and_keeps_unit() -> None:
    book = _book(_series(["2.9", "3.1"]))
    result = _calc(book, op="diff", inputs=[_ref(-1), _ref(-2)])
    assert result.status == "ok"
    assert result.rows == ({"value": "0.2"},)
    assert result.columns[0].unit == "%"
    assert result.source == "计算（基于 E1）"
    assert result.as_of == date(2026, 10, 17)


def test_pct_change_rounds_half_up_to_four_places() -> None:
    book = _book(_series(["80", "100"]))
    result = _calc(book, op="pct_change", inputs=[_ref(2), _ref(1)])
    assert result.rows == ({"value": "25"},)
    assert result.columns[0].unit == "%"
    third = _calc(_book(_series(["3", "9"])), op="pct_change", inputs=[_ref(1), _ref(2)])
    assert third.rows == ({"value": "-66.6667"},)


def test_ratio_mean_scale_and_percentile_rank() -> None:
    book = _book(_series(["1", "2", "3", "4"], unit="亿元"))
    assert _calc(book, op="ratio", inputs=[_ref(1), _ref(4)]).rows == ({"value": "0.25"},)
    assert _calc(book, op="mean", inputs=[_ref()]).rows == ({"value": "2.5"},)
    scaled = _calc(book, op="scale", inputs=[_ref(4)], exponent=-1)
    assert scaled.rows == ({"value": "0.4"},)
    # 第一个值 3 在样本 [1,2,3,4] 里，<=3 的占 75%
    ranked = _calc(book, op="percentile_rank", inputs=[_ref(3), _ref()])
    assert ranked.rows == ({"value": "75"},)


def test_calc_result_is_registered_as_new_evidence() -> None:
    book = _book(_series(["2.9", "3.1"]))
    result = _calc(book, op="diff", inputs=[_ref(2), _ref(1)])
    registered = book.add(result)
    assert registered.id == "E2"
    follow = _calc(book, op="scale", inputs=[_ref(1, evidence="E2")], exponent=2)
    assert follow.rows == ({"value": "20"},)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"op": "diff", "inputs": [_ref(1, evidence="E9"), _ref(2)]}, "没有这条证据"),
        ({"op": "diff", "inputs": [_ref(1, column="nope"), _ref(2)]}, "没有数字列"),
        ({"op": "diff", "inputs": [_ref(1, column="d"), _ref(2)]}, "没有数字列"),
        ({"op": "diff", "inputs": [_ref(9), _ref(2)]}, "没有第 9 行"),
        ({"op": "diff", "inputs": [_ref(0), _ref(2)]}, "没有第 0 行"),
        ({"op": "diff", "inputs": [_ref(1)]}, "需要刚好 2 个数字"),
        ({"op": "scale", "inputs": [_ref(1)]}, "需要刚好 1 个数字和 exponent"),
        ({"op": "percentile_rank", "inputs": [_ref(1)]}, "至少 2 个数字"),
    ],
)
def test_illegal_references_return_unavailable_with_reason(
    payload: dict[str, object], reason: str
) -> None:
    result = _calc(_book(_series(["2.9", "3.1"])), **payload)
    assert result.status == "unavailable"
    assert reason in (result.reason or "")
    assert result.rows == ()


def test_division_by_zero_is_reported_not_raised() -> None:
    book = _book(_series(["5", "0"]))
    for op in ("ratio", "pct_change"):
        result = _calc(book, op=op, inputs=[_ref(1), _ref(2)])
        assert result.status == "unavailable"
        assert "除数为 0" in (result.reason or "")


def test_non_numeric_cell_is_rejected() -> None:
    result = ToolResult(
        tool="macro_series",
        title="t",
        columns=(Column("value", "数值", "", "number"),),
        rows=({"value": None},),
        as_of=TODAY,
        source="x",
    )
    outcome = _calc(_book(result), op="mean", inputs=[_ref(1)])
    assert outcome.status == "unavailable"
    assert "不是数字" in (outcome.reason or "")


def test_assumptions_make_a_hypothetical_result() -> None:
    # 「若预期市盈率从 30 倍降到 20 倍」：两个都是假设，没有引用资料。
    result = _calc(
        EvidenceBook(),
        op="pct_change",
        inputs=[{"assume": 20, "label": "压缩后市盈率"}, {"assume": 30, "label": "当前市盈率"}],
    )
    assert result.status == "ok"
    assert result.rows == ({"value": "-33.3333"},)
    assert result.hypothetical
    assert result.assumptions == (("压缩后市盈率", "20"), ("当前市盈率", "30"))
    assert result.title == "计算：涨跌幅（情景假设）"
    assert result.source == "计算（基于 假设「压缩后市盈率」、假设「当前市盈率」）"
    assert "20" not in result.source and "30" not in result.source


def test_assumption_mixed_with_evidence_keeps_unit_and_date() -> None:
    book = _book(_series(["2.9", "3.1"]))
    result = _calc(book, op="diff", inputs=[_ref(-1), {"assume": 0.25, "label": "假设降息幅度"}])
    assert result.rows == ({"value": "2.85"},)
    assert result.columns[0].unit == "%"
    assert result.as_of == date(2026, 10, 17)
    assert result.hypothetical and result.assumptions == (("假设降息幅度", "0.25"),)


def test_hypothesis_propagates_through_chained_calc() -> None:
    book = _book(_series(["2.9", "3.1"]))
    first = _calc(book, op="diff", inputs=[_ref(-1), {"assume": 0.5, "label": "冲击"}])
    book.add(first)
    follow = _calc(book, op="scale", inputs=[_ref(1, evidence="E2")], exponent=2)
    assert follow.hypothetical
    assert follow.assumptions == (("冲击", "0.5"),)
    plain = _calc(book, op="scale", inputs=[_ref(1)], exponent=2)
    assert not plain.hypothetical and plain.assumptions == ()


@pytest.mark.parametrize(
    "ref",
    [
        {"assume": 1},
        {"label": "x"},
        {"assume": 1, "label": "x", "evidence": "E1", "column": "value"},
        {"evidence": "E1"},
        {"column": "value"},
        {"row": 1},
        {},
    ],
)
def test_each_input_is_either_a_reference_or_an_assumption(ref: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        CalcArgs.model_validate({"op": "mean", "inputs": [ref]})


def test_out_of_range_assumption_is_reported() -> None:
    result = _calc(EvidenceBook(), op="mean", inputs=[{"assume": 1e13, "label": "太大"}])
    assert result.status == "unavailable"
    assert "超出范围" in (result.reason or "")


def test_calc_schema_stays_flat_for_models() -> None:
    from app.services.assistant.tools.base import compact_schema

    schema = compact_schema(CalcArgs)
    item = schema["properties"]["inputs"]["items"]
    properties = item["properties"]
    assert set(properties) == {"evidence", "column", "match", "row", "assume", "label"}
    assert all("anyOf" not in field for field in properties.values())
    assert properties["assume"]["type"] == "number"


def _bars() -> ToolResult:
    return ToolResult(
        tool="price_bars",
        title="中芯国际（688981.SH）日线",
        columns=(
            Column("trade_date", "交易日", "", "date"),
            Column("close", "收盘", "CNY", "number"),
        ),
        rows=(
            {"trade_date": "2025-10-21", "close": "127.19"},
            {"trade_date": "2026-04-21", "close": "110.5"},
            {"trade_date": "2026-10-21", "close": "100.24"},
        ),
        as_of=TODAY,
        source="日线",
    )


def _metrics() -> ToolResult:
    return ToolResult(
        tool="financial_metrics",
        title="中芯国际（688981.SH）财务指标",
        columns=(
            Column("metric_code", "指标", "", "text"),
            Column("period_end", "报告期", "", "date"),
            Column("value", "数值", "", "number"),
        ),
        rows=(
            {"metric_code": "gross_margin", "period_end": "2026-06-30", "value": "23.16"},
            {"metric_code": "revenue_yoy", "period_end": "2026-06-30", "value": "19.44"},
        ),
        as_of=TODAY,
        source="财务指标",
    )


@pytest.mark.parametrize("column", ["close", "收盘", "收盘(CNY)", "收盘（CNY）", " 收盘 (CNY) "])
def test_column_can_be_the_key_or_the_header_the_model_saw(column: str) -> None:
    """资料表头写的是「收盘(CNY)」，模型照抄表头当列名也要认得（线上问答里的失败）。"""

    book = _book(_bars())
    result = _calc(
        book,
        op="pct_change",
        inputs=[_ref(-1, column=column), _ref(1, column=column)],
    )
    assert result.status == "ok" and result.rows == ({"value": "-21.1888"},)


def test_match_picks_a_trading_day_instead_of_counting_rows() -> None:
    book = _book(_bars())
    result = _calc(
        book,
        op="pct_change",
        inputs=[
            {"evidence": "E1", "column": "close", "match": "2026-10-21"},
            {"evidence": "E1", "column": "close", "match": "2025-10-21"},
        ],
    )
    assert result.rows == ({"value": "-21.1888"},)


def test_long_table_metric_code_works_as_column_or_match() -> None:
    """财务指标是一行一个指标：模型把 gross_margin 当列名，按行筛选来理解。"""

    book = _book(_metrics(), _metrics())
    as_column = _calc(
        book, op="diff", inputs=[_ref(column="gross_margin"), _ref(column="revenue_yoy")]
    )
    assert as_column.status == "ok" and as_column.rows == ({"value": "3.72"},)
    as_match = _calc(
        book,
        op="diff",
        inputs=[
            {"evidence": "E1", "column": "value", "match": "gross_margin"},
            {"evidence": "E2", "column": "value", "match": "revenue_yoy"},
        ],
    )
    assert as_match.rows == ({"value": "3.72"},)


def test_unknown_column_and_missing_match_say_what_is_available() -> None:
    book = _book(_metrics())
    wrong = _calc(book, op="mean", inputs=[_ref(column="roe")])
    assert wrong.status == "unavailable"
    assert "没有数字列 roe" in (wrong.reason or "") and "可用的数字列：value" in (
        wrong.reason or ""
    )
    missing = _calc(book, op="mean", inputs=[{"evidence": "E1", "column": "value", "match": "roe"}])
    assert "没有「roe」这一行" in (missing.reason or "")
    with pytest.raises(ValueError):
        CalcArgs.model_validate({"op": "mean", "inputs": [{"match": "gross_margin"}]})


def test_model_cannot_pass_raw_numbers() -> None:
    with pytest.raises(ValueError):
        CalcArgs.model_validate({"op": "diff", "inputs": [{"value": "3"}, {"value": "2"}]})
    with pytest.raises(ValueError):
        CalcArgs.model_validate(
            {"op": "diff", "inputs": [{"evidence": "E1", "column": "value", "row": 1, "x": 1}]}
        )
