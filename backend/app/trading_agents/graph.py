import re
from collections.abc import Callable
from dataclasses import dataclass

RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
ANALYSTS = (
    "Market Analyst",
    "Sentiment Analyst",
    "News Analyst",
    "Fundamentals Analyst",
    "Memory Log",
)
RISK_SPEAKERS = ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst")


@dataclass(frozen=True)
class Trace:
    node_name: str
    content: str


@dataclass(frozen=True)
class DebateRound:
    round_no: int
    bull_claim: str
    bear_claim: str


@dataclass(frozen=True)
class RiskNote:
    profile: str
    content: str


@dataclass(frozen=True)
class SimulationResult:
    rating: str
    decision: str
    traces: tuple[Trace, ...]
    debates: tuple[DebateRound, ...]
    risks: tuple[RiskNote, ...]
    events: tuple[str, ...]


def parse_rating(text: str) -> str:
    for word in RATINGS:
        if re.search(rf"\b{word}\b", text):
            return word
    return "REVIEW"


def debate_ready(speech_count: int, max_rounds: int) -> bool:
    return speech_count >= 2 * max_rounds


def risk_ready(speech_count: int, max_rounds: int) -> bool:
    return speech_count >= 3 * max_rounds


def run_mock_graph(
    *,
    symbol: str,
    as_of_text: str,
    context: str,
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
    complete: Callable[[str], str] | None = None,
) -> SimulationResult:
    if max_debate_rounds < 1 or max_risk_rounds < 1:
        raise ValueError("辩论轮数至少为 1")
    reply = complete or _hold
    traces: list[Trace] = []
    events: list[str] = []
    for node in ANALYSTS:
        events.append(f"node_started:{node}")
        text = reply(f"{node}\n{context}")
        if context.startswith(("DATA_UNAVAILABLE", "NO_DATA_AVAILABLE")):
            text = f"{node}: {context.splitlines()[0]}"
        traces.append(Trace(node, text))
        events.append(f"report_ready:{node}")
    debates: list[DebateRound] = []
    speeches = 0
    round_no = 0
    while not debate_ready(speeches, max_debate_rounds):
        round_no += 1
        bull = reply(f"Bull Researcher\n{context}")
        bear = reply(f"Bear Researcher\n{context}")
        speeches += 2
        debates.append(DebateRound(round_no, bull, bear))
        traces.append(Trace("Bull Researcher", bull))
        traces.append(Trace("Bear Researcher", bear))
    plan = reply(f"Research Manager\n{context}")
    traces.append(Trace("Research Manager", plan))
    trader = reply(f"Trader\n{context}")
    traces.append(Trace("Trader", trader))
    risks: list[RiskNote] = []
    risk_speeches = 0
    speaker_index = 0
    while not risk_ready(risk_speeches, max_risk_rounds):
        speaker = RISK_SPEAKERS[speaker_index % 3]
        speaker_index += 1
        text = reply(f"{speaker}\n{context}")
        risks.append(RiskNote(speaker, text))
        traces.append(Trace(speaker, text))
        risk_speeches += 1
    decision = reply(f"Portfolio Manager\n{context}")
    traces.append(Trace("Portfolio Manager", decision))
    rating = parse_rating(decision)
    events.append(f"rating_ready:{rating}")
    return SimulationResult(
        rating, decision, tuple(traces), tuple(debates), tuple(risks), tuple(events)
    )


def _hold(prompt: str) -> str:
    if "DATA_UNAVAILABLE" in prompt or "NO_DATA_AVAILABLE" in prompt:
        first = prompt.splitlines()[-1]
        return first
    return "Portfolio Manager: Hold. 这是研究评级，不是下单指令。"
