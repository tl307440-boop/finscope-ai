"""板块行情工具 `industry_board`：多关键词合并、亿元换算、过期、查不到。用假仓库，不碰数据库。"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError
from app.repositories.board_market import StoredSnapshot
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools import boards as board_tools
from app.services.assistant.tools.base import ToolContext
from app.services.board_gap import THS_SOURCE, LiveChange
from sqlalchemy import Engine

NOW = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)  # 上海 10-21 16:00


def _snapshot(code: str, name: str, kind: em.BoardKind, day: date) -> StoredSnapshot:
    return StoredSnapshot(
        node=em.BoardNode(code=code, name=name, kind=kind, level=1),
        quote=em.BoardQuote(
            code=code,
            name=name,
            last=Decimal("1234.5"),
            change_pct=Decimal("-1.25"),
            change_5d_pct=Decimal("3.4"),
            change_10d_pct=None,
            change_60d_pct=Decimal("12.8"),
            change_ytd_pct=Decimal("35.2"),
            amount=Decimal("88800000000"),
            turnover_pct=Decimal("2.1"),
            pe_ttm=Decimal("98.76"),
            market_cap=Decimal("5432100000000"),
            main_net_inflow=Decimal("-1234567890"),
            main_net_inflow_5d=None,
            advancers=40,
            decliners=120,
            unchanged=3,
            leader_code="688981",
            leader_name="中芯国际",
            leader_change_pct=Decimal("6.01"),
        ),
        trade_date=day,
        fetched_at=NOW,
    )


class FakeBoards:
    data: dict[str, list[StoredSnapshot]] = {}
    asked: list[tuple[str, date, tuple[str, ...]]] = []

    def __init__(self, engine: object) -> None:
        del engine

    def search_latest(
        self, query: str, on_or_before: date, kinds: list[str], limit: int
    ) -> list[StoredSnapshot]:
        FakeBoards.asked.append((query, on_or_before, tuple(kinds)))
        found = [
            item
            for item in FakeBoards.data.get(query, [])
            if item.node.kind in kinds and item.trade_date <= on_or_before
        ]
        return found[:limit]


@pytest.fixture(autouse=True)
def fake_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeBoards.data = {}
    FakeBoards.asked = []
    monkeypatch.setattr(board_tools, "BoardMarketRepository", FakeBoards)

    async def _offline(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        raise ProviderError(code="UPSTREAM_ERROR", message="测试里不连新浪", status_code=502)

    monkeypatch.setattr(board_tools, "fetch_board_changes", _offline)


def _run(arguments: str) -> ToolResult:
    ctx = ToolContext(engine=cast(Engine, None), user_id=uuid4(), now=NOW)
    return (
        ToolRegistry([board_tools.IndustryBoardTool()])
        .execute("industry_board", arguments, ctx)
        .result
    )


def test_several_keywords_are_merged_without_duplicates() -> None:
    semis = _snapshot("BK1036", "半导体", "industry", date(2026, 10, 21))
    chips = _snapshot("BK0891", "国产芯片", "concept", date(2026, 10, 21))
    FakeBoards.data = {"半导体": [semis], "芯片": [chips, semis]}
    result = _run('{"queries": ["半导体", "芯片", "半导体 "]}')
    assert result.status == "ok"
    assert [row["name"] for row in result.rows] == ["半导体", "国产芯片"]
    assert [row["kind"] for row in result.rows] == ["行业", "概念"]
    assert [query for query, _, _ in FakeBoards.asked] == ["半导体", "芯片"]
    assert FakeBoards.asked[0][1:] == (date(2026, 10, 21), ("industry", "concept"))
    assert result.as_of == date(2026, 10, 21)


def test_money_is_given_in_yi_yuan_with_decimal_rounding() -> None:
    FakeBoards.data = {"半导体": [_snapshot("BK1036", "半导体", "industry", date(2026, 10, 21))]}
    row = _run('{"queries": ["半导体"]}').rows[0]
    assert row["main_net_inflow"] == "-12.35"  # -1234567890 元
    assert row["main_net_inflow_5d"] is None
    assert row["change_ytd_pct"] == "35.2" and row["pe_ttm"] == "98.76"
    assert (row["advancers"], row["decliners"], row["leader_name"]) == ("40", "120", "中芯国际")
    units = {column.key: column.unit for column in board_tools.COLUMNS}
    assert units["main_net_inflow"] == "亿元" and units["change_ytd_pct"] == "%"


def test_kind_filter_and_as_of_are_passed_to_the_repository() -> None:
    FakeBoards.data = {"芯片": [_snapshot("BK0891", "国产芯片", "concept", date(2026, 9, 30))]}
    result = _run('{"queries": ["芯片"], "kind": "concept", "as_of": "2026-10-08"}')
    assert result.status == "ok"
    assert FakeBoards.asked == [("芯片", date(2026, 10, 8), ("concept",))]


def test_old_snapshot_is_marked_stale() -> None:
    FakeBoards.data = {"半导体": [_snapshot("BK1036", "半导体", "industry", date(2026, 10, 2))]}
    result = _run('{"queries": ["半导体"]}')
    assert result.status == "stale"
    assert "2026-10-02" in (result.reason or "")


def test_a_stale_snapshot_keeps_old_columns_and_overlays_todays_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBoards.data = {"半导体": [_snapshot("BK1036", "半导体", "industry", date(2026, 10, 2))]}

    async def _live(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        return (
            LiveChange(
                name="半导体",
                change_pct=Decimal("2.5"),
                leader_name="北方华创",
                leader_change_pct=Decimal("4.2"),
                source="新浪实时",
            ),
        )

    monkeypatch.setattr(board_tools, "fetch_board_changes", _live)
    result = _run('{"queries": ["半导体"]}')
    row = result.rows[0]
    assert result.status == "stale"
    assert row["change_pct"] == "2.5" and row["change_source"] == "新浪实时"
    assert row["change_date"] == "2026-10-21"
    assert row["leader_name"] == "北方华创" and row["leader_change_pct"] == "4.2"
    assert row["pe_ttm"] == "98.76" and row["change_5d_pct"] == "3.4" and row["advancers"] == "40"
    assert row["trade_date"] == "2026-10-02"
    assert "涨跌日期是 2026-10-21" in (result.reason or "") and "2026-10-02" in (
        result.reason or ""
    )


def test_a_live_quote_without_a_change_does_not_wipe_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBoards.data = {"半导体": [_snapshot("BK1036", "半导体", "industry", date(2026, 10, 2))]}

    async def _empty(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        return ()

    monkeypatch.setattr(board_tools, "fetch_board_changes", _empty)
    row = _run('{"queries": ["半导体"]}').rows[0]
    assert row["change_pct"] == "-1.25" and row["change_source"] == "收盘快照"
    assert row["change_date"] == "2026-10-02"
    assert row["leader_name"] == "中芯国际" and row["leader_change_pct"] == "6.01"


def test_tonghuashun_fills_when_sina_has_no_same_name(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeBoards.data = {
        "人形机器人": [_snapshot("BK1184", "人形机器人", "concept", date(2026, 10, 2))]
    }

    async def _live(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        return (
            LiveChange("机器人", Decimal("1.2"), "甲股份", Decimal("3"), "新浪实时"),
            LiveChange("人形机器人", Decimal("4.25"), None, None, THS_SOURCE),
        )

    monkeypatch.setattr(board_tools, "fetch_board_changes", _live)
    row = _run('{"queries": ["人形机器人"]}').rows[0]
    assert row["change_source"] == THS_SOURCE
    assert row["change_pct"] == "4.25" and row["change_date"] == "2026-10-21"
    assert row["leader_name"] == "中芯国际"


def test_a_long_live_percent_is_rounded_to_two_places(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeBoards.data = {
        "机器人概念": [_snapshot("BK1090", "机器人概念", "concept", date(2026, 10, 2))]
    }

    async def _live(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        return (
            LiveChange(
                "机器人", Decimal("0.5955594253374"), "瑞凌股份", Decimal("12.423"), "新浪实时"
            ),
        )

    monkeypatch.setattr(board_tools, "fetch_board_changes", _live)
    row = _run('{"queries": ["机器人概念"]}').rows[0]
    assert row["change_pct"] == "0.6" and row["leader_change_pct"] == "12.42"
    assert row["change_source"] == "新浪实时" and row["leader_name"] == "瑞凌股份"


def test_sina_change_without_a_leader_keeps_the_snapshot_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeBoards.data = {"半导体": [_snapshot("BK1036", "半导体", "industry", date(2026, 10, 2))]}

    async def _live(gateway: object) -> tuple[LiveChange, ...]:
        del gateway
        return (
            LiveChange(
                name="半导体",
                change_pct=Decimal("2.5"),
                leader_name=None,
                leader_change_pct=None,
                source="新浪实时",
            ),
        )

    monkeypatch.setattr(board_tools, "fetch_board_changes", _live)
    row = _run('{"queries": ["半导体"]}').rows[0]
    assert row["change_pct"] == "2.5" and row["change_source"] == "新浪实时"
    assert row["leader_name"] == "中芯国际" and row["leader_change_pct"] == "6.01"
    assert row["change_5d_pct"] == "3.4" and row["advancers"] == "40"


def test_no_matching_board_is_unavailable_with_a_hint() -> None:
    result = _run('{"queries": ["量子玄学"]}')
    assert result.status == "unavailable"
    assert "量子玄学" in (result.reason or "") and "半导体" in (result.reason or "")


@pytest.mark.parametrize(
    "arguments",
    ['{"queries": []}', '{"queries": ["  "]}', '{"queries": ["a","b","c","d","e"]}'],
)
def test_bad_arguments_are_rejected(arguments: str) -> None:
    assert _run(arguments).status == "unavailable"
