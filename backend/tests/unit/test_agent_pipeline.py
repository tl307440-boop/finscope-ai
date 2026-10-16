"""规则引擎版多角色推演：不连库，用合成日线和桩数据桥。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.services.macro_rules import MacroAssessment, RuleScore
from app.trading_agents.analysts import (
    AnalystReport,
    Finding,
    macro_news_analyst,
    score_findings,
    technical_analyst,
    technical_metrics,
)
from app.trading_agents.bridge import Announcement, Bar, Citation, Subject
from app.trading_agents.memory import reflection_for, settle, verdict_for
from app.trading_agents.pipeline import (
    MAX_REVISIONS,
    MemoryNote,
    PipelineConfig,
    confidence_for,
    propose,
    rating_for,
    review,
    run_debate,
    run_pipeline,
    to_simulation_result,
)

CITE = Citation("A股日线", "样例", "bar:TEST:2026-01-01", "2026-01-01")
SUBJECT = Subject(uuid4(), "600000.SH", "样例股份", "CN", None, None, None)


def _bars(start: Decimal, step: Decimal, count: int, first: date = date(2025, 1, 2)) -> list[Bar]:
    rows: list[Bar] = []
    price = start
    day = first
    for _ in range(count):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append(Bar(day, price, price, price, price, Decimal(1000), None))
        price += step
        day += timedelta(days=1)
    return rows


def _macro_missing() -> MacroAssessment:
    empty = RuleScore("x", None, "缺数据", ())
    return MacroAssessment("insufficient_data", empty, empty, empty, "", "v")


class StubBridge:
    def __init__(
        self,
        bars: list[Bar],
        benchmark: list[Bar],
        as_of: date,
        announcements: list[Announcement] | None = None,
    ) -> None:
        self._bars = bars
        self._benchmark = benchmark
        self.as_of = as_of
        self._announcements = announcements or []

    def bars(self, subject: Subject, limit: int = 260) -> list[Bar]:
        return [bar for bar in self._bars if bar.trade_date <= self.as_of][-limit:]

    def benchmark(self, subject: Subject, limit: int = 260) -> tuple[str, list[Bar]]:
        return "000300.SH", [bar for bar in self._benchmark if bar.trade_date <= self.as_of]

    def announcements(self, subject: Subject, days: int = 90) -> list[Announcement]:
        return self._announcements

    def facts(self, subject: Subject) -> list[object]:
        return []

    def macro(self) -> MacroAssessment:
        return _macro_missing()

    def temperature(self, subject: Subject) -> None:
        return None

    def sector(self, subject: Subject) -> None:
        return None


def test_short_history_is_unavailable_not_neutral() -> None:
    report = technical_analyst(SUBJECT, _bars(Decimal(10), Decimal("0.1"), 30))
    assert report.coverage == "unavailable"
    assert report.score is None
    assert report.stance == "unavailable"


def test_uptrend_is_bullish_and_every_finding_is_cited() -> None:
    report = technical_analyst(SUBJECT, _bars(Decimal(10), Decimal("0.1"), 200))
    assert report.stance == "bullish"
    assert report.findings
    assert all(item.citations for item in report.findings)
    assert any("多头排列" in item.text for item in report.findings)


def test_score_is_damped_so_one_signal_cannot_reach_the_extremes() -> None:
    one = score_findings([Finding("a", 1, 1, (CITE,))])
    assert one is not None and Decimal(50) < one < Decimal(70)
    assert score_findings([]) == Decimal(50)


def test_rating_thresholds_and_missing_score() -> None:
    assert rating_for(None) == "review"
    assert rating_for(Decimal(70)) == "buy"
    assert rating_for(Decimal(57)) == "overweight"
    assert rating_for(Decimal(50)) == "hold"
    assert rating_for(Decimal(40)) == "underweight"
    assert rating_for(Decimal(20)) == "sell"


def _report(analyst: str, findings: list[Finding]) -> AnalystReport:
    score = score_findings(findings)
    return AnalystReport(analyst, "full", "neutral", score, "s", tuple(findings))


def test_debate_stops_when_both_sides_run_out_of_evidence() -> None:
    reports = [
        _report("technical", [Finding("多1", 1, 2, (CITE,)), Finding("空1", -1, 1, (CITE,))]),
        AnalystReport("fundamental", "unavailable", "unavailable", None, "无", ()),
    ]
    rounds, memo, tilt = run_debate(reports, 3)
    assert len(rounds) == 1
    assert rounds[0].converged
    assert "基本面分析师" in memo
    assert tilt == Decimal(1)


def test_uncited_claims_are_flagged_by_the_lead_researcher() -> None:
    reports = [_report("technical", [Finding("无出处的说法", 1, 1, ())])]
    rounds, _memo, tilt = run_debate(reports, 1)
    assert rounds[0].unsupported == ("无出处的说法",)
    assert tilt == Decimal(0)


def test_pipeline_without_any_data_gives_review_not_hold() -> None:
    bridge = StubBridge([], [], date(2026, 6, 1))
    output = run_pipeline(bridge, SUBJECT, PipelineConfig())  # type: ignore[arg-type]
    assert output.final_rating == "review"
    assert output.status == "insufficient"
    assert output.confidence == Decimal(0)
    assert all(not item.approved for item in output.critiques)


def test_single_dimension_bull_case_is_vetoed_and_revised_down() -> None:
    bars = _bars(Decimal(10), Decimal("0.1"), 200)
    bridge = StubBridge(bars, [], bars[-1].trade_date)
    output = run_pipeline(bridge, SUBJECT, PipelineConfig())  # type: ignore[arg-type]
    assert output.proposals[0].rating in {"buy", "overweight"}
    vetoes = [item for item in output.critiques if item.veto]
    assert vetoes and "单一维度" in vetoes[0].reason
    assert output.proposals[-1].revision >= 1
    assert output.final_rating == "hold"
    assert len(output.proposals) <= MAX_REVISIONS + 2


def test_regulatory_event_triggers_conservative_veto_on_a_bullish_proposal() -> None:
    bars = _bars(Decimal(10), Decimal("0.1"), 200)
    as_of = bars[-1].trade_date
    severe = Announcement(
        title="样例股份：关于收到监管问询函的公告",
        url="https://static.cninfo.com.cn/finalpage/x.PDF",
        source_name="cninfo",
        event_type="governance",
        published_at=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
    )
    reports = [
        technical_analyst(SUBJECT, bars),
        macro_news_analyst(SUBJECT, _macro_missing(), [severe], as_of),
    ]
    assert any(item.severe for item in reports[1].findings)
    proposal = propose(reports, Decimal(0), technical_metrics(bars), 30, rating="buy")
    critiques = review(proposal, reports, technical_metrics(bars), "moderate")
    conservative = next(item for item in critiques if item.profile == "conservative")
    assert conservative.veto
    assert "监管" in conservative.reason


def test_pipeline_result_maps_to_persisted_shape() -> None:
    bars = _bars(Decimal(10), Decimal("0.1"), 200)
    bridge = StubBridge(bars, _bars(Decimal(10), Decimal(0), 200), bars[-1].trade_date)
    output = run_pipeline(bridge, SUBJECT, PipelineConfig())  # type: ignore[arg-type]
    result = to_simulation_result(output)
    nodes = [item.node_name for item in result.traces]
    assert nodes[0] == "Init"
    assert nodes.index("Conservative Analyst") < nodes.index("Portfolio Manager")
    assert result.config["llm"] is False
    assert "不是下单指令" in result.decision
    analyst_traces = [
        item for item in result.traces if (item.structured or {}).get("kind") == "analyst"
    ]
    assert len(analyst_traces) == 4


def test_memory_settlement_never_reads_past_the_visible_date() -> None:
    bars = _bars(Decimal(10), Decimal("0.1"), 120)
    benchmark = _bars(Decimal(10), Decimal(0), 120)
    as_of = bars[10].trade_date
    early = as_of + timedelta(days=10)
    assert settle("buy", bars, benchmark, as_of, 30, visible_until=early) is None
    late = as_of + timedelta(days=45)
    result = settle("buy", bars, benchmark, as_of, 30, visible_until=late)
    assert result is not None
    assert result.exit_date <= as_of + timedelta(days=30)
    assert result.excess is not None and result.excess > 0
    assert result.verdict == "accurate"


def test_verdicts_follow_rating_direction() -> None:
    assert verdict_for("buy", Decimal("0.05")) == "accurate"
    assert verdict_for("buy", Decimal("-0.05")) == "divergent"
    assert verdict_for("sell", Decimal("-0.05")) == "accurate"
    assert verdict_for("hold", Decimal("0.01")) == "accurate"
    assert verdict_for("hold", Decimal("0.20")) == "divergent"
    assert verdict_for("review", Decimal("0.01")) is None


def test_reflection_names_the_missed_risk_after_a_wrong_buy() -> None:
    bars = _bars(Decimal(20), Decimal("-0.1"), 80)
    benchmark = _bars(Decimal(10), Decimal(0), 80)
    result = settle("buy", bars, benchmark, bars[5].trade_date, 30)
    assert result is not None and result.verdict == "divergent"
    text = reflection_for("buy", result, ("均线多头排列",), ("距高点回撤较深",))
    assert "低估了「距高点回撤较深」" in text


def test_divergent_memory_lowers_confidence_for_the_same_direction() -> None:
    reports = [_report("technical", [Finding("多", 1, 1, (CITE,))])]
    base = confidence_for(reports, "buy", [])
    miss = MemoryNote(date(2026, 1, 5), "buy", Decimal("-0.08"), "divergent", "看错")
    assert confidence_for(reports, "buy", [miss]) == base - Decimal(10)
    assert confidence_for(reports, "sell", [miss]) == confidence_for(reports, "sell", [])
