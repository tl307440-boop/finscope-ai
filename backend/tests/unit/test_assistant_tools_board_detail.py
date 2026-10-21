"""板块详情工具 `board_detail`：摘要、成分股、细分行业、估值缺失、入库回退、只有实时。"""

from datetime import date
from decimal import Decimal
from typing import Any, cast

import pytest
from app.providers.errors import ProviderError
from app.schemas.industry_market import (
    BoardDetail,
    BoardQuoteItem,
    MemberItem,
    MembersBlock,
    PercentileItem,
    Section,
    SubIndustryItem,
    TrendBlock,
    ValuationBlock,
    WindowReturns,
)
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools import board_detail as detail_tools
from app.services.industry_market import IndustryMarketService

from live_fakes import NOW, context, install_boards, snapshot, upstream_down

OK = Section(status="ok", fetched_at=NOW)


def _detail(
    *,
    quote: Section = OK,
    members: Section = OK,
    valuation: Section = OK,
    trend: Section = OK,
) -> BoardDetail:
    board = BoardQuoteItem(
        code="BK1036",
        name="半导体",
        kind="industry",
        level=2,
        last=Decimal("1234.5"),
        change_pct=Decimal("-1.25"),
        change_ytd_pct=Decimal("35.2"),
        amount=Decimal("88800000000"),
        pe_ttm=Decimal("98.76"),
        market_cap=Decimal("5432100000000"),
        main_net_inflow=Decimal("-1234567890"),
        advancers=40,
        decliners=120,
    )
    percentile = PercentileItem(
        current=Decimal("98.76"),
        percentile=Decimal("91.2"),
        minimum=Decimal("20.1"),
        p20=Decimal("40.5"),
        median=Decimal("62.3"),
        p80=Decimal("85.9"),
        maximum=Decimal("120.4"),
        samples=1200,
        start=date(2021, 10, 21),
        end=date(2026, 10, 21),
    )
    return BoardDetail(
        board=board,
        quote_section=quote,
        trend=TrendBlock(
            section=trend,
            bars=[],
            relative=[],
            returns=WindowReturns(
                d20=Decimal("5.5"),
                d120=Decimal("20.25"),
                d250=Decimal("48.0"),
                excess_d20=Decimal("2.1"),
                excess_d60=Decimal("-1.4"),
                excess_d250=Decimal("30.2"),
            ),
            benchmark="沪深300",
        ),
        members=MembersBlock(
            section=members,
            total=150,
            members=[
                MemberItem(
                    code="603501",
                    name="韦尔股份",
                    market_cap=Decimal("200000000000"),
                    change_pct=Decimal("0.5"),
                    main_net_inflow=Decimal("150000000"),
                    sub_industry="数字芯片设计",
                ),
                MemberItem(
                    code="688981",
                    name="中芯国际",
                    market_cap=Decimal("500000000000"),
                    change_pct=Decimal("-2.1"),
                    pe_dynamic=Decimal("155.5"),
                    main_net_inflow=Decimal("-300000000"),
                    sub_industry="集成电路制造",
                ),
                MemberItem(code="000001", name="没有市值的票"),
            ],
            distribution=[],
            suspended=2,
            sub_industries=[
                SubIndustryItem(
                    name="集成电路制造",
                    members=10,
                    market_cap=Decimal("1500000000000"),
                    weighted_change_pct=Decimal("1.5"),
                    main_net_inflow=Decimal("2000000000"),
                )
            ],
        ),
        valuation=ValuationBlock(
            section=valuation,
            pe_ttm=percentile,
            pb_mrq=None,
            loss_members=20,
            members=130,
            series=[],
            window_years=5,
        ),
        sources=[],
        rules=[],
        analytics_version="test",
    )


class FakeService:
    def __init__(self, detail: BoardDetail | Exception) -> None:
        self.result = detail
        self.asked: list[str] = []

    async def detail(self, code: str) -> BoardDetail:
        self.asked.append(code)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture(autouse=True)
def boards(monkeypatch: pytest.MonkeyPatch) -> None:
    install_boards(monkeypatch, detail_tools, {"半导体": [snapshot("BK1036", "半导体")]})


def _run(service: FakeService, arguments: str) -> ToolResult:
    tool = detail_tools.BoardDetailTool(cast(IndustryMarketService, service))
    return ToolRegistry([tool]).execute("board_detail", arguments, context()).result


def _by_item(result: ToolResult) -> dict[str, dict[str, Any]]:
    return {str(row["item"]): row for row in result.rows}


def test_summary_gives_position_valuation_percentile_and_excess_return() -> None:
    service = FakeService(_detail())
    result = _run(service, '{"board": "半导体"}')
    assert result.status == "ok" and service.asked == ["BK1036"]
    rows = _by_item(result)
    assert rows["年初至今涨跌幅"]["value"] == "35.2"
    assert (rows["成交额"]["value"], rows["成交额"]["unit"]) == ("888", "亿元")
    assert rows["当日主力净流入"]["value"] == "-12.35"  # -1234567890 元
    assert rows["市盈率PE(TTM)分位"]["value"] == "91.2"
    assert "不高于当前值" in str(rows["市盈率PE(TTM)分位"]["note"])
    assert rows["近20个交易日相对沪深300超额"]["value"] == "2.1"
    assert rows["近20个交易日相对沪深300超额"]["basis"] == "按本系统日线计算"
    assert rows["亏损成分股数"]["value"] == "20" and rows["成分股总数"]["value"] == "150"
    assert rows["市净率PB(MRQ)"]["note"] == "样本不足，没有分位"
    assert result.as_of == date(2026, 10, 21)


def test_members_are_ranked_by_market_cap_and_limited() -> None:
    result = _run(FakeService(_detail()), '{"board": "半导体", "view": "members", "top": 3}')
    assert [row["name"] for row in result.rows] == ["中芯国际", "韦尔股份", "没有市值的票"]
    assert result.rows[0]["market_cap"] == "5000"
    assert result.rows[1]["main_net_inflow"] == "1.5"
    assert result.rows[2]["market_cap"] is None  # 缺失就是缺失，不补零
    assert "成分股" in result.title


def test_sub_industries_view() -> None:
    result = _run(FakeService(_detail()), '{"board": "半导体", "view": "sub_industries"}')
    assert result.rows == (
        {
            "name": "集成电路制造",
            "members": "10",
            "market_cap": "15000",
            "weighted_change_pct": "1.5",
            "main_net_inflow": "20",
        },
    )


def test_missing_valuation_is_a_row_with_the_reason_not_a_made_up_number() -> None:
    reason = "东财只提供二级行业的估值分位"
    detail = _detail(valuation=Section(status="unavailable", reason=reason))
    result = _run(FakeService(detail), '{"board": "半导体"}')
    row = _by_item(result)["估值分位"]
    assert row["value"] is None and row["note"] == reason
    assert "市盈率PE(TTM)分位" not in _by_item(result)


def test_stored_snapshot_is_dated_and_marked_stale() -> None:
    stored = Section(
        status="stale",
        reason="东财实时接口暂时取不到，展示最近一次收盘快照",
        origin="stored",
        data_date=date(2026, 10, 20),
    )
    result = _run(FakeService(_detail(quote=stored)), '{"board": "半导体"}')
    assert result.status == "stale" and result.as_of == date(2026, 10, 20)
    assert "最近一次收盘快照" in (result.reason or "")


def test_members_view_without_members_explains_why() -> None:
    section = Section(status="unavailable", reason="成分股接口暂时取不到")
    result = _run(FakeService(_detail(members=section)), '{"board": "半导体", "view": "members"}')
    assert result.status == "unavailable" and result.reason == "成分股接口暂时取不到"


def test_a_past_date_is_refused_because_the_data_is_live_only() -> None:
    service = FakeService(_detail())
    result = _run(service, '{"board": "半导体", "as_of": "2026-10-10"}')
    assert result.status == "unavailable" and "只有实时数据" in (result.reason or "")
    assert service.asked == []  # 不拿今天的数据冒充那一天


def test_board_code_is_used_directly_and_unknown_names_are_reported() -> None:
    service = FakeService(_detail())
    assert _run(service, '{"board": "bk1036"}').status == "ok"
    assert service.asked == ["BK1036"]
    unknown = _run(service, '{"board": "量子玄学"}')
    assert unknown.status == "unavailable" and "industry_board" in (unknown.reason or "")
    assert service.asked == ["BK1036"]


def test_upstream_failure_is_reported_in_chinese_without_details() -> None:
    result = _run(FakeService(upstream_down("东财板块接口返回 500")), '{"board": "半导体"}')
    assert result.status == "unavailable"
    assert result.reason == "数据源暂时取不到：东财板块接口返回 500"


def test_a_missing_board_in_the_upstream_is_not_a_crash() -> None:
    missing = ProviderError(code="NOT_FOUND", message="没有这个板块", status_code=404)
    result = _run(FakeService(missing), '{"board": "BK9999"}')
    assert result.status == "unavailable" and "没有这个板块" in (result.reason or "")
