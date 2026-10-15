"""调用已安装的 TradingAgents 图。Mock 模型不访问外部语言模型。"""

from datetime import date

from app.trading_agents.graph import DebateRound, RiskNote, SimulationResult, Trace
from app.trading_agents.vendor import PINNED_UPSTREAM, bars_note, income_visible, insider_note
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from tradingagents.dataflows.router import VENDOR_METHODS
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

_REGISTERED = False
_MOCK_TEXT = "Rating: Hold\n这是研究评级，不是下单指令。"


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
        "get_fundamentals": _income,
        "get_balance_sheet": _missing,
        "get_cashflow": _missing,
        "get_income_statement": _income,
        "get_news": _news,
        "get_global_news": _news,
        "get_insider_transactions": _insider,
        "get_macro_indicators": _missing,
        "get_prediction_markets": _missing,
    }
    for name, func in methods.items():
        VENDOR_METHODS.setdefault(name, {})
        VENDOR_METHODS[name]["finscope"] = func
    _REGISTERED = True


def finscope_config() -> dict[str, object]:
    config = dict(DEFAULT_CONFIG)
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


def run_upstream(symbol: str, trade_date: date, max_debate_rounds: int = 1) -> SimulationResult:
    state, rating = propagate_with_mock(symbol, trade_date, max_debate_rounds)
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


def propagate_with_mock(
    symbol: str,
    trade_date: date,
    max_debate_rounds: int = 1,
) -> tuple[dict[str, object], str]:
    register_finscope_vendor()
    import tradingagents.graph.trading_graph as graph_module

    original = graph_module.create_tier_client
    graph_module.create_tier_client = lambda config, tier, **extra: _MockClient()
    _block_social_feeds()
    config = finscope_config()
    config["max_debate_rounds"] = max_debate_rounds
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


def _block_social_feeds() -> None:
    import tradingagents.agents.analysts.sentiment_analyst as sentiment

    sentiment.fetch_stocktwits_messages = _no_social
    sentiment.fetch_reddit_posts = _no_social


def _no_social(*args: object, **kwargs: object) -> str:
    del args, kwargs
    return "NO_DATA_AVAILABLE: 不读取 StockTwits 或 Reddit。情绪只看巨潮标题，当前没有标题。"


def _stock(symbol: str, start_date: str, end_date: str) -> str:
    del start_date
    return bars_note(symbol, date.fromisoformat(end_date))


def _indicators(symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
    del indicator, look_back_days
    return bars_note(symbol, date.fromisoformat(curr_date))


def _income(ticker: str, freq: str = "quarterly", as_of_date: str | None = None) -> str:
    del freq
    day = date.fromisoformat(as_of_date) if as_of_date else date.today()
    return income_visible(ticker, day)


def _news(ticker: str, start_date: str, end_date: str) -> str:
    del ticker, start_date, end_date
    return "NO_DATA_AVAILABLE: 没有不晚于 as_of 的巨潮标题"


def _insider(ticker: str) -> str:
    del ticker
    return insider_note()


def _missing(*args: object, **kwargs: object) -> str:
    del args, kwargs
    return "DATA_UNAVAILABLE: 这个工具没有已核对的数据"
