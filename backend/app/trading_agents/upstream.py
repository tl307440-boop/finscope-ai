"""调用已安装的 TradingAgents 原版图（git pin 见 vendor.PINNED_UPSTREAM）。

- 工具层：注册 finscope vendor，所有工具经 FinScopeDataBridge 读库，并把模型请求的日期
  一律截断到本次推演的 as_of（模型即使要未来数据也拿不到）。
- 模型层：配置了 LLM_API_KEY / LLM_MODEL 时走 openai_compatible；否则用不访问外网的 Mock，
  Mock 只服务于离线测试，接口层不会在未配置模型时把它当成「AI 结论」返回。
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date

from app.core.config import Settings, get_settings
from app.services.runtime_settings import effective_settings
from app.trading_agents.analysts import (
    fundamental_analyst,
    macro_news_analyst,
    technical_analyst,
)
from app.trading_agents.bridge import DataStore, FinScopeDataBridge, Subject
from app.trading_agents.graph import DebateRound, RiskNote, SimulationResult, Trace
from app.trading_agents.vendor import PINNED_UPSTREAM
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import Engine
from tradingagents.dataflows.router import VENDOR_METHODS
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

_REGISTERED = False
_MOCK_TEXT = "Rating: Hold\n这是研究评级，不是下单指令。"
_RUN_LOCK = threading.Lock()
_CONTEXT: dict[str, object] = {}
NO_DATA = "NO_DATA_AVAILABLE"


class _MockChat(BaseChatModel):
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=_MOCK_TEXT))])

    def bind_tools(self, tools: object, **kwargs: object) -> "_MockChat":
        del tools, kwargs
        return self

    @property
    def _llm_type(self) -> str:
        return "finscope-mock"


class _MockClient:
    def __init__(self) -> None:
        self._llm = _MockChat()

    def get_llm(self) -> _MockChat:
        return self._llm

    def validate_model(self) -> bool:
        return True


def register_finscope_vendor() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    methods = {
        "get_stock_data": _stock,
        "get_indicators": _indicators,
        "get_fundamentals": _fundamentals,
        "get_balance_sheet": _fundamentals,
        "get_cashflow": _fundamentals,
        "get_income_statement": _fundamentals,
        "get_news": _news,
        "get_global_news": _global_news,
        "get_insider_transactions": _missing,
        "get_macro_indicators": _global_news,
        "get_prediction_markets": _missing,
    }
    for name, func in methods.items():
        VENDOR_METHODS.setdefault(name, {})
        VENDOR_METHODS[name]["finscope"] = func
    _REGISTERED = True


def _settings_for(engine: Engine | None) -> Settings:
    """有数据库连接时读界面里填的有效配置，否则只看环境变量。"""

    return effective_settings(engine) if engine is not None else get_settings()


def finscope_config(use_llm: bool = False, settings: Settings | None = None) -> dict[str, object]:
    config = dict(DEFAULT_CONFIG)
    settings = settings or get_settings()
    if use_llm:
        config["llm_provider"] = "openai_compatible"
        config["backend_url"] = settings.llm_api_base_url or None
        config["quick_think_llm"] = settings.llm_model
        config["deep_think_llm"] = settings.llm_model
    else:
        config["llm_provider"] = "openai"
        config["quick_think_llm"] = "mock"
        config["deep_think_llm"] = "mock"
    config["checkpoint_enabled"] = False
    config["output_language"] = "Chinese"
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["max_tool_rounds"] = 1
    config["data_vendors"] = {
        "core_stock_apis": "finscope",
        "technical_indicators": "finscope",
        "fundamental_data": "finscope",
        "news_data": "finscope",
        "macro_data": "finscope",
        "prediction_markets": "finscope",
    }
    config["upstream_commit"] = PINNED_UPSTREAM
    return config


def run_upstream(
    symbol: str,
    trade_date: date,
    max_debate_rounds: int = 1,
    engine: Engine | None = None,
) -> SimulationResult:
    settings = _settings_for(engine)
    use_llm = bool(settings.llm_api_key and settings.llm_model)
    state, rating = propagate(symbol, trade_date, max_debate_rounds, engine=engine, use_llm=use_llm)
    traces = _reports(state)
    debate = state.get("investment_debate_state")
    risk = state.get("risk_debate_state")
    rounds: list[DebateRound] = []
    if isinstance(debate, dict):
        bull = str(debate.get("bull_history") or "")
        bear = str(debate.get("bear_history") or "")
        if bull or bear:
            rounds.append(DebateRound(1, bull[:2000], bear[:2000]))
        if bull:
            traces.append(Trace("Bull Researcher", bull[:2000]))
        if bear:
            traces.append(Trace("Bear Researcher", bear[:2000]))
    plan = state.get("investment_plan")
    if plan:
        traces.append(Trace("Research Manager", str(plan)[:2000]))
    trader_plan = state.get("trader_investment_plan")
    if trader_plan:
        traces.append(Trace("Trader", str(trader_plan)[:2000]))
    notes: list[RiskNote] = []
    if isinstance(risk, dict):
        mapping = (
            ("Aggressive Analyst", "aggressive_history"),
            ("Conservative Analyst", "conservative_history"),
            ("Neutral Analyst", "neutral_history"),
        )
        for profile, key in mapping:
            text = str(risk.get(key) or risk.get(key.replace("_history", "_response")) or "")
            notes.append(RiskNote(profile, text[:2000] or "无额外意见"))
            traces.append(Trace(profile, notes[-1].content))
    decision = str(state.get("final_trade_decision") or "")
    if decision:
        traces.append(Trace("Portfolio Manager", decision[:2000]))
    events = tuple(f"node_started:{item.node_name}" for item in traces) + (
        f"rating_ready:{rating}",
    )
    return SimulationResult(
        str(rating),
        decision,
        tuple(traces),
        tuple(rounds),
        tuple(notes),
        events,
        config={
            "engine": "tradingagents" if use_llm else "tradingagents-mock",
            "engine_label": "TradingAgents 原版图（语言模型）"
            if use_llm
            else "TradingAgents 原版图（离线 Mock，仅测试用）",
            "llm": use_llm,
            "model": settings.llm_model if use_llm else "mock",
            "upstream_commit": PINNED_UPSTREAM,
            "max_debate_rounds": max_debate_rounds,
            "orders": False,
        },
    )


def _reports(state: dict[str, object]) -> list[Trace]:
    pairs = (
        ("market_report", "Market Analyst"),
        ("sentiment_report", "Sentiment Analyst"),
        ("news_report", "News Analyst"),
        ("fundamentals_report", "Fundamentals Analyst"),
    )
    traces: list[Trace] = []
    for key, node in pairs:
        text = state.get(key)
        traces.append(Trace(node, str(text or "无报告")[:2000]))
    return traces


def propagate(
    symbol: str,
    trade_date: date,
    max_debate_rounds: int = 1,
    *,
    engine: Engine | None = None,
    use_llm: bool = False,
) -> tuple[dict[str, object], str]:
    register_finscope_vendor()
    import tradingagents.graph.trading_graph as graph_module

    original = graph_module.create_tier_client
    settings = _settings_for(engine)
    if use_llm:
        key = settings.llm_api_key
        graph_module.create_tier_client = lambda config, tier, **extra: original(
            config, tier, api_key=key, **extra
        )
    else:
        graph_module.create_tier_client = lambda config, tier, **extra: _MockClient()
    _block_social_feeds()
    config = finscope_config(use_llm, settings)
    config["max_debate_rounds"] = max_debate_rounds
    with _RUN_LOCK, _bound(engine, trade_date):
        try:
            graph = TradingAgentsGraph(
                selected_analysts=("market", "social", "news", "fundamentals"),
                debug=False,
                config=config,
            )
            state, rating = graph.propagate(symbol, trade_date.isoformat())
        finally:
            graph_module.create_tier_client = original
    if not isinstance(state, dict):
        state = dict(state)
    return state, str(rating)


def propagate_with_mock(
    symbol: str, trade_date: date, max_debate_rounds: int = 1
) -> tuple[dict[str, object], str]:
    return propagate(symbol, trade_date, max_debate_rounds, use_llm=False)


@contextmanager
def _bound(engine: Engine | None, as_of: date) -> Iterator[None]:
    _CONTEXT.clear()
    if engine is not None:
        _CONTEXT["store"] = DataStore(engine)
    _CONTEXT["as_of"] = as_of
    try:
        yield
    finally:
        _CONTEXT.clear()


def _bridge(requested: str | None) -> FinScopeDataBridge | None:
    store = _CONTEXT.get("store")
    as_of = _CONTEXT.get("as_of")
    if not isinstance(store, DataStore) or not isinstance(as_of, date):
        return None
    day = as_of
    if requested:
        try:
            day = min(date.fromisoformat(requested[:10]), as_of)
        except ValueError:
            day = as_of
    return FinScopeDataBridge(store, day)


def _subject(bridge: FinScopeDataBridge | None, symbol: str) -> Subject | None:
    return None if bridge is None else bridge.subject(symbol)


def _block_social_feeds() -> None:
    import tradingagents.agents.analysts.sentiment_analyst as sentiment

    sentiment.fetch_stocktwits_messages = _no_social
    sentiment.fetch_reddit_posts = _no_social


def _no_social(*args: object, **kwargs: object) -> str:
    del args, kwargs
    return f"{NO_DATA}: 不读取 StockTwits 或 Reddit。情绪只看市场温度、行业强弱与相对强弱。"


def _stock(symbol: str, start_date: str, end_date: str) -> str:
    bridge = _bridge(end_date)
    subject = _subject(bridge, symbol)
    if bridge is None or subject is None:
        return f"{NO_DATA}: {symbol} 不在已收录的证券列表里"
    start = date.fromisoformat(start_date[:10]) if start_date else date.min
    rows = [bar for bar in bridge.bars(subject, limit=400) if bar.trade_date >= start]
    if not rows:
        return f"{NO_DATA}: {symbol} 在 {start_date}~{bridge.as_of} 没有已入库日线"
    lines = ["Date,Open,High,Low,Close,Volume"]
    lines.extend(
        f"{bar.trade_date},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}" for bar in rows
    )
    return "\n".join(lines)


def _indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
    del indicator, look_back_days
    bridge = _bridge(curr_date)
    subject = _subject(bridge, symbol)
    if bridge is None or subject is None:
        return f"{NO_DATA}: {symbol} 不在已收录的证券列表里"
    return _render(technical_analyst(subject, bridge.bars(subject)).as_dict())


def _fundamentals(ticker: str, freq: str = "quarterly", curr_date: str | None = None) -> str:
    del freq
    bridge = _bridge(curr_date)
    subject = _subject(bridge, ticker)
    if bridge is None or subject is None:
        return f"{NO_DATA}: {ticker} 不在已收录的证券列表里"
    return _render(fundamental_analyst(subject, bridge.facts(subject)).as_dict())


def _news(ticker: str, start_date: str, end_date: str) -> str:
    del start_date
    bridge = _bridge(end_date)
    subject = _subject(bridge, ticker)
    if bridge is None or subject is None:
        return f"{NO_DATA}: {ticker} 不在已收录的证券列表里"
    report = macro_news_analyst(
        subject, bridge.macro(), bridge.announcements(subject), bridge.as_of
    )
    return _render(report.as_dict())


def _global_news(curr_date: str, *args: object, **kwargs: object) -> str:
    del args, kwargs
    bridge = _bridge(curr_date)
    if bridge is None:
        return f"{NO_DATA}: 没有绑定数据库"
    macro = bridge.macro()
    if macro.regime_code == "insufficient_data":
        return f"{NO_DATA}: {bridge.as_of} 没有已发布的 FRED vintage"
    scores = (macro.growth, macro.inflation, macro.liquidity)
    return "\n".join(f"{item.code}: {item.value} ({'; '.join(item.evidence)})" for item in scores)


def _render(report: dict[str, object]) -> str:
    if report.get("coverage") == "unavailable":
        return f"{NO_DATA}: {report.get('summary')}"
    lines = [str(report.get("summary", ""))]
    findings = report.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            refs = item.get("citations")
            label = ""
            if isinstance(refs, list) and refs and isinstance(refs[0], dict):
                label = f" [source: {refs[0].get('label')}]"
            lines.append(f"- {item.get('text')}{label}")
    return "\n".join(lines)


def _missing(*args: object, **kwargs: object) -> str:
    del args, kwargs
    return "DATA_UNAVAILABLE: 这个工具没有已核对的数据"
