"""FinScope 规则引擎版多角色推演（规格书 §20.2 / §20.3，对齐 TradingAgents v0.6 流程）。

分析师 → 多空辩论（主导研究员核验引用、判断收敛）→ 交易员提案 → 风控三方（保守方可一票否决，
最多退回修正 2 次，仍否决则降为防御方案）→ 组合经理签署 → 决策记忆反思。

这是确定性规则，不调用语言模型；所有发言由已核验的 Finding 拼出，没有引用的论点会被裁判剔除。
接入语言模型后可以换成 TradingAgents 原版图（见 upstream.py），这里的口径不变。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.trading_agents.analysts import AnalystReport, Finding, TechnicalMetrics, run_analysts
from app.trading_agents.bridge import FinScopeDataBridge, Subject
from app.trading_agents.graph import DebateRound, RiskNote, SimulationResult, Trace

ENGINE = "finscope-rules"
ENGINE_VERSION = "rules-v1"
ENGINE_LABEL = "规则引擎（确定性规则，未调用语言模型）"
TIERS = ("sell", "underweight", "hold", "overweight", "buy")
DISPLAY = {
    "buy": "Buy",
    "overweight": "Overweight",
    "hold": "Hold",
    "underweight": "Underweight",
    "sell": "Sell",
    "review": "REVIEW",
}
TIER_LABEL = {
    "buy": "买入",
    "overweight": "增持",
    "hold": "持有观望",
    "underweight": "减持",
    "sell": "卖出",
    "review": "无评级",
}
WEIGHTS = {
    "fundamental": Decimal("0.30"),
    "technical": Decimal("0.30"),
    "macro_news": Decimal("0.20"),
    "sentiment": Decimal("0.20"),
}
THRESHOLDS = (
    (Decimal(64), "buy"),
    (Decimal(56), "overweight"),
    (Decimal("44.01"), "hold"),
    (Decimal(36), "underweight"),
)
ALLOCATION = {
    "buy": "5-10%",
    "overweight": "3-5%",
    "hold": "维持现有",
    "underweight": "0-2%",
    "sell": "0%",
    "review": "不配置",
}
MAX_REVISIONS = 2
DISCLAIMER = "仅用于研究与教学，不构成交易建议，也不会发送委托。"


@dataclass(frozen=True)
class PipelineConfig:
    max_debate_rounds: int = 2
    holding_days: int = 30
    risk_strictness: str = "moderate"
    use_memory: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "engine": ENGINE,
            "engine_version": ENGINE_VERSION,
            "engine_label": ENGINE_LABEL,
            "llm": False,
            "max_debate_rounds": self.max_debate_rounds,
            "holding_days": self.holding_days,
            "risk_strictness": self.risk_strictness,
            "use_memory": self.use_memory,
            "analyst_weights": {key: str(value) for key, value in WEIGHTS.items()},
            "rating_thresholds": {name: str(bound) for bound, name in THRESHOLDS},
            "max_revisions": MAX_REVISIONS,
            "orders": False,
        }


@dataclass(frozen=True)
class MemoryNote:
    """组合经理在本次决策前读到的同标的历史决策复盘（只含 as_of 之前已能结算的）。"""

    as_of: date
    rating: str
    excess_return: Decimal | None
    verdict: str
    reflection: str

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "rating": DISPLAY.get(self.rating.lower(), self.rating),
            "excess_return": None if self.excess_return is None else str(self.excess_return),
            "verdict": self.verdict,
            "reflection": self.reflection,
        }


@dataclass(frozen=True)
class Turn:
    speaker: str
    claims: tuple[Finding, ...]
    text: str
    rebuttal: str = ""


@dataclass(frozen=True)
class RoundRecord:
    round_no: int
    bull: Turn
    bear: Turn
    arbitration: str
    unsupported: tuple[str, ...]
    converged: bool


@dataclass(frozen=True)
class Proposal:
    rating: str
    composite: Decimal | None
    debate_tilt: Decimal
    allocation: str
    horizon_days: int
    stop_loss: str
    watch_list: tuple[str, ...]
    revision: int
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "rating": self.rating,
            "display_rating": DISPLAY[self.rating],
            "composite": None if self.composite is None else str(self.composite),
            "debate_tilt": str(self.debate_tilt),
            "allocation": self.allocation,
            "horizon_days": self.horizon_days,
            "stop_loss": self.stop_loss,
            "watch_list": list(self.watch_list),
            "revision": self.revision,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class Critique:
    profile: str
    approved: bool
    veto: bool
    reason: str
    scenario: str
    drawdown: Decimal | None
    mitigations: tuple[str, ...]
    revision: int


@dataclass
class PipelineOutput:
    subject: Subject
    as_of: date
    config: PipelineConfig
    analysts: list[AnalystReport]
    rounds: list[RoundRecord]
    memo: str
    proposals: list[Proposal]
    critiques: list[Critique]
    final_rating: str
    confidence: Decimal
    status: str
    drivers: list[Finding]
    risks: list[Finding]
    memory: list[MemoryNote]
    decision_text: str
    aborted: bool = False
    events: list[str] = field(default_factory=list)


def rating_for(score: Decimal | None) -> str:
    if score is None:
        return "review"
    for bound, name in THRESHOLDS:
        if score >= bound:
            return name
    return "sell"


def direction(rating: str) -> int:
    if rating in {"buy", "overweight"}:
        return 1
    if rating in {"underweight", "sell"}:
        return -1
    return 0


def composite_score(reports: Sequence[AnalystReport]) -> Decimal | None:
    usable = [item for item in reports if item.score is not None]
    if not usable:
        return None
    total = sum((WEIGHTS[item.analyst] for item in usable), Decimal(0))
    value = sum((WEIGHTS[item.analyst] * (item.score or Decimal(0)) for item in usable), Decimal(0))
    return (value / total).quantize(Decimal("0.1"))


# ---------------------------------------------------------------- 辩论


def run_debate(
    reports: Sequence[AnalystReport], max_rounds: int
) -> tuple[list[RoundRecord], str, Decimal]:
    pool_bull = _pool(reports, 1)
    pool_bear = _pool(reports, -1)
    silent = [item.role for item in reports if not item.available]
    records: list[RoundRecord] = []
    used_bull: list[Finding] = []
    used_bear: list[Finding] = []
    previous_bear: Turn | None = None
    for round_no in range(1, max_rounds + 1):
        take = 2 if round_no == 1 else 1
        bull_claims = tuple(pool_bull[:take])
        pool_bull = pool_bull[take:]
        rebut = ""
        if previous_bear is not None and previous_bear.claims:
            target = previous_bear.claims[0]
            counter = next(
                (item for item in bull_claims if _same_dimension(item, target, reports)), None
            )
            rebut = (
                f"针对空方「{_short(target.text)}」：{_short(counter.text)}，同一维度存在对冲证据。"
                if counter is not None
                else f"空方「{_short(target.text)}」暂无同维度反证，多方承认该风险。"
            )
        bull = Turn("bull_researcher", bull_claims, _speech("多方", bull_claims, round_no), rebut)
        bear_claims = tuple(pool_bear[:take])
        pool_bear = pool_bear[take:]
        bear_rebut = ""
        if bull_claims:
            target = bull_claims[0]
            counter = next(
                (item for item in bear_claims if _same_dimension(item, target, reports)), None
            )
            bear_rebut = (
                f"针对多方「{_short(target.text)}」：{_short(counter.text)}，该论据并不稳固。"
                if counter is not None
                else f"多方「{_short(target.text)}」暂无同维度反证，空方保留意见。"
            )
        bear = Turn(
            "bear_researcher", bear_claims, _speech("空方", bear_claims, round_no), bear_rebut
        )
        unsupported = tuple(
            item.text for item in (*bull_claims, *bear_claims) if not item.citations
        )
        used_bull.extend(item for item in bull_claims if item.citations)
        used_bear.extend(item for item in bear_claims if item.citations)
        converged = not pool_bull and not pool_bear
        no_new = not bull_claims and not bear_claims
        arbitration = _arbitration(
            round_no, bull_claims, bear_claims, unsupported, silent, converged
        )
        records.append(
            RoundRecord(round_no, bull, bear, arbitration, unsupported, converged or no_new)
        )
        previous_bear = bear
        if converged or no_new:
            break
    bull_weight = sum(item.weight for item in used_bull)
    bear_weight = sum(item.weight for item in used_bear)
    tilt = Decimal(max(-5, min(5, bull_weight - bear_weight)))
    memo = (
        f"多空辩论共 {len(records)} 轮：多方采信 {len(used_bull)} 条论据（权重 {bull_weight}），"
        f"空方采信 {len(used_bear)} 条（权重 {bear_weight}）。"
        + (f"{'、'.join(silent)}无数据，双方均不得就该维度立论。" if silent else "")
        + ("多方论据更充分。" if bull_weight > bear_weight else "")
        + ("空方论据更充分。" if bear_weight > bull_weight else "")
        + ("双方势均力敌。" if bull_weight == bear_weight else "")
    )
    return records, memo, tilt


def _pool(reports: Sequence[AnalystReport], polarity: int) -> list[Finding]:
    found = [item for report in reports for item in report.findings if item.polarity == polarity]
    return sorted(found, key=lambda item: (-item.weight, -int(item.severe)))


def _same_dimension(a: Finding, b: Finding, reports: Sequence[AnalystReport]) -> bool:
    return any(a in report.findings and b in report.findings for report in reports)


def _speech(side: str, claims: Sequence[Finding], round_no: int) -> str:
    if not claims:
        return f"第 {round_no} 轮{side}没有新的有据论点。"
    body = "；".join(item.text for item in claims)
    return f"第 {round_no} 轮{side}立论：{body}。"


def _arbitration(
    round_no: int,
    bull: Sequence[Finding],
    bear: Sequence[Finding],
    unsupported: Sequence[str],
    silent: Sequence[str],
    converged: bool,
) -> str:
    verified = sum(1 for item in (*bull, *bear) if item.citations)
    parts = [f"第 {round_no} 轮核验：{verified} 条论点附带可查验引用"]
    if unsupported:
        parts.append(f"{len(unsupported)} 条缺少引用，已剔除")
    if silent:
        parts.append(f"{'、'.join(silent)}无数据，不计入")
    parts.append("双方已无新证据，辩论收敛" if converged else "仍有未出示的证据，继续下一轮")
    return "；".join(parts) + "。"


def _short(text: str) -> str:
    return text if len(text) <= 36 else text[:35] + "…"


# ---------------------------------------------------------------- 交易员与风控


def propose(
    reports: Sequence[AnalystReport],
    tilt: Decimal,
    metrics: TechnicalMetrics,
    holding_days: int,
    *,
    rating: str | None = None,
    revision: int = 0,
    reason: str = "",
) -> Proposal:
    composite = composite_score(reports)
    adjusted = None if composite is None else composite + tilt
    chosen = rating or rating_for(adjusted)
    stop = "数据不足，无法给出失效条件"
    if metrics.ma60 is not None and metrics.close is not None:
        stop = f"收盘跌破 MA60（{metrics.ma60}）即复核"
        if metrics.vol20_daily is not None:
            band = Decimal(str(round(metrics.vol20_daily * (20**0.5) * 1.5 * 100, 1)))
            stop = (
                f"收盘跌破 MA60（{metrics.ma60}），或较推演日收盘 {metrics.close} "
                f"回撤超过 {band}%（约 1.5 倍 20 日波动）即复核"
            )
    elif any(item.analyst == "fundamental" and item.available for item in reports):
        stop = "下一份 10-Q/10-K 营收或现金流指标转负即复核"
    watch = tuple(
        item.text for report in reports for item in report.findings if item.polarity != 0
    )[:4]
    rationale = (
        "各维度都没有可用数据，交易员不给评级"
        if composite is None
        else f"加权综合分 {composite}，辩论倾斜 {tilt:+}，调整后 {adjusted}，对应 {DISPLAY[chosen]}"
    )
    if reason:
        rationale = f"第 {revision} 次修正：{reason}；" + rationale
    return Proposal(
        rating=chosen,
        composite=composite,
        debate_tilt=tilt,
        allocation=ALLOCATION[chosen],
        horizon_days=holding_days,
        stop_loss=stop,
        watch_list=watch,
        revision=revision,
        rationale=rationale,
    )


def review(
    proposal: Proposal,
    reports: Sequence[AnalystReport],
    metrics: TechnicalMetrics,
    strictness: str,
) -> list[Critique]:
    if proposal.rating == "review":
        return [
            Critique(profile, False, False, "交易员没有给出评级，无提案可审", "—", None, (), 0)
            for profile in ("aggressive", "neutral", "conservative")
        ]
    available = [item for item in reports if item.available]
    severe = [
        item for report in reports for item in report.findings if item.severe and item.polarity < 0
    ]
    worst = None if metrics.worst_20d is None else Decimal(str(round(metrics.worst_20d, 4)))
    vol = metrics.vol60_annual
    swing = None
    if metrics.vol20_daily is not None:
        swing = Decimal(str(round(-metrics.vol20_daily * (20**0.5), 4)))
    bullish = direction(proposal.rating) > 0
    critiques: list[Critique] = []
    aggressive_ok = proposal.rating != "review"
    critiques.append(
        Critique(
            profile="aggressive",
            approved=aggressive_ok,
            veto=False,
            reason="上行弹性与催化剂可跟踪，接受提案" if aggressive_ok else "没有评级可审",
            scenario="一倍 20 日波动下行",
            drawdown=swing,
            mitigations=("按催化剂兑现节奏分批建立研究仓位",) if bullish else (),
            revision=proposal.revision,
        )
    )
    mitigations: list[str] = []
    neutral_ok = True
    vol_cap = 0.35 if strictness == "conservative" else 0.45
    if vol is not None and vol > vol_cap and bullish:
        neutral_ok = False
        mitigations.append(
            f"60 日年化波动 {vol * 100:.1f}% 高于 {vol_cap * 100:.0f}%，虚拟仓位上限减半"
        )
    if len(available) <= 1 and proposal.rating != "review":
        mitigations.append("只有一个维度有数据，结论置信度应下调")
    critiques.append(
        Critique(
            profile="neutral",
            approved=neutral_ok,
            veto=False,
            reason="收益与波动大体平衡" if neutral_ok else "波动偏高，要求缩小仓位",
            scenario="60 日年化波动",
            drawdown=None if vol is None else Decimal(str(round(-vol / (252**0.5) * (20**0.5), 4))),
            mitigations=tuple(mitigations),
            revision=proposal.revision,
        )
    )
    veto_reasons: list[str] = []
    if bullish:
        limit = Decimal("-0.15") if strictness == "conservative" else Decimal("-0.20")
        below_ma60 = (
            metrics.close is not None and metrics.ma60 is not None and metrics.close < metrics.ma60
        )
        if worst is not None and worst < limit and below_ma60:
            veto_reasons.append(f"近一年最差 20 日回撤 {worst * 100:.1f}% 且价格仍在 MA60 下方")
        if severe:
            veto_reasons.append(f"存在未消化的监管/问询类事件：{_short(severe[0].text)}")
        if len(available) <= 1:
            veto_reasons.append("看多结论只有单一维度支撑")
    veto = bool(veto_reasons)
    critiques.append(
        Critique(
            profile="conservative",
            approved=not veto,
            veto=veto,
            reason="；".join(veto_reasons) if veto else "未触及硬性风险阈值",
            scenario="近一年最差 20 日回撤重演",
            drawdown=worst,
            mitigations=(f"止损观察：{proposal.stop_loss}",) if proposal.rating != "review" else (),
            revision=proposal.revision,
        )
    )
    return critiques


def downgrade(rating: str) -> str:
    if rating not in TIERS:
        return rating
    index = TIERS.index(rating)
    return TIERS[max(0, index - 1)] if index > TIERS.index("hold") else rating


# ---------------------------------------------------------------- 组合经理


def confidence_for(
    reports: Sequence[AnalystReport], rating: str, memory: Sequence[MemoryNote]
) -> Decimal:
    if rating == "review":
        return Decimal(0)
    usable = [item for item in reports if item.available]
    coverage = Decimal(len(usable)) / Decimal(len(reports))
    want = direction(rating)
    agree = Decimal(0)
    for item in usable:
        side = {"bullish": 1, "bearish": -1}.get(item.stance, 0)
        if side == want:
            agree += 1
        elif side == 0 or want == 0:
            agree += Decimal("0.5")
    agreement = agree / Decimal(len(usable)) if usable else Decimal(0)
    value = Decimal(100) * (coverage * Decimal("0.5") + agreement * Decimal("0.5"))
    misses = [
        note for note in memory if note.verdict == "divergent" and direction(note.rating) == want
    ]
    if misses:
        value -= Decimal(10)
    return max(Decimal(0), min(Decimal(100), value)).quantize(Decimal("0.1"))


def run_pipeline(
    bridge: FinScopeDataBridge,
    subject: Subject,
    config: PipelineConfig,
    memory: Sequence[MemoryNote] = (),
) -> PipelineOutput:
    events = ["node_started:init"]
    reports, metrics, _announcements = run_analysts(bridge, subject)
    events.extend(f"agent_signal:{item.analyst}" for item in reports)
    rounds, memo, tilt = run_debate(reports, config.max_debate_rounds)
    events.extend(f"debate_turn:{item.round_no}" for item in rounds)
    proposals = [propose(reports, tilt, metrics, config.holding_days)]
    critiques: list[Critique] = []
    aborted = False
    while True:
        current = proposals[-1]
        batch = review(current, reports, metrics, config.risk_strictness)
        critiques.extend(batch)
        events.append(f"risk_verdict:{current.revision}")
        vetoed = next((item for item in batch if item.veto), None)
        if vetoed is None:
            break
        if current.revision >= MAX_REVISIONS:
            aborted = True
            proposals.append(
                propose(
                    reports,
                    tilt,
                    metrics,
                    config.holding_days,
                    rating="hold",
                    revision=current.revision + 1,
                    reason=f"保守风控连续否决，强制转为防御方案（{vetoed.reason}）",
                )
            )
            break
        proposals.append(
            propose(
                reports,
                tilt,
                metrics,
                config.holding_days,
                rating=downgrade(current.rating),
                revision=current.revision + 1,
                reason=f"保守风控否决：{vetoed.reason}",
            )
        )
    final = proposals[-1]
    confidence = confidence_for(reports, final.rating, memory)
    verified = [item for report in reports for item in report.findings if item.citations]
    drivers = sorted(
        (item for item in verified if item.polarity > 0), key=lambda item: -item.weight
    )[:3]
    risks = sorted((item for item in verified if item.polarity < 0), key=lambda item: -item.weight)[
        :3
    ]
    status = "insufficient" if final.rating == "review" else "succeeded"
    text = _decision_text(subject, bridge.as_of, final, confidence, drivers, risks, memory, aborted)
    events.append(f"rating_ready:{DISPLAY[final.rating]}")
    return PipelineOutput(
        subject=subject,
        as_of=bridge.as_of,
        config=config,
        analysts=reports,
        rounds=rounds,
        memo=memo,
        proposals=proposals,
        critiques=critiques,
        final_rating=final.rating,
        confidence=confidence,
        status=status,
        drivers=drivers,
        risks=risks,
        memory=list(memory),
        decision_text=text,
        aborted=aborted,
        events=events,
    )


def _decision_text(
    subject: Subject,
    as_of: date,
    final: Proposal,
    confidence: Decimal,
    drivers: Sequence[Finding],
    risks: Sequence[Finding],
    memory: Sequence[MemoryNote],
    aborted: bool,
) -> str:
    if final.rating == "review":
        return (
            f"{subject.name}（{subject.symbol}）截至 {as_of}：各维度都没有可用数据，不给评级。"
            "这是研究记录，不是下单指令。"
        )
    lines = [
        f"研究评级：{DISPLAY[final.rating]}（{TIER_LABEL[final.rating]}），置信度 {confidence}。",
        f"{final.rationale}。",
        f"关注周期 {final.horizon_days} 天，参考虚拟配置 {final.allocation}；{final.stop_loss}。",
    ]
    if drivers:
        lines.append("核心上行动能：" + "；".join(item.text for item in drivers) + "。")
    if risks:
        lines.append("核心风险敞口：" + "；".join(item.text for item in risks) + "。")
    if aborted:
        lines.append("保守风控连续否决，已强制转为防御方案。")
    if memory:
        lines.append("决策记忆：" + "；".join(note.reflection for note in memory[:3]) + "。")
    lines.append("这是研究评级，不是下单指令。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 落库适配


def to_simulation_result(output: PipelineOutput) -> SimulationResult:
    traces: list[Trace] = [
        Trace(
            "Init",
            f"校验标的 {output.subject.name}（{output.subject.symbol}，"
            f"{output.subject.market}）与时间基准 "
            f"推演日 {output.as_of}，所有工具只读取当日及以前已公开的数据。",
            role="数据桥",
            structured={"kind": "init", "subject": _subject_dict(output.subject)},
        )
    ]
    for report in output.analysts:
        traces.append(
            Trace(
                report.role,
                report.summary,
                role=report.analyst,
                structured={"kind": "analyst", **report.as_dict()},
                citations=tuple(
                    cite.as_dict() for item in report.findings for cite in item.citations
                ),
            )
        )
    for record in output.rounds:
        traces.append(
            Trace(
                "Bull Researcher",
                record.bull.text + (f"\n{record.bull.rebuttal}" if record.bull.rebuttal else ""),
                role="bull_researcher",
                structured={"kind": "debate", "round": record.round_no},
            )
        )
        traces.append(
            Trace(
                "Bear Researcher",
                record.bear.text + (f"\n{record.bear.rebuttal}" if record.bear.rebuttal else ""),
                role="bear_researcher",
                structured={"kind": "debate", "round": record.round_no},
            )
        )
        traces.append(
            Trace(
                "Lead Researcher",
                record.arbitration,
                role="lead_researcher",
                structured={"kind": "arbitration", "round": record.round_no},
            )
        )
    traces.append(
        Trace("Research Manager", output.memo, role="lead_researcher", structured={"kind": "memo"})
    )
    critiques_by_revision: dict[int, list[Critique]] = {}
    for item in output.critiques:
        critiques_by_revision.setdefault(item.revision, []).append(item)
    for proposal in output.proposals:
        traces.append(
            Trace(
                "Trader",
                proposal.rationale,
                role="trader",
                structured={"kind": "proposal", **proposal.as_dict()},
            )
        )
        for critique in critiques_by_revision.get(proposal.revision, []):
            traces.append(
                Trace(
                    _RISK_NODE[critique.profile],
                    critique.reason,
                    role=critique.profile,
                    structured={
                        "kind": "risk",
                        "revision": critique.revision,
                        "veto": critique.veto,
                    },
                )
            )
    traces.append(
        Trace(
            "Portfolio Manager",
            output.decision_text,
            role="portfolio_manager",
            structured={
                "kind": "decision",
                "memory": [note.as_dict() for note in output.memory],
                "aborted": output.aborted,
            },
        )
    )
    debates = tuple(
        DebateRound(
            record.round_no,
            record.bull.text + (f"\n{record.bull.rebuttal}" if record.bull.rebuttal else ""),
            record.bear.text + (f"\n{record.bear.rebuttal}" if record.bear.rebuttal else ""),
            bull_citations=tuple(
                c.as_dict() for item in record.bull.claims for c in item.citations
            ),
            bear_citations=tuple(
                c.as_dict() for item in record.bear.claims for c in item.citations
            ),
            arbitration=record.arbitration,
            unsupported=record.unsupported,
            converged=record.converged,
        )
        for record in output.rounds
    )
    risks = tuple(
        RiskNote(
            _RISK_NODE[item.profile],
            item.reason,
            approved=item.approved,
            veto_reason=item.reason if item.veto else None,
            scenario=item.scenario,
            drawdown=item.drawdown,
            mitigations=item.mitigations,
            revision=item.revision,
        )
        for item in output.critiques
    )
    final = output.proposals[-1]
    detail: dict[str, object] = {
        "confidence": str(output.confidence),
        "horizon": f"{final.horizon_days}d",
        "allocation": final.allocation,
        "stop_loss": final.stop_loss,
        "drivers": [item.as_dict() for item in output.drivers],
        "risks": [item.as_dict() for item in output.risks],
    }
    return SimulationResult(
        DISPLAY[output.final_rating],
        output.decision_text,
        tuple(traces),
        debates,
        risks,
        tuple(output.events),
        status=output.status,
        config=output.config.as_dict(),
        detail=detail,
    )


_RISK_NODE = {
    "aggressive": "Aggressive Analyst",
    "neutral": "Neutral Analyst",
    "conservative": "Conservative Analyst",
}


def _subject_dict(subject: Subject) -> dict[str, object]:
    return {
        "symbol": subject.symbol,
        "name": subject.name,
        "market": subject.market,
        "industry": subject.industry_name,
    }
