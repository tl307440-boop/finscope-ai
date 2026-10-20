"""`calc` 工具：算术只能基于本回合已有证据里的数字（规格 §7）。

模型不能直接给事实数字，只能引用证据单元格 `{"evidence": "E2", "column": "close", "row": -1}`，
所以算出来的结果也一定能追溯到入库的值。用 `Decimal` 计算，结果四舍五入到 4 位小数，
作为新证据登记，叙述里就可以引用它。

情景分析要用的假设数字（「若预期市盈率从 30 倍降到 20 倍」）用 `{"assume": 30, "label": "…"}`
直接给出。只要用到一个假设，结果就是**假设证据**（`ToolResult.hypothetical`）：
叙述里只有写明「假设 / 若 / 如果 / 情景」的句子才能引用它（见 `grounding.py`）。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, DivisionByZero, InvalidOperation
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.assistant.evidence import EvidenceBook, to_decimal
from app.services.assistant.results import Column, ToolResult, unavailable

MAX_VALUES = 500
MAX_ASSUMPTION = Decimal("1e12")
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


# 两种写法放在同一个对象里而不是联合类型：各家模型对 `anyOf` 的支持参差不齐。
class CellRef(BaseModel):
    """一个输入：要么引用资料（evidence + column [+ row]），要么是情景假设（assume + label）。"""

    model_config = ConfigDict(extra="forbid")

    evidence: str | None = Field(default=None, description="证据编号，如 E2")
    column: str | None = Field(default=None, description="数字列的 key")
    row: int | None = Field(
        default=None,
        description="第几行，从 1 开始；负数从末尾数（-1 是最后一行）；不给表示整列",
    )
    assume: float | None = Field(
        default=None,
        description="情景假设的数字（不是查到的数据）；必须同时给 label，不能和 evidence 一起用",
    )
    label: str | None = Field(
        default=None, min_length=1, max_length=40, description="假设的名字，如「假设预期市盈率」"
    )

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        cites = self.evidence is not None or self.column is not None or self.row is not None
        assumes = self.assume is not None or self.label is not None
        if cites == assumes:
            raise ValueError("每个输入要么给 evidence + column，要么给 assume + label")
        if cites and (self.evidence is None or self.column is None):
            raise ValueError("引用资料要同时给 evidence 和 column")
        if assumes and (self.assume is None or self.label is None):
            raise ValueError("假设要同时给 assume 和 label")
        return self


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
        collected = _collect(args, book)
        value, result_unit = _compute(args, collected.values, collected.unit or "")
    except CalcError as exc:
        return unavailable("calc", title, today, str(exc))
    basis = [*collected.ids, *(f"假设「{label}」" for label, _ in collected.assumptions)]
    hypothetical = bool(collected.assumptions)
    return ToolResult(
        tool="calc",
        title=f"{title}（情景假设）" if hypothetical else title,
        columns=(Column("value", "结果", result_unit, "number"),),
        rows=({"value": plain_number(value)},),
        as_of=collected.as_of or today,
        source=f"计算（基于 {'、'.join(basis)}）",
        hypothetical=hypothetical,
        assumptions=collected.assumptions,
    )


@dataclass
class _Collected:
    values: list[Decimal] = field(default_factory=list)
    # 结果单位跟第一条引用的资料走；假设没有单位。
    unit: str | None = None
    as_of: date | None = None
    ids: list[str] = field(default_factory=list)
    assumptions: tuple[tuple[str, str], ...] = ()

    def assume(self, items: Sequence[tuple[str, str]]) -> None:
        self.assumptions = (*self.assumptions, *(i for i in items if i not in self.assumptions))


def _collect(args: CalcArgs, book: EvidenceBook) -> _Collected:
    collected = _Collected()
    for ref in args.inputs:
        if ref.assume is not None and ref.label is not None:
            assumed = _assumed(ref.assume)
            collected.values.append(assumed)
            collected.assume([(ref.label, plain_number(assumed))])
            continue
        assert ref.evidence is not None and ref.column is not None
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
            collected.values.append(parsed)
        if collected.unit is None:
            collected.unit = column.unit
        if item.id not in collected.ids:
            collected.ids.append(item.id)
        collected.assume(item.result.assumptions)
        as_of = collected.as_of
        collected.as_of = item.result.as_of if as_of is None else max(as_of, item.result.as_of)
        if len(collected.values) > MAX_VALUES:
            raise CalcError(f"引用的数字超过 {MAX_VALUES} 个")
    return collected


def _assumed(raw: float) -> Decimal:
    try:
        number = Decimal(repr(raw))
    except InvalidOperation as exc:
        raise CalcError("假设的数字不合法") from exc
    if not number.is_finite() or abs(number) > MAX_ASSUMPTION:
        raise CalcError("假设的数字超出范围")
    return number


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
