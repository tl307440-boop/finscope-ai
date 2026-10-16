from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import PasswordService
from app.db.session import create_db_engine
from app.main import create_app
from app.repositories.accounts import AccountRepository
from app.trading_agents.graph import DebateRound, RiskNote, SimulationResult, Trace
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


def _fake_simulation(symbol: str, as_of: date, max_debate_rounds: int = 1) -> SimulationResult:
    """确定性桩。规格书 §14.2 要求集成测试不依赖外网，TradingAgents 图会走外部行情。"""

    del symbol, as_of, max_debate_rounds
    traces = (
        Trace("Market Analyst", "行情报告：样本不足，不做趋势判断。"),
        Trace("Sentiment Analyst", "情绪报告：没有合规标题。"),
        Trace("News Analyst", "新闻报告：没有不晚于 as_of 的标题。"),
        Trace("Fundamentals Analyst", "财报报告：没有已核对的口径。"),
        Trace("Bull Researcher", "多方：估值处于历史低位区间。"),
        Trace("Bear Researcher", "空方：增速放缓尚未证伪。"),
        Trace("Research Manager", "研究经理：两边都缺证据，维持观察。"),
        Trace("Trader", "交易员：不建议下单。"),
        Trace("Aggressive Analyst", "激进：可以分批建仓。"),
        Trace("Conservative Analyst", "保守：等下一期财报再动手。"),
        Trace("Neutral Analyst", "中性：维持现状。"),
        Trace("Portfolio Manager", "组合经理：Hold。"),
    )
    return SimulationResult(
        rating="Hold",
        decision="Rating: Hold\n这是研究评级，不是下单指令。",
        traces=traces,
        debates=(DebateRound(1, "多方：估值处于历史低位区间。", "空方：增速放缓尚未证伪。"),),
        risks=(
            RiskNote("Aggressive Analyst", "激进：可以分批建仓。"),
            RiskNote("Conservative Analyst", "保守：等下一期财报再动手。"),
            RiskNote("Neutral Analyst", "中性：维持现状。"),
        ),
        events=tuple(f"node_started:{item.node_name}" for item in traces) + ("rating_ready:Hold",),
    )


def _ready() -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        from alembic import command
        from alembic.config import Config

        root = Path(__file__).resolve().parents[2]
        command.upgrade(Config(str(root / "alembic.ini")), "head")
        return True
    except SQLAlchemyError:
        return False
    finally:
        engine.dispose()


def _cleanup(username: str) -> None:
    engine = create_db_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                DELETE FROM agent_simulation_runs
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text(
                """
                DELETE FROM refresh_sessions
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text(
                """
                DELETE FROM user_preferences
                WHERE user_id IN (SELECT id FROM users WHERE username = :username)
                """
            ),
            {"username": username},
        )
        connection.execute(
            text("DELETE FROM users WHERE username = :username"), {"username": username}
        )
    engine.dispose()


def _has_bars(display_symbol: str) -> bool:
    engine = create_db_engine(get_settings().database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text(
                    """
                    SELECT count(*) FROM market_bars b
                    JOIN instruments i ON i.id = b.instrument_id
                    WHERE i.display_symbol = :symbol
                    """
                ),
                {"symbol": display_symbol},
            ).scalar_one()
        return int(count) >= 120
    finally:
        engine.dispose()


@pytest.mark.integration
def test_rules_engine_runs_on_stored_data_and_settles_alpha() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    if not _has_bars("600519.SH"):
        pytest.skip("本机还没有灌入 600519.SH 的日线")
    username = f"t30-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    AccountRepository(engine).create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()
    try:
        with TestClient(create_app()) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            early = client.post(
                "/api/v1/simulation/runs",
                headers=headers,
                json={"symbol": "600519.SH", "as_of": "2025-06-03", "holding_days": 30},
            )
            assert early.status_code == 200
            first = early.json()["data"]
            assert first["engine"] == "finscope-rules"
            assert first["config"]["llm"] is False
            assert "未调用语言模型" in first["engine_label"]
            analysts = {item["analyst"]: item for item in first["detail"]["analysts"]}
            assert set(analysts) == {"fundamental", "technical", "macro_news", "sentiment"}
            assert analysts["fundamental"]["coverage"] == "unavailable"
            for item in analysts["technical"]["findings"]:
                assert item["citations"]
                for cite in item["citations"]:
                    assert cite["observed_at"] <= "2025-06-03"
            assert first["detail"]["risks"]
            assert "委托" in first["disclaimer"]

            later = client.post(
                "/api/v1/simulation/runs",
                headers=headers,
                json={"symbol": "600519.SH", "as_of": "2025-09-01", "holding_days": 30},
            )
            assert later.status_code == 200
            memory = later.json()["data"]["detail"]["memory"]
            if first["rating"] != "REVIEW":
                assert memory, "持有期已走完的历史决策应进入决策记忆"
                assert memory[0]["as_of"] == "2025-06-03"
                assert memory[0]["verdict"] in {"accurate", "partially_accurate", "divergent"}

            listed = client.get("/api/v1/simulation/runs", headers=headers).json()["data"]
            assert len(listed["runs"]) == 2
            report = client.get(
                f"/api/v1/simulation/runs/{first['run_id']}/report", headers=headers
            ).json()["data"]
            assert "多空辩论" in report["markdown"]
            settled = client.post(
                f"/api/v1/simulation/runs/{first['run_id']}/backtest", headers=headers
            ).json()["data"]
            assert settled["quality_flag"] == "measured"
            assert settled["benchmark_symbol"] == "000300.SH"
            assert "excess_return" in settled
            stream = client.get(
                f"/api/v1/simulation/runs/{first['run_id']}/stream", headers=headers
            )
            assert "event: agent_signal" in stream.text
            assert "event: simulation_completed" in stream.text

            grid = client.post(
                "/api/v1/simulation/experiments",
                headers=headers,
                json={
                    "symbols": ["600519.SH"],
                    "start": "2025-03-03",
                    "end": "2025-06-30",
                    "step": 20,
                    "holding_days": 30,
                },
            )
            assert grid.status_code == 200
            body = grid.json()["data"]
            assert body["samples"] > 0
            assert [item["code"] for item in body["modes"]] == ["multi_agent", "technical_only"]

            future = client.post(
                "/api/v1/simulation/runs",
                headers=headers,
                json={"symbol": "600519.SH", "as_of": "2099-01-01"},
            )
            assert future.status_code == 422
            if not (get_settings().llm_api_key and get_settings().llm_model):
                blocked = client.post(
                    "/api/v1/simulation/runs",
                    headers=headers,
                    json={"symbol": "600519.SH", "as_of": "2025-06-03", "engine": "tradingagents"},
                )
                assert blocked.status_code == 409
    finally:
        _cleanup(username)


@pytest.mark.integration
def test_mock_simulation_persists_rating_without_an_order() -> None:
    if not _ready():
        pytest.skip("PostgreSQL 不可用")
    username = f"t29-{uuid4().hex[:8]}"
    other = f"t29b-{uuid4().hex[:8]}"
    engine = create_db_engine(get_settings().database_url)
    accounts = AccountRepository(engine)
    accounts.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    accounts.create_user(
        username=other,
        email=f"{other}@example.com",
        password_hash=PasswordService("test").hash("correct-password"),
        role="researcher",
    )
    engine.dispose()
    try:
        application = create_app()
        application.state.simulation_runner = _fake_simulation
        with TestClient(application) as client:
            token = client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "correct-password"},
            ).json()["data"]["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post(
                "/api/v1/simulation/runs",
                headers=headers,
                json={"symbol": "600519.SH", "as_of": "2026-06-01", "max_debate_rounds": 1},
            )
            assert created.status_code == 200
            data = created.json()["data"]
            assert data["rating"] == "Hold"
            assert "Market Analyst" in data["nodes"]
            assert "Bull Researcher" in data["nodes"]
            assert "Portfolio Manager" in data["nodes"]
            assert "Conservative Analyst" in data["nodes"]
            assert data["nodes"].index("Conservative Analyst") < data["nodes"].index(
                "Portfolio Manager"
            )
            assert "委托" in data["disclaimer"]
            detail = client.get(f"/api/v1/simulation/runs/{data['run_id']}", headers=headers)
            assert detail.status_code == 200
            stream = client.get(f"/api/v1/simulation/runs/{data['run_id']}/stream", headers=headers)
            backtest = client.post(
                f"/api/v1/simulation/runs/{data['run_id']}/backtest",
                headers=headers,
            )
            assert backtest.status_code == 200
            # 前向窗口有没有被真实日线覆盖，取决于本机已经同步了哪些行情，所以这里断言的是
            # 不变量而不是固定值：没有实际收益就必须是 insufficient，有实际收益才允许 measured。
            # 这一条正是「数据不足时不编造收益」的回归保护。
            backtest_data = backtest.json()["data"]
            assert backtest_data["quality_flag"] in {"insufficient", "measured"}
            if "actual_return" in backtest_data:
                assert backtest_data["quality_flag"] == "measured"
            else:
                assert backtest_data["quality_flag"] == "insufficient"
            assert "event: node_started" in stream.text
            assert "Market Analyst" in stream.text
            other_token = client.post(
                "/api/v1/auth/login",
                json={"username": other, "password": "correct-password"},
            ).json()["data"]["access_token"]
            hidden = client.get(
                f"/api/v1/simulation/runs/{data['run_id']}",
                headers={"Authorization": f"Bearer {other_token}"},
            )
            assert hidden.status_code == 404
            missing = client.get("/api/v1/macro/china")
            names = [item["code"] for item in missing.json()["data"]["series"]]
            assert "SOCIAL_FINANCING" in names
            assert all(item["status"] == "unavailable" for item in missing.json()["data"]["series"])
            hot = client.get("/api/v1/market/hotspots")
            assert "情绪" in hot.json()["data"]["label"]
            temp = client.get("/api/v1/market/temperature")
            assert temp.json()["data"]["label"] in {"无法判断", "偏弱", "中性", "偏强"}
    finally:
        _cleanup(username)
        _cleanup(other)
