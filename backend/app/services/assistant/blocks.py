"""由工具结果生成渲染块（规格 §9）。纯函数。

块全部由服务端生成，模型不写数字也不写块；时间序列出折线 + 指标卡，截面出表格，
状态不是 ok 就多一个提示块。证券搜索、`calc` 属于中间步骤，只在没数据时给提示，
不单独出块（过程里的步骤列表会列出它们）；用到情景假设的 `calc` 例外，出一条提示写明假设，
让读者分得清哪些数字不是查到的。
"""

from decimal import Decimal
from typing import Any

from app.services.assistant.calc import plain_number, round4
from app.services.assistant.evidence import to_decimal
from app.services.assistant.results import Column, ToolResult

# 工具名 → 不出数据块（只在没数据时出提示）。
QUIET_TOOLS = frozenset({"calc", "instrument_search", "corpus_search"})
# 失败只说明模型摘抄错了、引用写错了，原因回给模型改，不给读者出提示。
SILENT_FAILURES = frozenset({"quote_table", "calc"})
MIN_CHART_POINTS = 3


def notice_block(level: str, code: str, message: str) -> dict[str, Any]:
    return {"type": "notice", "level": level, "code": code, "message": message}


def blocks_for(evidence_id: str, result: ToolResult) -> list[dict[str, Any]]:
    if result.status == "unavailable":
        if result.tool in SILENT_FAILURES:
            return []
        return [
            notice_block("warning", "UNAVAILABLE", f"{result.title}：{result.reason or '没有数据'}")
        ]
    if result.hypothetical:
        return [_assumption_notice(result)]
    if result.tool in QUIET_TOOLS:
        return []
    visible = [column for column in result.columns if column.visible]
    blocks: list[dict[str, Any]]
    if _is_series(result, visible):
        blocks = [_line_chart(evidence_id, result, visible), _metric_cards(evidence_id, result)]
    else:
        blocks = [_table(evidence_id, result, visible)]
    if result.status == "stale":
        blocks.append(
            notice_block("warning", "STALE", f"{result.title}：{result.reason or '数据偏旧'}")
        )
    return blocks


def _assumption_notice(result: ToolResult) -> dict[str, Any]:
    given = "；".join(f"{label} = {value}" for label, value in result.assumptions)
    outcome = ""
    if result.rows and result.columns:
        column = result.columns[0]
        outcome = f"；算得 {result.rows[0].get(column.key)}{column.unit}"
    return notice_block("info", "ASSUMPTION", f"情景假设（不是查到的数据）：{given}{outcome}")


def _is_series(result: ToolResult, visible: list[Column]) -> bool:
    return (
        result.primary is not None
        and bool(visible)
        and visible[0].kind == "date"
        and len(result.rows) >= MIN_CHART_POINTS
        and any(column.key == result.primary for column in visible)
    )


def _column(result: ToolResult, key: str) -> Column:
    return next(column for column in result.columns if column.key == key)


def _line_chart(evidence_id: str, result: ToolResult, visible: list[Column]) -> dict[str, Any]:
    primary = _column(result, result.primary or "")
    x = visible[0].key
    return {
        "type": "line_chart",
        "evidence": evidence_id,
        "title": result.title,
        "x": x,
        "series": [{"key": primary.key, "label": primary.label, "unit": primary.unit}],
        "rows": [{x: row.get(x), primary.key: row.get(primary.key)} for row in result.rows],
    }


def _metric_cards(evidence_id: str, result: ToolResult) -> dict[str, Any]:
    primary = _column(result, result.primary or "")
    date_key = next(column.key for column in result.columns if column.kind == "date")
    latest, previous = result.rows[-1], result.rows[-2] if len(result.rows) > 1 else None
    item: dict[str, Any] = {
        "label": result.title,
        "value": latest.get(primary.key),
        "unit": primary.unit,
        "as_of": latest.get(date_key),
        "status": result.status,
    }
    change = _change(latest.get(primary.key), previous.get(primary.key) if previous else None)
    if change is not None:
        item["change"] = change
        item["change_label"] = "较上一期"
    return {"type": "metric_cards", "evidence": evidence_id, "items": [item]}


def _change(latest: str | None, previous: str | None) -> str | None:
    now, before = to_decimal(latest), to_decimal(previous)
    if now is None or before is None:
        return None
    diff: Decimal = round4(now - before)
    return plain_number(diff)


def _table(evidence_id: str, result: ToolResult, visible: list[Column]) -> dict[str, Any]:
    return {
        "type": "table",
        "evidence": evidence_id,
        "title": result.title,
        "columns": [
            {"key": column.key, "label": column.label, "unit": column.unit} for column in visible
        ],
        "rows": [{column.key: row.get(column.key) for column in visible} for row in result.rows],
        "truncated": result.truncated,
    }
