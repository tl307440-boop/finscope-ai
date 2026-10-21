"""研报索引工具 `research_reports`：行业 / 个股、时点、部分失败、查不到。假网关，不碰网络。"""

from datetime import date
from typing import Any

import pytest
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools import reports as report_tools
from app.services.market_cache import TtlCache
from app.services.research_reports import ResearchReportService

from live_fakes import (
    ScriptedGateway,
    as_json,
    context,
    install_boards,
    snapshot,
    upstream_down,
)

INDUSTRY = "qType=1"
STOCK = "qType=0"


def _report(info_code: str, day: str, **extra: Any) -> dict[str, Any]:
    return {
        "infoCode": info_code,
        "title": f"研报 {info_code}",
        "orgSName": "中信证券",
        "publishDate": f"{day} 00:00:00.000",
        "emRatingName": "买入",
        "researcher": "张三",
        **extra,
    }


def _run(gateway: ScriptedGateway, arguments: str) -> ToolResult:
    service = ResearchReportService(gateway=gateway, cache=TtlCache())
    tool = report_tools.ResearchReportsTool(service)
    return ToolRegistry([tool]).execute("research_reports", arguments, context()).result


@pytest.fixture(autouse=True)
def boards(monkeypatch: pytest.MonkeyPatch) -> None:
    install_boards(
        monkeypatch,
        report_tools,
        {
            "半导体": [
                snapshot("BK1036", "半导体", level=1),
                snapshot("BK0481", "半导体设备", level=2),
            ]
        },
    )


def test_industry_reports_are_listed_newest_first_with_links() -> None:
    gateway = ScriptedGateway(
        {INDUSTRY: as_json({"data": [_report("A1", "2026-10-15"), _report("A2", "2026-10-20")]})}
    )
    result = _run(gateway, '{"industry": "半导体"}')
    assert result.status == "ok"
    assert [row["published"] for row in result.rows] == ["2026-10-20", "2026-10-15"]
    first = result.rows[0]
    assert (first["org"], first["rating"], first["researcher"]) == ("中信证券", "买入", "张三")
    assert first["url"] == "https://data.eastmoney.com/report/zw_industry.jshtml?infocode=A2"
    assert result.as_of == date(2026, 10, 20)
    assert result.title == "行业研报：半导体"


def test_second_level_industry_is_queried_first_and_by_board_number() -> None:
    gateway = ScriptedGateway({INDUSTRY: as_json({"data": [_report("A1", "2026-10-20")]})})
    _run(gateway, '{"industry": "半导体"}')
    assert "industryCode=481" in gateway.urls[0]  # 东财研报按二级行业归档：BK0481 排在 BK1036 前
    assert "industryCode=1036" in gateway.urls[1]


def test_reports_after_the_as_of_date_are_never_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    # 时点：10-10 的板块名称只能来自 10-10 或更早入库的快照
    early = snapshot("BK0481", "半导体设备", level=2, day=date(2026, 10, 1))
    repo = install_boards(monkeypatch, report_tools, {"半导体": [early]})
    payload = as_json({"data": [_report("OLD", "2026-10-08"), _report("NEW", "2026-10-12")]})
    gateway = ScriptedGateway({INDUSTRY: payload})
    result = _run(gateway, '{"industry": "半导体", "as_of": "2026-10-10", "days": 30}')
    assert [row["title"] for row in result.rows] == ["研报 OLD"]
    assert "endTime=2026-10-10" in gateway.urls[0] and "beginTime=2026-09-10" in gateway.urls[0]
    assert repo.asked[0][1] == date(2026, 10, 10)


def test_stock_reports_show_the_company_and_use_the_stock_query() -> None:
    row = _report("S1", "2026-10-19", stockCode="688981", stockName="中芯国际")
    gateway = ScriptedGateway({STOCK: as_json({"data": [row]})})
    result = _run(gateway, '{"stock_code": "688981"}')
    assert result.rows[0]["scope"] == "中芯国际（688981）"
    assert "code=688981" in gateway.urls[0]
    assert result.rows[0]["url"].endswith("zw_stock.jshtml?infocode=S1")


def test_a_failing_board_is_reported_but_the_others_still_count() -> None:
    gateway = ScriptedGateway(
        {
            "industryCode=481": upstream_down("东财研报接口返回 500"),
            "industryCode=1036": as_json({"data": [_report("A1", "2026-10-20")]}),
        }
    )
    result = _run(gateway, '{"industry": "半导体"}')
    assert result.status == "stale" and len(result.rows) == 1
    assert "半导体设备" in (result.reason or "") and "500" in (result.reason or "")


def test_every_board_failing_is_unavailable_with_the_upstream_reason() -> None:
    gateway = ScriptedGateway({INDUSTRY: upstream_down("东财研报接口返回 500")})
    result = _run(gateway, '{"industry": "半导体"}')
    assert result.status == "unavailable" and "500" in (result.reason or "")


def test_no_reports_in_the_window_is_unavailable_with_a_hint() -> None:
    result = _run(ScriptedGateway({INDUSTRY: as_json({"data": []})}), '{"industry": "半导体"}')
    assert result.status == "unavailable"
    assert "最近 60 天没有查到研报" in (result.reason or "")
    assert "二级行业" in (result.reason or "")


def test_unknown_industry_name_never_reaches_the_network() -> None:
    gateway = ScriptedGateway({})
    result = _run(gateway, '{"industry": "量子玄学"}')
    assert result.status == "unavailable" and "没有名称包含「量子玄学」" in (result.reason or "")
    assert gateway.urls == []


@pytest.mark.parametrize(
    "arguments",
    [
        "{}",
        '{"industry": "半导体", "stock_code": "688981"}',
        '{"stock_code": "68898"}',
        '{"industry": "   "}',
        '{"industry": "半导体", "days": 5}',
    ],
)
def test_bad_arguments_are_rejected_before_any_request(arguments: str) -> None:
    gateway = ScriptedGateway({})
    result = _run(gateway, arguments)
    assert result.status == "unavailable" and "参数不合法" in (result.reason or "")
    assert gateway.urls == []
