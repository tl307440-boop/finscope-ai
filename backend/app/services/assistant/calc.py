"""`calc` 工具：算术只能基于本回合已有证据里的数字（规格 §7）。

模型不能直接给数字，只能引用证据单元格 `{"evidence": "E2", "column": "close", "row": -1}`，
所以算出来的结果也一定能追溯到入库的值。用 `Decimal` 计算，结果四舍五入到 4 位小数，
作为新证据登记，叙述里就可以引用它。
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.assistant.evidence import EvidenceBook, to_decimal
from app.services.assistant.results import Column, ToolResult, unavailable

MAX_VALUES = 500
_QUANT = Decimal("0.0001")
_OP_LABELS = {
    "diff": "差值",
    "pct_change": "涨跌幅",
    "ratio": "比值",
    "mean": "平均",
    "percentile_rank": "分位",
    "scale": "换算",
}


class CalcError(ValueError):
    """引用不合法或算不出来；文字直接给模型看，不含任何外部内容。"""


class CellRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: str = Field(description="证据编号，如 E2")
    column: str = Field(description="数字列的 key")
    row: int | None = Field(
        default=None,
        description="第几行，从 1 开始；负数从末尾数（-1 是最后一行）；不给表示整列",
    )


class CalcArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["diff", "pct_change", "ratio", "mean", "percentile_rank", "scale"] = Field(
        description=(
            "diff=a-b；pct_change=(a-b)/b*100；ratio=a/b；mean=平均；"
            "percentile_rank=第一个值在其余值里的分位（<=它的占比）；scale=乘以 10 的 exponent 次方"
        )
    )
    inputs: list[CellRef] = Field(min_length=1, max_length=20)
    exponent: int | None = Field(default=None, ge=-12, le=12, description="scale 专用")


def run_calc(args: CalcArgs, book: EvidenceBook, today: date) -> ToolResult:
    """算不出来时返回 unavailable 并写明原因，不抛异常。"""

    title = f"计算：{_OP_LABELS[args.op]}"
    try:
        values, unit, as_of, ids = _collect(args, book, today)
        value, result_unit = _compute(args, values, unit)
    except CalcError as exc:
        return unavailable("calc", title, today, str(exc))
    return ToolResult(
        tool="calc",
        title=title,
        columns=(Column("value", "结果", result_unit, "number"),),
        rows=({"value": plain_number(value)},),
        as_of=as_of,
        source=f"计算（基于 {'、'.join(ids)}）",
    )


def _collect(
    args: CalcArgs, book: EvidenceBook, today: date
) -> tuple[list[Decimal], str, date, list[str]]:
    values: list[Decimal] = []
    unit = ""
    as_of: date | None = None
    ids: list[str] = []
    for ref in args.inputs:
        item = book.get(ref.evidence)
        if item is None:
            raise CalcError(f"没有这条证据：{ref.evidence}")
        column = next((col for col in item.result.columns if col.key == ref.column), None)
        if column is None or column.kind != "number":
            raise CalcError(f"{ref.evidence} 里没有数字列 {ref.column}")
        rows = item.result.rows
        picked = _pick(rows, ref)
        for number, row in picked:
            parsed = to_decimal(row.get(ref.column))
            if parsed is None:
                raise CalcError(f"{ref.evidence} 第 {number} 行的 {ref.column} 不是数字")
            values.append(parsed)
        if not ids:
            unit = column.unit
        if item.id not in ids:
            ids.append(item.id)
        as_of = item.result.as_of if as_of is None else max(as_of, item.result.as_of)
        if len(values) > MAX_VALUES:
            raise CalcError(f"引用的数字超过 {MAX_VALUES} 个")
    return values, unit, as_of or today, ids


def _pick(
    rows: Sequence[dict[str, str | None]], ref: CellRef
) -> list[tuple[int, dict[str, str | None]]]:
    if ref.row is None:
        return list(enumerate(rows, start=1))
    if ref.row == 0 or abs(ref.row) > len(rows):
        raise CalcError(f"{ref.evidence} 只有 {len(rows)} 行，没有第 {ref.row} 行")
    index = ref.row - 1 if ref.row > 0 else len(rows) + ref.row
    return [(index + 1, rows[index])]


def _compute(args: CalcArgs, values: list[Decimal], unit: str) -> tuple[Decimal, str]:
    op = args.op
    try:
        if op in {"diff", "pct_change", "ratio"}:
            if len(values) != 2:
                raise CalcError(f"{op} 需要刚好 2 个数字，实际 {len(values)} 个")
            first, second = values
            if op == "diff":
                return round4(first - second), unit
            if second == 0:
                raise CalcError("除数为 0，算不出来")
            if op == "pct_change":
                return round4((first - second) / second * 100), "%"
            return round4(first / second), ""
        if op == "mean":
            return round4(sum(values, Decimal(0)) / len(values)), unit
        if op == "percentile_rank":
            if len(values) < 2:
                raise CalcError("percentile_rank 需要至少 2 个数字：第一个是被比较的值，其余是样本")
            target, sample = values[0], values[1:]
            below = sum(1 for item in sample if item <= target)
            return round4(Decimal(below) * 100 / Decimal(len(sample))), "%"
        if len(values) != 1 or args.exponent is None:
            raise CalcError("scale 需要刚好 1 个数字和 exponent")
        return round4(values[0].scaleb(args.exponent)), unit
    except (DivisionByZero, InvalidOperation) as exc:
        raise CalcError("数值超出范围，算不出来") from exc


def round4(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_UP)


def plain_number(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text
