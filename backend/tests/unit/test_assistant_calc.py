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


def test_model_cannot_pass_raw_numbers() -> None:
    with pytest.raises(ValueError):
        CalcArgs.model_validate({"op": "diff", "inputs": [{"value": "3"}, {"value": "2"}]})
    with pytest.raises(ValueError):
        CalcArgs.model_validate(
            {"op": "diff", "inputs": [{"evidence": "E1", "column": "value", "row": 1, "x": 1}]}
        )
