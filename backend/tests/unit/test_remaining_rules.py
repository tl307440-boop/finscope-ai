from datetime import UTC, date, datetime
from decimal import Decimal

from app.scripts.eval_multi_agent_experiment import recall, summary
from app.services.china_macro import parse_macro_csv, series_status
from app.services.daily_digest import build_digest
from app.services.disclosure_events import (
    DisclosureDraft,
    event_hash,
    heat_label,
    validate_disclosure,
)
from app.trading_agents.backtest import window_return
from app.trading_agents.graph import debate_ready, parse_rating, risk_ready, run_mock_graph
from app.trading_agents.vendor import income_visible, single_quarter_check


def test_duplicate_announcements_share_a_hash_and_heat_is_not_sentiment() -> None:
    published = datetime(2026, 3, 1, tzinfo=UTC)
    draft = DisclosureDraft(
        "贵州茅台2025年年度报告", "https://www.cninfo.com.cn/a", "cninfo", published, "filing"
    )
    assert event_hash(draft.canonical_url, draft.title, published) == event_hash(
        draft.canonical_url,
        draft.title,
        published,
    )
    assert validate_disclosure(draft) is None
    assert validate_disclosure(
        DisclosureDraft("标题", "https://reddit.com/a", "reddit", published, "social")
    )
    assert "情绪" in heat_label(0)
    assert "情绪分" not in heat_label(2)


def test_china_csv_rejects_missing_source_and_social_financing_starts_unavailable() -> None:
    accepted, errors = parse_macro_csv(
        "series_code,observation_date,value,unit,source,published_at\n"
        "CPI_CN,2026-09-01,0.9,Percent,,2026-10-01\n"
    )
    assert accepted == []
    assert errors
    assert series_status("SOCIAL_FINANCING", False) == "unavailable"
    rows, problems = parse_macro_csv(
        "series_code,observation_date,value,unit,source,published_at\n"
        "CPI_CN,2026-09-01,0.9,Percent,国家统计局,2026-10-15\n"
    )
    assert problems == []
    assert rows[0].value == Decimal("0.9")


def test_digest_does_not_invent_items() -> None:
    items = build_digest([], 0)
    assert any("没有新的研究记录" in item.summary for item in items)
    assert any("不会向外发送" in item.summary for item in items)


def test_moutai_interim_is_hidden_before_its_notice_date() -> None:
    hidden = income_visible("600519.SH", date(2026, 6, 1))
    assert "2026H1" not in hidden
    assert "2026Q1" in hidden
    visible = income_visible("600519.SH", date(2026, 8, 16))
    assert "2026H1" in visible
    check = single_quarter_check("600519.SH", date(2026, 8, 16))
    assert "差额 0.00" in check
    assert single_quarter_check("600519.SH", date(2026, 6, 1)).startswith("NO_DATA_AVAILABLE")


def test_backtest_needs_a_later_close_and_recall_needs_labels() -> None:
    closes = [(date(2024, 1, 2), Decimal("10")), (date(2024, 1, 20), Decimal("12"))]
    measured = window_return(closes, date(2024, 1, 2), 30)
    assert measured is not None
    assert measured.actual == Decimal("0.2")
    assert window_return(closes[:1], date(2024, 1, 2), 30) is None
    assert recall({"002717"}, {"002717", "000001"}) == Decimal("0.5")
    assert "留空" in summary(1)


def test_mock_graph_waits_for_both_sides_and_does_not_default_to_hold() -> None:
    assert debate_ready(1, 1) is False
    assert debate_ready(2, 1) is True
    assert risk_ready(2, 1) is False
    assert risk_ready(3, 1) is True
    assert parse_rating("没有评级词") == "REVIEW"
    result = run_mock_graph(
        symbol="600519.SH", as_of_text="2026-08-16", context="NO_DATA_AVAILABLE: 空"
    )
    assert result.rating == "REVIEW"
    assert any(trace.node_name == "Conservative Analyst" for trace in result.traces)
    assert "下单" not in result.decision or "不是下单" in result.decision
