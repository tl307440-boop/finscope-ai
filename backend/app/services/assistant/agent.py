"""编排器：有上限的工具调用循环 + 按句校验推送（规格 §6）。

一个回合的流程：
1. 快速路径（可关）：一眼能看出要查什么的问题先直取数据，
   结果以「模型已调用过这些工具」的形式放进消息；
2. 工具调用循环：模型按需调用数据工具，最多 `max_steps` 轮，每轮最多并行 3 个；
   用完后强制写叙述。给了技能清单（`SkillBook`）时，一开始只开放核心工具和 `load_skill`，
   其余工具随模型加载的技能开放（渐进式披露，见 `skills.py`）；
3. 叙述按句校验：每句都要通过 `grounding` 才推给浏览器；
   某句不通过，改写一次，仍不通过就隐藏后文；
4. 总时长、取消：每轮开始前和每次模型调用返回后检查；
   超时或取消不再写叙述，已推出的块保留。

模型不支持工具调用时走规划器模式：先让模型输出要调用的工具（JSON），服务端执行，
再让它写叙述。事件通过 `emit` 交给调用方（回合服务负责编码成 SSE 并落库），
这里不碰数据库连接和 HTTP。
"""

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.assistant import fast_path
from app.services.assistant.blocks import blocks_for, notice_block
from app.services.assistant.evidence import render, source_payload
from app.services.assistant.grounding import CITATION, Grounder, GroundingResult
from app.services.assistant.llm_tools import ModelCallError, ModelReply, ToolCall, ToolModel
from app.services.assistant.sentences import split_sentences
from app.services.assistant.skills import LOAD_SKILL, SkillBook
from app.services.assistant.skills import load as load_skill
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.macro import SERIES

logger = logging.getLogger(__name__)

PROMPT_VERSION = "assistant_v4"
MAX_PARALLEL = 3
PLANNER_MAX_CALLS = 4
MAX_ENTITIES = 8
MAX_QUERIED = 6
# 只加载技能、没查数据的轮次不占步数，但最多这么多轮，防止模型反复加载。
MAX_SKILL_ROUNDS = 2

NO_PLAN_TEXT = "没能理解这个问题，请换个问法，或直接说明要查的序列、证券或指标。"
NO_TEXT_NOTICE = "没能生成可核对的文字回答，请看下方查到的数据。"
HIDDEN_NOTICE = "后续内容没能通过核对，已隐藏；请看下方查到的数据。"
IGNORED_CALL = "本轮同时调用的工具太多，这个调用被忽略；需要的话下一步再调用。"
NO_MORE_TOOLS = (
    "这一回合已经不能再调用工具了。请直接根据已经查到的资料写回答；"
    "没算出来的数就不写，或直接引用资料里的原始数字。"
)

STEP_LABELS = {
    "macro_series": "查宏观序列",
    "macro_compare": "对比宏观序列",
    "instrument_search": "搜索证券",
    "price_bars": "查日线",
    "financial_metrics": "查财务指标",
    "board_strength": "查行业强弱",
    "industry_board": "查板块行情",
    "custom_indicator": "查自定义指标",
    "watchlist": "读我的自选",
    "calc": "计算",
    "corpus_search": "检索资料",
    "filing_fetch": "读取公告",
    "web_search": "联网检索",
    "news_flash": "查实时快讯",
    "fetch_url": "读取网页",
    "board_detail": "查板块详情",
    "research_reports": "查研报列表",
    "market_sentiment": "查市场热度",
    "hotspot_events": "查披露事件",
    "quote_table": "整理原文摘录",
}

Emit = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class AgentSettings:
    max_steps: int
    turn_seconds: float
    fast_path: bool


@dataclass(frozen=True)
class HistoryMessage:
    role: str  # user / assistant
    content: str


@dataclass(frozen=True)
class AgentRequest:
    question: str
    history: tuple[HistoryMessage, ...] = ()
    entities: tuple[str, ...] = ()
    # 上文查过的数据（工具、参数）：旧回合不带工具结果，追问里的「这些」靠它找回指代。
    queried: tuple[str, ...] = ()
    # 上一回合用过的工具：追问时直接开放，不必让模型再加载一次技能。
    carry_tools: tuple[str, ...] = ()


@dataclass
class AgentResult:
    status: str  # completed / cancelled / failed
    text: str
    blocks: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    as_of: date | None
    model: str
    grounding: str  # full / partial / none
    trace: dict[str, Any]
    # 正文里实际引用过的来源（前端 `parseSources` 的字段），按编号排序。
    sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _Prefetched:
    call_id: str
    name: str
    arguments: str
    text: str


@dataclass
class _Turn:
    request: AgentRequest
    ctx: ToolContext
    emit: Emit
    cancelled: Callable[[], bool]
    deadline: float
    messages: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    prefetched: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    # 按需开放：本回合加载过的技能、开放的工具（核心工具不在这里）。
    skills: list[str] = field(default_factory=list)
    enabled: set[str] = field(default_factory=set)
    skill_rounds: int = 0
    model_calls: int = 0
    counter: int = 0
    repaired: bool = False
    fixed_text: bool = False  # 叙述是服务端写死的说明，不经过模型也不做 grounding


class Agent:
    def __init__(
        self,
        *,
        model: ToolModel,
        registry: ToolRegistry,
        settings: AgentSettings,
        tools_supported: bool,
        system_prompt: str,
        clock: Callable[[], float] = time.monotonic,
        skills: SkillBook | None = None,
    ) -> None:
        """`skills` 为 None 时一开始就给全部工具；给了就只给核心工具 + `load_skill`，按需开放。"""

        self._model = model
        self._registry = registry
        self._settings = settings
        self._tools_supported = tools_supported
        self._prompt = system_prompt
        self._clock = clock
        self._skills = skills

    # ------------------------------------------------------------------ 入口
    def run(
        self,
        request: AgentRequest,
        ctx: ToolContext,
        emit: Emit,
        cancelled: Callable[[], bool],
    ) -> AgentResult:
        turn = _Turn(
            request=request,
            ctx=ctx,
            emit=emit,
            cancelled=cancelled,
            deadline=self._clock() + self._settings.turn_seconds,
        )
        turn.enabled.update(request.carry_tools)
        narrative: str | None = None
        failure: str | None = None
        try:
            narrative = self._produce(turn)
        except ModelCallError as exc:
            failure = exc.kind
        if cancelled():
            return self._result(turn, "cancelled", "", "none", failure)
        if narrative is None:
            return self._evidence_only(turn, failure)
        if turn.fixed_text:
            self._say(turn, narrative)
            return self._result(turn, "completed", narrative, "none", failure)
        pushed, grounding = self._publish(turn, narrative)
        return self._result(turn, "completed", "".join(pushed).strip(), grounding, failure)

    # ------------------------------------------------------------------ 生成叙述
    def _produce(self, turn: _Turn) -> str | None:
        turn.messages = self._base_messages(turn)
        fetched = self._prefetch(turn)
        if self._tools_supported:
            if fetched:
                self._append_prefetched(turn, fetched)
            return self._tool_loop(turn)
        return self._planner(turn)

    def _base_messages(self, turn: _Turn) -> list[dict[str, Any]]:
        request = turn.request
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._prompt}]
        messages.extend({"role": item.role, "content": item.content} for item in request.history)
        note = f"今天是 {turn.ctx.today_cn.isoformat()}（上海时间）。"
        if request.entities:
            note += "上文提到的实体：" + "；".join(request.entities) + "。"
        if request.queried:
            note += (
                "上文查过的数据：" + "；".join(request.queried) + "。"
                "追问里的「这些」「那几只」「刚才那个」指的就是它们；这些结果不在本回合的资料里，"
                "要引用其中的数字或名单，沿用同样的条件重新调用工具查一次，不要说上一轮没有给出过。"
            )
        messages.append({"role": "system", "content": note})
        messages.append({"role": "user", "content": request.question})
        return messages

    def _prefetch(self, turn: _Turn) -> list[_Prefetched]:
        """快速路径：一眼能看出要查什么的问题，先把数据取回来。"""

        if not self._settings.fast_path:
            return []
        done: list[_Prefetched] = []
        for index, planned in enumerate(fast_path.plan(turn.request.question), start=1):
            arguments = json.dumps(planned.args, ensure_ascii=False)
            text = self._execute(turn, planned.name, arguments)
            turn.prefetched.append(planned.name)
            turn.enabled.add(planned.name)
            done.append(_Prefetched(f"prefetch_{index}", planned.name, arguments, text))
        return done

    @staticmethod
    def _append_prefetched(turn: _Turn, fetched: list[_Prefetched]) -> None:
        """预取结果以「模型已经调用过这些工具」的形式放进消息。"""

        turn.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": item.call_id,
                        "type": "function",
                        "function": {"name": item.name, "arguments": item.arguments},
                    }
                    for item in fetched
                ],
            }
        )
        for item in fetched:
            turn.messages.append(
                {"role": "tool", "tool_call_id": item.call_id, "content": item.text}
            )

    def _tool_loop(self, turn: _Turn) -> str | None:
        steps = 0
        while steps < self._settings.max_steps:
            if self._stopped(turn):
                return None
            reply = self._call(turn, self._tools(turn))
            if self._stopped(turn):
                return None
            if not reply.tool_calls:
                return reply.content
            self._run_calls(turn, reply.tool_calls)
            only_skills = all(call.name == LOAD_SKILL for call in reply.tool_calls)
            if only_skills and turn.skill_rounds < MAX_SKILL_ROUNDS:
                turn.skill_rounds += 1
                continue
            steps += 1
        if self._stopped(turn):
            return None
        return self._final(turn)

    def _tools(self, turn: _Turn) -> list[dict[str, Any]]:
        if self._skills is None:
            return self._registry.definitions()
        opened = self._registry.definitions({*self._skills.core, *turn.enabled})
        return [*opened, self._skills.definition()]

    def _final(self, turn: _Turn) -> str | None:
        """步数用完：不再给工具，强制写叙述。模型仍要调用工具（DSML 写在正文里）时再催一次。"""

        reply = self._call(turn, None)
        if not reply.tool_calls:
            return reply.content
        if self._stopped(turn):
            return None
        reply = self._call(turn, None, extra=[{"role": "system", "content": NO_MORE_TOOLS}])
        return None if reply.tool_calls else reply.content

    def _run_calls(self, turn: _Turn, calls: Sequence[ToolCall]) -> None:
        turn.messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in calls
                ],
            }
        )
        queried = 0
        for call in calls:
            if call.name == LOAD_SKILL and self._skills is not None:
                text = self._load_skill(turn, self._skills, call.arguments)
            elif queried < MAX_PARALLEL:
                queried += 1
                text = self._execute(turn, call.name, call.arguments)
                # 模型没加载技能就直接调用了技能里的工具：照常执行，之后也一直给它。
                turn.enabled.add(call.name)
            else:
                text = IGNORED_CALL
            turn.messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

    def _load_skill(self, turn: _Turn, skills: SkillBook, arguments: str) -> str:
        """加载技能：做法说明作为工具结果回给模型，技能带的工具从下一次调用起开放。

        不是查数据，不登记证据、不出数据块；步骤列表里照样显示，读者看得到助手展开了哪些能力。
        """

        turn.counter += 1
        step_id = f"s{turn.counter}"
        turn.emit({"type": "step", "id": step_id, "label": "加载技能…", "status": "running"})
        loaded = load_skill(skills, arguments, turn.skills)
        turn.enabled.update(loaded.tools)
        turn.skills.extend(name for name in loaded.names if name not in turn.skills)
        label = "、".join(loaded.titles) or "没有这个技能"
        status = "ok" if loaded.ok else "unavailable"
        done = {"id": step_id, "label": f"加载技能：{label}", "status": status}
        turn.steps.append(done)
        turn.emit({"type": "step", **done})
        turn.calls.append(
            {
                "step": turn.counter,
                "tool": LOAD_SKILL,
                "args": {"names": list(loaded.names)},
                "status": status,
            }
        )
        return loaded.text

    # ------------------------------------------------------------------ 规划器模式
    def _planner(self, turn: _Turn) -> str | None:
        if self._stopped(turn):
            return None
        reply = self._call(
            turn, None, extra=[{"role": "system", "content": self._plan_prompt(turn)}]
        )
        plan = _parse_plan(reply.content)
        if plan is None:
            turn.fixed_text = True
            return NO_PLAN_TEXT
        for name, args in plan:
            if self._stopped(turn):
                return None
            self._execute(turn, name, json.dumps(args, ensure_ascii=False))
        if self._stopped(turn):
            return None
        turn.messages.append({"role": "system", "content": self._evidence_text(turn)})
        return self._call(turn, None).content

    def _plan_prompt(self, turn: _Turn) -> str:
        tools = [
            {
                "name": item["function"]["name"],
                "description": item["function"]["description"],
                "parameters": item["function"]["parameters"],
            }
            for item in self._registry.definitions()
        ]
        known = self._evidence_text(turn) if len(turn.ctx.book) else "（还没有查任何资料）"
        return (
            "现在先不要回答问题，只规划要查哪些数据。可用工具如下（JSON）：\n"
            + json.dumps(tools, ensure_ascii=False)
            + "\n\n只输出一个 JSON 对象，不要任何其他文字："
            + '{"calls":[{"tool":"工具名","args":{...}}]}，最多 4 个调用；'
            + '不需要再查数据就输出 {"calls":[]}。\n\n已有资料：\n'
            + known
        )

    @staticmethod
    def _evidence_text(turn: _Turn) -> str:
        body = "\n".join(render(item) for item in turn.ctx.book) or "（没有查到任何资料）"
        return "已查到的资料如下，回答只能依据它们：\n" + body

    # ------------------------------------------------------------------ 工具执行
    def _execute(self, turn: _Turn, name: str, arguments: str) -> str:
        turn.counter += 1
        step_id = f"s{turn.counter}"
        label = STEP_LABELS.get(name, name[:30])
        turn.emit({"type": "step", "id": step_id, "label": f"{label}…", "status": "running"})
        outcome = self._registry.execute(name, arguments, turn.ctx)
        result = outcome.result
        item = turn.ctx.book.add(result)
        done = {"id": step_id, "label": f"{label}：{result.title}", "status": result.status}
        turn.steps.append(done)
        turn.emit({"type": "step", **done})
        for block in blocks_for(item.id, result):
            turn.blocks.append(block)
            turn.emit({"type": "block", "block": block})
        turn.calls.append(_trace_entry(turn.counter, item.id, outcome))
        return render(item)

    # ------------------------------------------------------------------ 模型调用
    def _call(
        self,
        turn: _Turn,
        tools: list[dict[str, Any]] | None,
        extra: Sequence[dict[str, Any]] = (),
    ) -> ModelReply:
        remaining = turn.deadline - self._clock()
        if remaining < 1.0:
            raise ModelCallError("timeout")
        reply = self._model.complete([*turn.messages, *extra], tools, timeout=remaining)
        turn.model_calls += 1
        for key, value in reply.usage.items():
            turn.usage[key] = turn.usage.get(key, 0) + value
        return reply

    def _stopped(self, turn: _Turn) -> bool:
        return turn.cancelled() or self._clock() >= turn.deadline

    # ------------------------------------------------------------------ 按句校验与推送
    def _publish(self, turn: _Turn, narrative: str) -> tuple[list[str], str]:
        grounder = Grounder(turn.ctx.book)
        pushed: list[str] = []
        failed = self._push(turn, grounder, narrative, pushed)
        if failed is None:
            return pushed, "full"
        turn.unmatched.extend(failed.unmatched)
        # 排错用：只记没对上的数字和日期（模型写的数，不是密钥或用户资料），不记整句。
        logger.warning("助手回答有句子没通过核对：%s", ", ".join(failed.unmatched) or "引用无效")
        rewritten = self._rewrite(turn, pushed, failed)
        if rewritten is not None:
            second = self._push(turn, grounder, rewritten, pushed, skip_repeats=True)
            if second is None:
                turn.repaired = True
                return pushed, "full"
            turn.unmatched.extend(second.unmatched)
        grounding = "partial" if "".join(pushed).strip() else "none"
        self._notice(turn, "warning", "NOT_GROUNDED", HIDDEN_NOTICE if pushed else NO_TEXT_NOTICE)
        return pushed, grounding

    def _push(
        self,
        turn: _Turn,
        grounder: Grounder,
        text: str,
        pushed: list[str],
        *,
        skip_repeats: bool = False,
    ) -> GroundingResult | None:
        """逐句校验并推送；遇到第一句不通过就停，返回它的校验结果。

        `skip_repeats`：改写时模型常把已经展示过的前文原样再写一遍，这些句子不再推第二次。
        """

        shown = {item.strip() for item in pushed} if skip_repeats else set()
        for sentence in split_sentences(text):
            if sentence.strip() and sentence.strip() in shown:
                continue
            result = grounder.check(sentence)
            if not result.ok:
                return result
            self._cite(turn, sentence)
            turn.emit({"type": "delta", "delta": sentence})
            pushed.append(sentence)
        return None

    @staticmethod
    def _cite(turn: _Turn, sentence: str) -> None:
        """句子引用了还没发过的来源：先发完整的来源清单，正文里的 [n] 一出来就能画成角标。"""

        known = {int(item["n"]) for item in turn.sources}
        added = False
        for number in sorted({int(m) for m in CITATION.findall(sentence)} - known):
            passage = turn.ctx.book.source(number)
            if passage is not None:
                turn.sources.append(source_payload(number, passage))
                added = True
        if added:
            turn.sources.sort(key=lambda item: int(item["n"]))
            turn.emit({"type": "sources", "sources": list(turn.sources)})

    def _rewrite(self, turn: _Turn, pushed: list[str], failed: GroundingResult) -> str | None:
        """改写一次：把已推出的前文和对不上的数字交给模型，要求接着往下写。"""

        if self._stopped(turn):
            return None
        problems = list(failed.unmatched) + [f"[{n}]" for n in failed.bad_citations]
        instruction = (
            "你上一次写的回答里，下面这些内容在资料里找不到或不合法："
            + "、".join(problems)
            + "。\n已经展示给用户的前文如下（保持原样，不要重复）：\n"
            + ("".join(pushed).strip() or "（无）")
            + "\n请从这里接着往下写，只能使用资料里出现过的数字和日期；用到原文片段的内容时"
            "在句末写它的编号 [n]，没有编号的资料不要写引用标记；"
            "资料里没有的结论就不要写。如果写不出可核对的内容，就简短说明已经查到了什么。"
        )
        try:
            reply = self._call(turn, None, extra=[{"role": "system", "content": instruction}])
        except ModelCallError:
            return None
        return reply.content if reply.content.strip() else None

    def _say(self, turn: _Turn, text: str) -> None:
        turn.emit({"type": "delta", "delta": text})

    def _notice(self, turn: _Turn, level: str, code: str, message: str) -> None:
        block = notice_block(level, code, message)
        turn.blocks.append(block)
        turn.emit({"type": "block", "block": block})

    # ------------------------------------------------------------------ 结局
    def _evidence_only(self, turn: _Turn, failure: str | None) -> AgentResult:
        """没有叙述（超时、模型出错）：有证据就只给证据，没有证据才算失败。

        走到这里而没有模型错误，只可能是回合时长用完了（取消在更早处已返回）。
        """

        reason = failure or "timeout"
        if not any(item.result.ok for item in turn.ctx.book):
            return self._result(turn, "failed", "", "none", reason)
        self._notice(turn, "warning", "NOT_GROUNDED", NO_TEXT_NOTICE)
        return self._result(turn, "completed", "", "none", reason)

    def _result(
        self, turn: _Turn, status: str, text: str, grounding: str, failure: str | None
    ) -> AgentResult:
        if any(item.get("kind") == "web" for item in turn.sources) and not any(
            block.get("code") == "WEB_EPHEMERAL" for block in turn.blocks
        ):
            self._notice(
                turn,
                "info",
                "WEB_EPHEMERAL",
                "联网来源只在这一次回答里显示，刷新后不再保存。需要的话可以重新提问。",
            )
        book = turn.ctx.book
        as_of = max((item.result.as_of for item in book), default=None)
        trace: dict[str, Any] = {
            "prompt": PROMPT_VERSION,
            "model": self._model.model_name,
            "mode": "tools" if self._tools_supported else "planner",
            "fast_path": turn.prefetched,
            "skills": turn.skills,
            "calls": turn.calls,
            "model_calls": turn.model_calls,
            "usage": turn.usage,
            "repaired": turn.repaired,
            "unmatched": list(dict.fromkeys(turn.unmatched)),
            "failure": failure,
        }
        return AgentResult(
            status=status,
            text=text,
            blocks=turn.blocks,
            steps=turn.steps,
            as_of=as_of or turn.ctx.today_cn,
            model=self._model.model_name,
            grounding=grounding,
            trace=trace,
            sources=[item for item in turn.sources if item.get("kind") != "web"],
        )


def _trace_entry(number: int, evidence_id: str, outcome: Any) -> dict[str, Any]:
    result = outcome.result
    entry: dict[str, Any] = {
        "step": number,
        "tool": outcome.name,
        "args": outcome.args,
        "status": result.status,
        "cached": outcome.cached,
        "ms": outcome.millis,
        "evidence": evidence_id,
        "rows": len(result.rows),
        "truncated": result.truncated,
    }
    if result.passages:
        entry["passages"] = len(result.passages)
    if outcome.name == "instrument_search" and result.status != "unavailable":
        entry["entities"] = [
            {"symbol": row.get("symbol"), "name": row.get("name"), "market": row.get("market")}
            for row in result.rows[:5]
        ]
    return entry


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _parse_plan(content: str) -> list[tuple[str, dict[str, Any]]] | None:
    """规划器模式：解析模型给的调用计划；JSON 不合法返回 None（只给「没能理解」的说明，不猜）。"""

    text = _FENCE.sub("", content.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    calls = data.get("calls") if isinstance(data, dict) else None
    if not isinstance(calls, list):
        return None
    plan: list[tuple[str, dict[str, Any]]] = []
    for item in calls[:PLANNER_MAX_CALLS]:
        if not isinstance(item, dict) or not isinstance(item.get("tool"), str):
            return None
        args = item.get("args", {})
        if not isinstance(args, dict):
            return None
        plan.append((item["tool"], args))
    return plan


def entity_notes(traces: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """从旧回合的 `tool_trace` 汇总「上文提到的实体」，让「那它最近一个月呢」这类追问有指代。

    只给名称和代码，不给证券 id：`price_bars` 只认本回合搜索到的 id，所以模型要重新搜一次。
    """

    notes: list[str] = []
    for trace in traces:
        calls = trace.get("calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            notes.extend(_notes_for(call))
    unique = list(dict.fromkeys(notes))
    return tuple(unique[-MAX_ENTITIES:])


# 这些工具的指代已经由「上文提到的实体」带上，或本身不是数据查询。
_NOT_QUERIED = frozenset(
    {LOAD_SKILL, "calc", "instrument_search", "macro_series", "macro_compare", "custom_indicator"}
)
_HIDDEN_ARGS = frozenset({"instrument_id", "instrument_ids"})


def query_notes(traces: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """从旧回合的 `tool_trace` 汇总「上文查过的数据」：工具名和参数，不含结果行。

    只有数据没有文字的回合（回答没通过核对、只给了表）也在里面，追问才找得到「这些」指什么。
    """

    notes: list[str] = []
    for trace in traces:
        for call in _calls(trace):
            tool = call.get("tool")
            if not isinstance(tool, str) or tool in _NOT_QUERIED:
                continue
            if call.get("status") not in ("ok", "stale"):
                continue
            raw = call.get("args")
            args = (
                {k: v for k, v in raw.items() if k not in _HIDDEN_ARGS and v is not None}
                if isinstance(raw, dict)
                else {}
            )
            shown = ", ".join(f"{key}={_short(value)}" for key, value in args.items())
            notes.append(f"{STEP_LABELS.get(tool, tool[:30])}（{tool}({shown})）")
    unique = list(dict.fromkeys(notes))
    return tuple(unique[-MAX_QUERIED:])


def carry_tools(traces: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """最近一个有工具调用的旧回合用过的工具：追问时直接开放。"""

    for trace in reversed(traces):
        names = [
            tool
            for call in _calls(trace)
            if isinstance(tool := call.get("tool"), str) and tool != LOAD_SKILL
        ]
        if names:
            return tuple(dict.fromkeys(names))
    return ()


def _calls(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    calls = trace.get("calls")
    if not isinstance(calls, list):
        return []
    return [call for call in calls if isinstance(call, dict)]


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    return text if len(text) <= 60 else text[:60] + "…"


def _notes_for(call: Mapping[str, Any]) -> list[str]:
    tool = call.get("tool")
    raw = call.get("args")
    args: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
    if tool == "macro_series" and isinstance(args.get("code"), str):
        meta = SERIES.get(args["code"])
        return [f"序列 {args['code']}（{meta.name if meta else args['code']}）"]
    if tool == "macro_compare" and isinstance(args.get("codes"), list):
        return [f"序列 {code}" for code in args["codes"] if isinstance(code, str)]
    if tool == "custom_indicator" and isinstance(args.get("indicator"), str):
        group = args.get("group")
        return [
            f"指标 {args['indicator']}" + (f"（分组 {group}）" if isinstance(group, str) else "")
        ]
    if tool == "instrument_search":
        found = call.get("entities")
        if isinstance(found, list) and found and isinstance(found[0], dict):
            first = found[0]
            return [f"证券 {str(first.get('name'))[:40]}（{str(first.get('symbol'))[:20]}）"]
    return []
