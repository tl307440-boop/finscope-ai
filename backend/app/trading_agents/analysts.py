"""分析师团队（规格书 §20.2 Tier 1）：确定性规则，不调用语言模型。

每条 Finding 都带引用；拿不到数据的维度标 unavailable，不出分，也不写结论。
分数口径：50 为中性，按「利多权重 - 利空权重」占总权重的比例向两侧展开，范围 10~90。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from app.repositories.filings import FactRecord
from app.services.macro_rules import MacroAssessment
from app.services.market_temperature import TemperatureResult
from app.trading_agents.bridge import (
    SHANGHAI as _SH,
)
from app.trading_agents.bridge import (
    Announcement,
    Bar,
    Citation,
    FinScopeDataBridge,
    Subject,
)

ROLE_NAMES = {
    "fundamental": "基本面分析师",
    "technical": "技术面分析师",
    "macro_news": "宏观与资讯分析师",
    "sentiment": "市场情绪分析师",
}
MIN_TECH_BARS = 60
SCORE_DAMPING = 2
REVENUE_CONCEPTS = (
    "us-gaap:Revenues",
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
)
POSITIVE_WORDS = ("回购", "增持", "预增", "扭亏", "中标", "分红", "派息")
NEGATIVE_WORDS = (
    "减持",
    "问询",
    "监管",
    "处罚",
    "立案",
    "诉讼",
    "风险提示",
    "预减",
    "预亏",
    "亏损",
)
SEVERE_WORDS = ("问询", "监管", "处罚", "立案")


@dataclass(frozen=True)
class Finding:
    text: str
    polarity: int
    weight: int
    citations: tuple[Citation, ...]
    severe: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "polarity": self.polarity,
            "weight": self.weight,
            "severe": self.severe,
            "citations": [item.as_dict() for item in self.citations],
        }


@dataclass(frozen=True)
class AnalystReport:
    analyst: str
    coverage: str
    stance: str
    score: Decimal | None
    summary: str
    findings: tuple[Finding, ...]
    metrics: dict[str, str] = field(default_factory=dict)
    gaps: tuple[str, ...] = ()

    @property
    def role(self) -> str:
        return ROLE_NAMES[self.analyst]

    @property
    def available(self) -> bool:
        return self.coverage != "unavailable"

    def as_dict(self) -> dict[str, object]:
        return {
            "analyst": self.analyst,
            "role": self.role,
            "coverage": self.coverage,
            "stance": self.stance,
            "score": None if self.score is None else str(self.score),
            "summary": self.summary,
            "findings": [item.as_dict() for item in self.findings],
            "metrics": dict(self.metrics),
            "gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class TechnicalMetrics:
    """风控委员会也要用这些数，单独算一次。"""

    close: Decimal | None = None
    ma20: Decimal | None = None
    ma60: Decimal | None = None
    vol20_daily: float | None = None
    vol60_annual: float | None = None
    worst_20d: float | None = None
    drawdown_from_high: float | None = None
    last_date: date | None = None


def score_findings(findings: Sequence[Finding]) -> Decimal | None:
    """中性信号也计入分母，再加 SCORE_DAMPING 的先验权重，避免一两条信号就打到极值。"""

    if not findings:
        return Decimal(50)
    total = sum(item.weight for item in findings) + SCORE_DAMPING
    net = sum(item.weight * item.polarity for item in findings)
    raw = Decimal(50) + Decimal(40) * Decimal(net) / Decimal(total)
    return raw.quantize(Decimal("0.1"))


def stance_for(score: Decimal | None) -> str:
    if score is None:
        return "unavailable"
    if score >= 58:
        return "bullish"
    if score <= 42:
        return "bearish"
    return "neutral"


def _report(
    analyst: str,
    findings: list[Finding],
    *,
    coverage: str,
    summary: str,
    metrics: dict[str, str] | None = None,
    gaps: Sequence[str] = (),
) -> AnalystReport:
    score = None if coverage == "unavailable" else score_findings(findings)
    return AnalystReport(
        analyst=analyst,
        coverage=coverage,
        stance=stance_for(score),
        score=score,
        summary=summary,
        findings=tuple(findings),
        metrics=metrics or {},
        gaps=tuple(gaps),
    )


def _unavailable(analyst: str, reason: str) -> AnalystReport:
    return _report(analyst, [], coverage="unavailable", summary=reason, gaps=(reason,))


# ---------------------------------------------------------------- 技术面


def technical_metrics(bars: Sequence[Bar]) -> TechnicalMetrics:
    if len(bars) < MIN_TECH_BARS:
        return TechnicalMetrics(last_date=bars[-1].trade_date if bars else None)
    closes = [float(bar.close) for bar in bars]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    worst = None
    if len(closes) > 20:
        worst = min(closes[i] / max(closes[i - 20 : i + 1]) - 1 for i in range(20, len(closes)))
    high = max(closes[-120:])
    vol60 = _stdev(returns[-60:])
    return TechnicalMetrics(
        close=bars[-1].close,
        ma20=_ma(bars, 20),
        ma60=_ma(bars, 60),
        vol20_daily=_stdev(returns[-20:]),
        vol60_annual=None if vol60 is None else vol60 * math.sqrt(252),
        worst_20d=worst,
        drawdown_from_high=closes[-1] / high - 1 if high > 0 else None,
        last_date=bars[-1].trade_date,
    )


def technical_analyst(subject: Subject, bars: Sequence[Bar]) -> AnalystReport:
    if len(bars) < MIN_TECH_BARS:
        reason = (
            f"截至当日只有 {len(bars)} 根已入库日线，少于 {MIN_TECH_BARS} 根，不做趋势判断"
            if bars
            else (
                "美股日线尚未接入（NOT_IMPLEMENTED），技术面不出分"
                if subject.market == "US"
                else "截至当日没有已入库的日线"
            )
        )
        return _unavailable("technical", reason)
    last = bars[-1]
    cite_last = _bar_cite(subject, last)
    metrics = technical_metrics(bars)
    findings: list[Finding] = []
    ma5, ma20, ma60 = _ma(bars, 5), metrics.ma20, metrics.ma60
    if ma5 and ma20 and ma60:
        close = last.close
        if close > ma20 > ma60 and ma5 > ma20:
            findings.append(
                Finding(
                    f"均线多头排列：收盘 {close} > MA20 {ma20} > MA60 {ma60}", 1, 2, (cite_last,)
                )
            )
        elif close < ma20 < ma60 and ma5 < ma20:
            findings.append(
                Finding(
                    f"均线空头排列：收盘 {close} < MA20 {ma20} < MA60 {ma60}", -1, 2, (cite_last,)
                )
            )
        else:
            findings.append(
                Finding(
                    f"均线缠绕：收盘 {close}，MA20 {ma20}，MA60 {ma60}，趋势未定",
                    0,
                    1,
                    (cite_last,),
                )
            )
    if len(bars) > 20:
        base = bars[-21]
        change = last.close / base.close - 1
        cites = (_bar_cite(subject, base), cite_last)
        text = f"近 20 个交易日涨跌 {_pct(change)}"
        if change > Decimal("0.08"):
            findings.append(Finding(text + "，短线动能偏强", 1, 1, cites))
        elif change < Decimal("-0.08"):
            findings.append(Finding(text + "，短线动能偏弱", -1, 1, cites))
    rsi = _rsi([float(bar.close) for bar in bars], 14)
    if rsi is not None:
        if rsi >= 70:
            findings.append(
                Finding(f"RSI14 = {rsi:.1f}，处于超买区，追高性价比下降", -1, 1, (cite_last,))
            )
        elif rsi <= 30:
            findings.append(
                Finding(f"RSI14 = {rsi:.1f}，处于超卖区，存在技术性修复空间", 1, 1, (cite_last,))
            )
    hist_now, hist_prev = _macd_hist([float(bar.close) for bar in bars])
    if hist_now is not None and hist_prev is not None:
        if hist_now > 0 and hist_now >= hist_prev:
            findings.append(
                Finding(f"MACD 柱为正且放大（{hist_now:.3f}），上行动能延续", 1, 1, (cite_last,))
            )
        elif hist_now < 0 and hist_now <= hist_prev:
            findings.append(
                Finding(f"MACD 柱为负且放大（{hist_now:.3f}），下行动能延续", -1, 1, (cite_last,))
            )
    if metrics.drawdown_from_high is not None and metrics.drawdown_from_high < -0.2:
        findings.append(
            Finding(
                f"距近 120 日高点回撤 {metrics.drawdown_from_high * 100:.1f}%，处于深度调整",
                -1,
                1,
                (cite_last,),
            )
        )
    volume_ratio = _volume_ratio(bars)
    if volume_ratio is not None and volume_ratio >= 1.5 and len(bars) > 5:
        move = last.close / bars[-6].close - 1
        polarity = 1 if move > 0 else -1
        findings.append(
            Finding(
                f"近 5 日成交量为 20 日均量的 {volume_ratio:.2f} 倍，"
                + ("放量上涨" if polarity > 0 else "放量下跌"),
                polarity,
                1,
                (cite_last,),
            )
        )
    info = {
        "close": str(last.close),
        "ma20": _plain(ma20),
        "ma60": _plain(ma60),
        "rsi14": "" if rsi is None else f"{rsi:.1f}",
        "vol60_annual": _ratio(metrics.vol60_annual),
        "worst_20d": _ratio(metrics.worst_20d),
        "bars": str(len(bars)),
    }
    coverage = "full" if len(bars) >= 120 else "partial"
    summary = _summary(
        findings, f"{len(bars)} 根日线（截至 {last.trade_date.isoformat()}，未复权）"
    )
    return _report("technical", findings, coverage=coverage, summary=summary, metrics=info)


# ---------------------------------------------------------------- 基本面


def fundamental_analyst(subject: Subject, facts: Sequence[FactRecord]) -> AnalystReport:
    if subject.market == "CN":
        return _unavailable(
            "fundamental", "A 股财务报表尚未接入（NOT_IMPLEMENTED），基本面分析师不出分"
        )
    if not facts:
        return _unavailable("fundamental", "截至推演日没有已提交的 SEC 财务事实")
    findings: list[Finding] = []
    metrics: dict[str, str] = {}
    revenue = _annual(facts, REVENUE_CONCEPTS)
    profit = _annual(facts, ("us-gaap:NetIncomeLoss",))
    for label, series, key in (
        ("营业收入", revenue, "revenue_yoy"),
        ("净利润", profit, "net_income_yoy"),
    ):
        if len(series) < 2 or series[-2].value == 0:
            continue
        now, prior = series[-1], series[-2]
        yoy = now.value / abs(prior.value) - 1 if prior.value > 0 else None
        if yoy is None:
            continue
        metrics[key] = _plain(yoy.quantize(Decimal("0.0001")))
        text = f"{label}（FY 截至 {now.period_end}）同比 {_pct(yoy)}"
        cites = (_fact_cite(label, now), _fact_cite(label, prior))
        if yoy >= Decimal("0.10"):
            findings.append(Finding(text + "，增长稳健", 1, 2, cites))
        elif yoy < 0:
            findings.append(Finding(text + "，出现下滑", -1, 2, cites))
        else:
            findings.append(Finding(text, 0, 1, cites))
    gross = _annual(facts, ("us-gaap:GrossProfit",))
    margins = _margins(gross, revenue)
    if len(margins) >= 2:
        (now_end, now_margin, now_fact), (_end, prior_margin, prior_fact) = margins[-1], margins[-2]
        delta = now_margin - prior_margin
        metrics["gross_margin"] = _plain(now_margin.quantize(Decimal("0.0001")))
        text = f"毛利率 {now_margin * 100:.2f}%（FY {now_end}），较上年 {_pp(delta)}"
        cites = (_fact_cite("毛利", now_fact), _fact_cite("毛利", prior_fact))
        if delta >= Decimal("0.01"):
            findings.append(Finding(text + "，盈利能力改善", 1, 1, cites))
        elif delta <= Decimal("-0.02"):
            findings.append(Finding(text + "，盈利能力承压", -1, 1, cites))
    cfo = _annual(facts, ("us-gaap:NetCashProvidedByUsedInOperatingActivities",))
    if cfo and profit and cfo[-1].period_end == profit[-1].period_end and profit[-1].value > 0:
        cover = cfo[-1].value / profit[-1].value
        metrics["cfo_to_net_income"] = _plain(cover.quantize(Decimal("0.01")))
        cites = (_fact_cite("经营现金流", cfo[-1]), _fact_cite("净利润", profit[-1]))
        if cover >= 1:
            findings.append(
                Finding(f"经营现金流 / 净利润 = {cover:.2f}，利润有现金支撑", 1, 1, cites)
            )
        elif cover < Decimal("0.8"):
            findings.append(
                Finding(
                    f"经营现金流 / 净利润 = {cover:.2f}，低于 0.8，利润含金量存疑",
                    -1,
                    2,
                    cites,
                    True,
                )
            )
    liabilities = _latest_instant(facts, "us-gaap:Liabilities")
    assets = _latest_instant(facts, "us-gaap:Assets")
    if liabilities and assets and assets.period_end == liabilities.period_end and assets.value > 0:
        ratio = liabilities.value / assets.value
        metrics["debt_ratio"] = _plain(ratio.quantize(Decimal("0.0001")))
        cites = (_fact_cite("总负债", liabilities), _fact_cite("总资产", assets))
        if ratio > Decimal("0.7"):
            findings.append(
                Finding(f"资产负债率 {ratio * 100:.2f}%，高于 70% 警戒线", -1, 1, cites)
            )
        else:
            findings.append(Finding(f"资产负债率 {ratio * 100:.2f}%", 0, 1, cites))
    receivable = _instants(facts, "us-gaap:AccountsReceivableNetCurrent")
    if len(receivable) >= 2 and len(revenue) >= 2 and revenue[-2].value > 0:
        ar_now, ar_prior = receivable[-1], _year_before(receivable, receivable[-1].period_end)
        if ar_prior is not None and ar_prior.value > 0:
            ar_yoy = ar_now.value / ar_prior.value - 1
            rev_yoy = revenue[-1].value / revenue[-2].value - 1
            if ar_yoy - rev_yoy > Decimal("0.15"):
                findings.append(
                    Finding(
                        f"应收账款同比 {_pct(ar_yoy)}，"
                        f"快于营收同比 {_pct(rev_yoy)} 超过 15 个百分点",
                        -1,
                        2,
                        (_fact_cite("应收账款", ar_now), _fact_cite("应收账款", ar_prior)),
                    )
                )
    if not findings:
        return _unavailable("fundamental", "已提交的 SEC 事实里凑不齐可比的年度口径")
    latest_filed = max(item.filed for item in facts)
    summary = _summary(findings, f"SEC EDGAR as-filed 事实（最近提交 {latest_filed.isoformat()}）")
    return _report("fundamental", findings, coverage="full", summary=summary, metrics=metrics)


# ---------------------------------------------------------------- 宏观与资讯


def macro_news_analyst(
    subject: Subject,
    macro: MacroAssessment,
    announcements: Sequence[Announcement],
    as_of: date,
) -> AnalystReport:
    findings: list[Finding] = []
    gaps: list[str] = []
    metrics: dict[str, str] = {"regime": macro.regime_code}
    scope = "美国宏观" if subject.market == "US" else "海外宏观（美国，外部环境）"
    if macro.regime_code == "insufficient_data":
        gaps.append("当日没有已发布的 FRED vintage，宏观维度不参与")
    else:
        liquidity = macro.liquidity
        growth = macro.growth
        cites = tuple(
            Citation("FRED", text, f"fred:{score.code}", as_of.isoformat())
            for score in (growth, liquidity)
            for text in score.evidence[:1]
        )
        if liquidity.value is not None:
            if liquidity.value < 50:
                findings.append(
                    Finding(f"{scope}：流动性收紧（评分 {liquidity.value}）", -1, 1, cites)
                )
            else:
                findings.append(
                    Finding(f"{scope}：流动性宽松（评分 {liquidity.value}）", 1, 1, cites)
                )
        if growth.value is not None:
            if growth.value >= 50:
                findings.append(
                    Finding(f"{scope}：增长动能回升（评分 {growth.value}）", 1, 1, cites)
                )
            else:
                findings.append(
                    Finding(f"{scope}：增长动能放缓（评分 {growth.value}）", -1, 1, cites)
                )
    material = 0
    for item in announcements[:30]:
        polarity, severe = _announcement_polarity(item.title)
        if polarity == 0:
            continue
        material += 1
        if material > 6:
            break
        day = item.published_at.astimezone(_SH).date().isoformat()
        source = "巨潮资讯" if item.source_name == "cninfo" else "SEC EDGAR"
        cite = Citation(source, item.title[:80], item.url, day)
        findings.append(
            Finding(
                f"{day} 公告：{_trim_title(item.title, subject.name)}",
                polarity,
                2 if severe else 1,
                (cite,),
                severe,
            )
        )
    metrics["announcements_90d"] = str(len(announcements))
    if announcements and material == 0:
        latest = announcements[0]
        day = latest.published_at.astimezone(_SH).date().isoformat()
        source = "巨潮资讯" if latest.source_name == "cninfo" else "SEC EDGAR"
        findings.append(
            Finding(
                f"近 90 天 {len(announcements)} 条公告，均为常规披露，没有识别出利多/利空关键词",
                0,
                1,
                (Citation(source, latest.title[:80], latest.url, day),),
            )
        )
    if not announcements:
        gaps.append("近 90 天没有收录到该公司的公告标题")
    if not findings:
        return _report("macro_news", [], coverage="unavailable", summary="；".join(gaps), gaps=gaps)
    coverage = "full" if not gaps else "partial"
    summary = _summary(findings, f"FRED vintage + 近 90 天 {len(announcements)} 条公告标题")
    return _report(
        "macro_news", findings, coverage=coverage, summary=summary, metrics=metrics, gaps=gaps
    )


# ---------------------------------------------------------------- 情绪


def sentiment_analyst(
    subject: Subject,
    bars: Sequence[Bar],
    benchmark_symbol: str,
    benchmark: Sequence[Bar],
    temperature: TemperatureResult | None,
    sector: TemperatureResult | None,
) -> AnalystReport:
    if subject.market != "CN":
        return _unavailable("sentiment", "美股情绪数据未接入（不读取社交媒体），情绪维度不出分")
    findings: list[Finding] = []
    gaps: list[str] = []
    metrics: dict[str, str] = {}
    as_of = bars[-1].trade_date.isoformat() if bars else ""
    if temperature is not None and temperature.score is not None:
        score = temperature.score
        metrics["market_temperature"] = str(score)
        cite = Citation(
            "市场温度", f"全市场温度 {score}（{temperature.label}）", "market-temperature-v1", as_of
        )
        if score >= 65:
            findings.append(
                Finding(f"A 股市场温度 {score}（{temperature.label}），风险偏好偏强", 1, 1, (cite,))
            )
        elif score <= 35:
            findings.append(
                Finding(
                    f"A 股市场温度 {score}（{temperature.label}），风险偏好偏弱", -1, 1, (cite,)
                )
            )
        else:
            findings.append(Finding(f"A 股市场温度 {score}（{temperature.label}）", 0, 1, (cite,)))
    else:
        gaps.append("市场温度样本不足")
    if sector is not None and sector.score is not None and subject.industry_name:
        metrics["sector_strength"] = str(sector.score)
        cite = Citation(
            "板块强弱", f"{subject.industry_name} 强弱分 {sector.score}", "sector-strength", as_of
        )
        text = (
            f"所属行业「{subject.industry_name}」近 60 个交易日强弱分 {sector.score}"
            f"（{sector.label}，{sector.coverage}）"
        )
        if sector.score >= 60:
            findings.append(Finding(text + "，资金偏好该板块", 1, 1, (cite,)))
        elif sector.score <= 40:
            findings.append(Finding(text + "，板块相对走弱", -1, 1, (cite,)))
        else:
            findings.append(Finding(text, 0, 1, (cite,)))
    else:
        gaps.append("所属行业强弱样本不足")
    for window in (20, 60):
        excess = _relative(bars, benchmark, window)
        if excess is None:
            continue
        metrics[f"excess_{window}d"] = _plain(excess.quantize(Decimal("0.0001")))
        cites = (
            _bar_cite(subject, bars[-1]),
            Citation(
                "A股日线",
                f"{benchmark_symbol} {benchmark[-1].trade_date} 收盘 {benchmark[-1].close}",
                f"bar:{benchmark_symbol}:{benchmark[-1].trade_date}",
                benchmark[-1].trade_date.isoformat(),
            ),
        )
        text = f"近 {window} 个交易日相对沪深300 超额 {_pct(excess)}"
        if excess > Decimal("0.05"):
            findings.append(Finding(text + "，相对强势", 1, 1, cites))
        elif excess < Decimal("-0.05"):
            findings.append(Finding(text + "，相对弱势", -1, 1, cites))
    if not findings:
        return _report("sentiment", [], coverage="unavailable", summary="；".join(gaps), gaps=gaps)
    coverage = "full" if not gaps else "partial"
    summary = _summary(findings, "市场温度、行业强弱与相对沪深300 的超额")
    return _report(
        "sentiment", findings, coverage=coverage, summary=summary, metrics=metrics, gaps=gaps
    )


def run_analysts(
    bridge: FinScopeDataBridge, subject: Subject
) -> tuple[list[AnalystReport], TechnicalMetrics, list[Announcement]]:
    bars = bridge.bars(subject)
    benchmark_symbol, benchmark = bridge.benchmark(subject)
    announcements = bridge.announcements(subject)
    reports = [
        fundamental_analyst(subject, bridge.facts(subject)),
        technical_analyst(subject, bars),
        macro_news_analyst(subject, bridge.macro(), announcements, bridge.as_of),
        sentiment_analyst(
            subject,
            bars,
            benchmark_symbol,
            benchmark,
            bridge.temperature(subject),
            bridge.sector(subject),
        ),
    ]
    return reports, technical_metrics(bars), announcements


# ---------------------------------------------------------------- 工具函数


def _announcement_polarity(title: str) -> tuple[int, bool]:
    if any(word in title for word in NEGATIVE_WORDS):
        return -1, any(word in title for word in SEVERE_WORDS)
    if any(word in title for word in POSITIVE_WORDS):
        return 1, False
    return 0, False


def _trim_title(title: str, name: str) -> str:
    for prefix in (f"{name}：", f"{name}:"):
        if title.startswith(prefix):
            return title[len(prefix) :][:60]
    return title[:60]


def _bar_cite(subject: Subject, bar: Bar) -> Citation:
    return Citation(
        "A股日线",
        f"{subject.symbol} {bar.trade_date} 收盘 {bar.close}",
        f"bar:{subject.symbol}:{bar.trade_date}",
        bar.trade_date.isoformat(),
    )


def _fact_cite(label: str, fact: FactRecord) -> Citation:
    return Citation(
        "SEC EDGAR",
        f"{label} {fact.form} 期末 {fact.period_end} = {fact.value}（{fact.unit}），"
        f"提交 {fact.filed}，accession {fact.accession_no}",
        fact.source_url if fact.source_url.startswith("https://") else fact.accession_no,
        fact.filed.isoformat(),
    )


def _ma(bars: Sequence[Bar], window: int) -> Decimal | None:
    if len(bars) < window:
        return None
    total = sum((bar.close for bar in bars[-window:]), Decimal(0))
    return (total / Decimal(window)).quantize(Decimal("0.01"))


def _stdev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((item - mean) ** 2 for item in values) / (len(values) - 1))


def _rsi(closes: Sequence[float], window: int) -> float | None:
    if len(closes) <= window:
        return None
    gains = losses = 0.0
    for index in range(1, window + 1):
        change = closes[index] - closes[index - 1]
        gains += max(change, 0)
        losses += max(-change, 0)
    avg_gain, avg_loss = gains / window, losses / window
    for index in range(window + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        avg_gain = (avg_gain * (window - 1) + max(change, 0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-change, 0)) / window
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _ema(values: Sequence[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    found: list[float] = []
    for value in values:
        found.append(value if not found else alpha * value + (1 - alpha) * found[-1])
    return found


def _macd_hist(closes: Sequence[float]) -> tuple[float | None, float | None]:
    if len(closes) < 35:
        return None, None
    fast, slow = _ema(closes, 12), _ema(closes, 26)
    dif = [a - b for a, b in zip(fast, slow, strict=True)]
    dea = _ema(dif, 9)
    hist = [2 * (a - b) for a, b in zip(dif, dea, strict=True)]
    return hist[-1], hist[-2]


def _volume_ratio(bars: Sequence[Bar]) -> float | None:
    if len(bars) < 20:
        return None
    recent = sum(float(bar.volume) for bar in bars[-5:]) / 5
    base = sum(float(bar.volume) for bar in bars[-20:]) / 20
    return None if base <= 0 else recent / base


def _relative(bars: Sequence[Bar], benchmark: Sequence[Bar], window: int) -> Decimal | None:
    if len(bars) <= window or len(benchmark) <= window:
        return None
    if bars[-1].trade_date != benchmark[-1].trade_date:
        return None
    own = bars[-1].close / bars[-1 - window].close - 1
    base = benchmark[-1].close / benchmark[-1 - window].close - 1
    return own - base


def _annual(facts: Sequence[FactRecord], concepts: Sequence[str]) -> list[FactRecord]:
    """年度口径（期间 330~380 天），同一期末取最晚提交的那一条。

    公司会换科目（如 Revenues → RevenueFromContract…），取最近期末最新的那个科目整列。
    """

    best: list[FactRecord] = []
    for concept in concepts:
        chosen: dict[date, FactRecord] = {}
        for item in facts:
            if item.concept != concept or item.period_start is None:
                continue
            days = (item.period_end - item.period_start).days
            if not 330 <= days <= 380:
                continue
            current = chosen.get(item.period_end)
            if current is None or item.filed > current.filed:
                chosen[item.period_end] = item
        series = [chosen[key] for key in sorted(chosen)]
        if series and (not best or series[-1].period_end > best[-1].period_end):
            best = series
    return best


def _instants(facts: Sequence[FactRecord], concept: str) -> list[FactRecord]:
    chosen: dict[date, FactRecord] = {}
    for item in facts:
        if item.concept != concept or item.period_start is not None:
            continue
        current = chosen.get(item.period_end)
        if current is None or item.filed > current.filed:
            chosen[item.period_end] = item
    return [chosen[key] for key in sorted(chosen)]


def _latest_instant(facts: Sequence[FactRecord], concept: str) -> FactRecord | None:
    rows = _instants(facts, concept)
    return rows[-1] if rows else None


def _year_before(rows: Sequence[FactRecord], end: date) -> FactRecord | None:
    target = end - timedelta(days=365)
    candidates = [item for item in rows if abs((item.period_end - target).days) <= 20]
    return candidates[-1] if candidates else None


def _margins(
    gross: Sequence[FactRecord], revenue: Sequence[FactRecord]
) -> list[tuple[date, Decimal, FactRecord]]:
    by_end = {item.period_end: item for item in revenue}
    found: list[tuple[date, Decimal, FactRecord]] = []
    for item in gross:
        base = by_end.get(item.period_end)
        if base is not None and base.value > 0:
            found.append((item.period_end, item.value / base.value, item))
    return found


def _summary(findings: Sequence[Finding], basis: str) -> str:
    ups = sum(1 for item in findings if item.polarity > 0)
    downs = sum(1 for item in findings if item.polarity < 0)
    neutral = len(findings) - ups - downs
    return f"依据 {basis}：识别出 {ups} 条利多、{downs} 条利空、{neutral} 条中性信号。"


def _pct(value: Decimal) -> str:
    return f"{value * 100:+.2f}%"


def _pp(value: Decimal) -> str:
    return f"{value * 100:+.2f} 个百分点"


def _plain(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _ratio(value: float | None) -> str:
    return "" if value is None else f"{value:.4f}"
