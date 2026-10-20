"""用真实模型跑语料评测集（知识库设计 §9），量 CI 量不到的：模型给的关键词能不能查到、
引用挂没挂对、摘录表能不能过逐字核对。不进 CI（要模型、要花钱）。

    python -m app.scripts.assistant_corpus_smoke --test-db
    python -m app.scripts.assistant_corpus_smoke --test-db --only en-01,zh-06 --verbose

会往 DATABASE_URL 指向的库里写评测数据（带 `t-eval-` 前缀，结束时删掉），只对测试库用；
`--test-db` 是确认这一点的开关。模型用 LLM_* 配置，语料检索按 CORPUS_MIN_RANK。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.db.session import create_db_engine
from app.services.assistant.agent import Agent, AgentRequest, AgentResult, AgentSettings
from app.services.assistant.llm_tools import OpenAIToolModel, supports_tools
from app.services.assistant.responder import SYSTEM_PROMPT
from app.services.assistant.tools import default_registry
from app.services.assistant.tools.base import ToolContext
from app.services.corpus_eval import Loaded, cleanup, documents_of_chunks, load, read_spec


@dataclass(frozen=True)
class Outcome:
    question: dict[str, Any]
    result: AgentResult
    cited: list[str]

    @property
    def expected(self) -> list[str]:
        return list(self.question["expect"])

    def calls(self, tool: str) -> list[dict[str, Any]]:
        return [call for call in self.result.trace.get("calls", []) if call.get("tool") == tool]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-db", action="store_true", help="确认 DATABASE_URL 是测试库")
    parser.add_argument("--only", default="", help="只跑这些题，逗号分隔的题号")
    parser.add_argument("--verbose", action="store_true", help="打印每题的回答")
    args = parser.parse_args(argv)
    if not args.test_db:
        parser.error("会往库里写评测数据，确认是测试库后加 --test-db")
    settings = get_settings()
    if not (settings.llm_api_key and settings.llm_api_base_url and settings.llm_model):
        parser.error("没配 LLM_API_KEY / LLM_API_BASE_URL / LLM_MODEL")
    spec = read_spec()
    wanted = {item.strip() for item in args.only.split(",") if item.strip()}
    questions = [q for q in spec["questions"] if not wanted or q["id"] in wanted]
    engine = create_db_engine(settings.database_url)
    loaded = load(engine, spec)
    try:
        with httpx.Client() as client:
            ask = _asker(settings, loaded, client)
            outcomes = [ask(question) for question in questions]
    finally:
        cleanup(engine)
        engine.dispose()
    for outcome in outcomes:
        print(_line(outcome))
        if args.verbose:
            print(f"     {outcome.result.text or '（没有正文）'}")
    print(_summary(outcomes))
    return 0


def _asker(
    settings: Settings, loaded: Loaded, client: httpx.Client
) -> Callable[[dict[str, Any]], Outcome]:
    model = OpenAIToolModel(
        base_url=settings.llm_api_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        client=client,
    )
    agent = Agent(
        model=model,
        registry=default_registry(corpus_min_rank=settings.corpus_min_rank),
        settings=AgentSettings(
            max_steps=settings.assistant_max_steps,
            turn_seconds=settings.assistant_turn_seconds,
            fast_path=False,
        ),
        tools_supported=supports_tools(model, settings.llm_api_base_url),
        system_prompt=SYSTEM_PROMPT,
    )

    def ask(question: dict[str, Any]) -> Outcome:
        ctx = ToolContext(
            engine=loaded.engine,
            user_id=loaded.users[question["asker"]],
            now=loaded.as_of(question),
        )
        result = agent.run(AgentRequest(question=question["q"]), ctx, lambda _event: None, _never)
        keys = [p.key for n in (s["n"] for s in result.sources) if (p := ctx.book.source(n))]
        chunks = documents_of_chunks(loaded.engine, keys)
        cited = list(
            dict.fromkeys(loaded.names[chunks[key]] if key in chunks else "?" for key in keys)
        )
        return Outcome(question, result, cited)

    return ask


def _never() -> bool:
    return False


def _line(outcome: Outcome) -> str:
    searched = "检索" if outcome.calls("corpus_search") else "未检索"
    quotes = outcome.calls("quote_table")
    quoted = f" 摘录 {sum(c.get('status') == 'ok' for c in quotes)}/{len(quotes)}" if quotes else ""
    return (
        f"{outcome.question['id']:<6} {outcome.result.grounding:<7} {searched}{quoted} "
        f"引用 {outcome.cited} 期望 {outcome.expected}"
    )


def _summary(outcomes: list[Outcome]) -> str:
    answerable = [o for o in outcomes if o.expected]
    refusals = [o for o in outcomes if not o.expected]
    quotes = [call for o in outcomes for call in o.calls("quote_table")]
    rows = [
        ("调了检索的比例", _rate(answerable, lambda o: bool(o.calls("corpus_search")))),
        ("引用命中期望文档", _rate(answerable, lambda o: bool(set(o.cited) & set(o.expected)))),
        ("引用了期望以外的文档", _rate(answerable, lambda o: bool(set(o.cited) - set(o.expected)))),
        ("核对全部通过", _rate(answerable, lambda o: o.result.grounding == "full")),
        ("该拒答时没引用任何资料", _rate(refusals, lambda o: not o.cited)),
        ("摘录表通过", _rate(quotes, lambda c: c.get("status") == "ok")),
    ]
    return "\n".join(f"{label}：{value}" for label, value in rows)


def _rate(items: list[Any], ok: Callable[[Any], bool]) -> str:
    if not items:
        return "—（没有样本）"
    hits = sum(1 for item in items if ok(item))
    return f"{hits}/{len(items)} = {hits / len(items):.2f}"


if __name__ == "__main__":
    sys.exit(main())
