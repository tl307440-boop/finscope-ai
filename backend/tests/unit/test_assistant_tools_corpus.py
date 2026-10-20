from datetime import UTC, date, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from app.services.assistant.blocks import blocks_for
from app.services.assistant.grounding import check
from app.services.assistant.results import Passage, ToolResult
from app.services.assistant.tool_cache import NEVER_CACHED
from app.services.assistant.tools import default_registry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import CorpusSearchArgs
from app.services.assistant.tools.quote import QuoteTableArgs, QuoteTableTool
from pydantic import ValidationError
from sqlalchemy import Engine

SPEECH = (
    "The Committee judges that the risks to achieving its employment and inflation goals "
    "are roughly in balance. Inflation has eased over the past year but remains\nsomewhat "
    'elevated at 2.9 percent. Some participants noted "upside risks" | not all.'
)


def _ctx() -> ToolContext:
    ctx = ToolContext(
        engine=cast(Engine, None), user_id=uuid4(), now=datetime(2026, 10, 20, 4, tzinfo=UTC)
    )
    ctx.book.add(
        ToolResult(
            tool="corpus_search",
            title="inflation",
            columns=(),
            rows=(),
            as_of=date(2026, 9, 17),
            source="本系统语料库",
            passages=(
                Passage("a", "FOMC statement", "美联储", "official", date(2026, 9, 17), SPEECH),
                Passage("b", "纪要", "你上传的文档", "user", date(2026, 9, 1), "良率提升到 92%。"),
            ),
        )
    )
    return ctx


def _quote(ctx: ToolContext, *rows: dict[str, Any], title: str | None = None) -> ToolResult:
    raw: dict[str, Any] = {"rows": list(rows)}
    if title is not None:
        raw["title"] = title
    return QuoteTableTool().run(ctx, QuoteTableArgs.model_validate(raw))


def _row(original: str, *, source: int = 1, note: str = "通胀仍偏高", stance: str = "偏鹰") -> dict:
    return {"source": source, "original": original, "stance": stance, "note": note}


def test_quotes_must_be_verbatim_up_to_width_and_whitespace() -> None:
    ctx = _ctx()
    result = _quote(
        ctx,
        # 原文跨行、模型抄成一行；全角数字按 NFKC 归一。
        _row("remains somewhat elevated at ２.9 percent", note="通胀 2.9% 仍偏高"),
        # 资料文本里 `"` 显示成 `'`、`|` 显示成 `｜`：照显示的样子抄也算原话。
        _row("Some participants noted 'upside risks' ｜ not all.", stance="偏鹰"),
        _row("良率提升到92%", source=2, stance="利好", note="良率到 92%"),
        title="9 月声明措辞",
    )
    assert result.status == "ok" and result.excerpt
    assert [row["source"] for row in result.rows] == ["[1]", "[1]", "[2]"]
    assert result.rows[0]["original"] == "remains somewhat elevated at ２.9 percent"
    assert result.as_of == date(2026, 9, 17)
    [block] = blocks_for("E2", result)
    assert block["type"] == "table"
    assert [column["label"] for column in block["columns"]] == ["来源", "原文", "倾向", "解读"]


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        (_row("risks are balanced"), "原文不是来源 [1] 里的原话"),  # 改写过
        (_row("良率提升到 92%", source=1), "原文不是来源 [1] 里的原话"),  # 挂错来源
        (_row("roughly in balance", source=3), "没有来源 [3]"),
        (_row("roughly in balance", note="通胀 3.1%"), "解读里有来源 [1] 里找不到的数字"),
        (_row("roughly in balance", note="良率 92%"), "解读里有来源 [1] 里找不到的数字"),
    ],
)
def test_bad_rows_sink_the_whole_table_and_tell_only_the_model(
    row: dict[str, Any], problem: str
) -> None:
    ctx = _ctx()
    result = _quote(ctx, _row("roughly in balance"), row)
    assert result.status == "unavailable"
    assert "第 2 行" in (result.reason or "") and problem in (result.reason or "")
    assert blocks_for("E2", result) == []  # 读者看不到这条失败


def test_title_numbers_and_stance_digits_are_rejected() -> None:
    ctx = _ctx()
    titled = _quote(ctx, _row("roughly in balance"), title="2027 年展望")
    assert titled.status == "unavailable" and "标题" in (titled.reason or "")
    with pytest.raises(ValidationError):
        QuoteTableArgs.model_validate({"rows": [_row("roughly in balance", stance="偏鹰 2")]})
    with pytest.raises(ValidationError):
        QuoteTableArgs.model_validate({"rows": []})


def test_excerpt_text_does_not_ground_the_narrative() -> None:
    ctx = _ctx()
    ctx.book.add(_quote(ctx, _row("roughly in balance", note="通胀 2.9%"), title="声明措辞"))
    # 2.9 只在来源 [1] 里：叙述里不引用 [1] 就不放行，摘录表不能替它作证。
    assert not check("通胀 2.9%。", ctx.book).ok
    assert check("通胀 2.9%[1]。", ctx.book).ok


def test_args_need_at_least_one_real_keyword() -> None:
    assert CorpusSearchArgs(keywords_en=["FOMC"]).keywords_en == ["FOMC"]
    for bad in (
        {},
        {"keywords_zh": ["  "]},
        {"keywords_zh": ["良率", "x" * 41]},
        {"keywords_zh": ["k"] * 9},
        {"keywords_zh": ["良率"], "since": date(2026, 10, 2), "until": date(2026, 10, 1)},
        {"keywords_zh": ["良率"], "kind": "web"},
        {"keywords_zh": ["良率"], "query": "良率"},
    ):
        with pytest.raises(ValidationError):
            CorpusSearchArgs.model_validate(bad)


def test_tool_is_registered_only_when_enabled_and_never_cached() -> None:
    assert not {"corpus_search", "quote_table"} & set(default_registry().names)
    assert {"corpus_search", "quote_table"} <= set(default_registry(corpus_min_rank=0.0).names)
    assert {"corpus_search", "quote_table"} <= NEVER_CACHED
