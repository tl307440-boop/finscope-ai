"""自定义数据分析 API：用假网关同步进内存仓库，再经真实路由读出来（不连库、不联网）。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import date

import pytest
from app.api.v1.custom_analysis import _service
from app.main import create_app
from app.services import custom_analysis_sync as sync
from app.services.custom_analysis import CustomAnalysisService
from fastapi.testclient import TestClient
from tests.unit.custom_analysis_fakes import (
    NOW,
    TODAY,
    MemoryStore,
    build_gateway,
    weekdays,
)

CALENDAR = weekdays(TODAY, 40)


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(seconds: float) -> None:
        return None

    monkeypatch.setattr(sync, "_pause", skip)
    monkeypatch.setattr(sync, "_throttle", skip)


def _client(store: MemoryStore, today: date = TODAY) -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[_service] = lambda: CustomAnalysisService(
        store, today=lambda: today
    )
    # 不进入 lifespan：这些测试不需要数据库连接。
    yield TestClient(application)


@pytest.fixture
def seeded_client() -> Iterator[TestClient]:
    store = MemoryStore()
    report = asyncio.run(sync.sync_custom_analysis(store, build_gateway(CALENDAR), NOW, years=1))
    assert report.failures == []
    yield from _client(store)


@pytest.fixture
def empty_client() -> Iterator[TestClient]:
    yield from _client(MemoryStore())


def test_pcr_endpoint_returns_both_baskets_for_a_group(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/v1/custom-analysis/pcr/csi300")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["group_code"] == "csi300"
    assert [basket["kind"] for basket in data["baskets"]] == ["etf", "index"]
    etf = data["baskets"][0]
    assert etf["status"] == "ok"
    assert etf["latest"]["trade_date"] == TODAY.isoformat()
    # SSE 510300（认沽 400 / 认购 600）+ SZSE 159919（认沽 100 / 认购 200）→ 500 / 800
    assert (etf["latest"]["put_volume"], etf["latest"]["call_volume"]) == (500, 800)
    assert etf["latest"]["volume_pcr"] == 0.625
    assert len(etf["points"]) == len(CALENDAR)
    # 样本远不足 750 个交易日：不给分位，也不给相对信号
    assert "volume_pcr_percentile_5y" not in etf["latest"]


def test_pcr_star50_is_sse_etf_only_and_csv_has_no_index_kind(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/v1/custom-analysis/pcr/star50")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["group_name"] == "科创50"
    assert [basket["kind"] for basket in data["baskets"]] == ["etf"]
    latest = data["baskets"][0]["latest"]
    # SSE 588000 + 588080 各认沽 400 / 认购 600 → 800 / 1200
    assert (latest["put_volume"], latest["call_volume"]) == (800, 1200)
    assert latest["index_close"] is not None
    ok = seeded_client.get("/api/v1/custom-analysis/export/pcr/star50.csv", params={"kind": "etf"})
    assert ok.status_code == 200
    missing = seeded_client.get(
        "/api/v1/custom-analysis/export/pcr/star50.csv", params={"kind": "index"}
    )
    assert missing.status_code == 404


def test_pcr_days_parameter_limits_the_series(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/v1/custom-analysis/pcr/sse50", params={"days": 20})

    assert response.status_code == 200
    assert len(response.json()["data"]["baskets"][0]["points"]) == 20


def test_unknown_group_is_a_404_and_bad_days_are_rejected(seeded_client: TestClient) -> None:
    assert seeded_client.get("/api/v1/custom-analysis/pcr/nasdaq").status_code == 404
    assert (
        seeded_client.get("/api/v1/custom-analysis/pcr/csi300", params={"days": 5}).status_code
        == 422
    )
    assert seeded_client.get("/api/v1/custom-analysis/pcr/BAD-CODE").status_code == 422


def test_empty_database_reports_unavailable_instead_of_inventing_numbers(
    empty_client: TestClient,
) -> None:
    pcr = empty_client.get("/api/v1/custom-analysis/pcr/csi300").json()["data"]
    assert [basket["status"] for basket in pcr["baskets"]] == ["unavailable", "unavailable"]
    assert all("latest" not in basket and basket["points"] == [] for basket in pcr["baskets"])

    valuation = empty_client.get("/api/v1/custom-analysis/valuation").json()["data"]
    assert [item["code"] for item in valuation["indicators"]] == ["erp", "buffett", "median_pb"]
    assert all(
        item["status"] == "unavailable" and item["reason"] for item in valuation["indicators"]
    )

    sources = empty_client.get("/api/v1/custom-analysis/sources").json()["data"]
    assert sources["recent_issues"] == []
    assert all(row["status"] == "unavailable" for row in sources["sources"])


def test_valuation_endpoint_computes_all_three_indicators(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/v1/custom-analysis/valuation").json()["data"]

    by_code = {item["code"]: item for item in data["indicators"]}
    erp = by_code["erp"]
    assert erp["status"] == "ok"
    # 最后一天：PE = 12.5 + 39 * 0.01 = 12.89，收益率 1.65% → 100 / 12.89 − 1.65 ≈ 6.108
    assert erp["latest"]["value"] == pytest.approx(100 / 12.89 - 1.65, abs=1e-3)
    assert by_code["median_pb"]["status"] == "ok"
    assert by_code["median_pb"]["latest"]["extras"]["st_basis"] == "snapshot_name"
    # 样本只有 40 天：分位 / 通道不给，并说明原因
    assert "percentile_5y" not in erp["latest"]
    assert "不足 750" in erp["percentile_note"]


def test_sources_endpoint_lists_every_provider_with_latest_dates(seeded_client: TestClient) -> None:
    data = seeded_client.get("/api/v1/custom-analysis/sources").json()["data"]

    keys = {row["key"] for row in data["sources"]}
    assert {"SSE", "SZSE", "CFFEX", "CN_10Y_YIELD", "A_MEDIAN_PB"} <= keys
    sse = next(row for row in data["sources"] if row["key"] == "SSE")
    assert sse["status"] == "ok"
    assert sse["last_date"] == TODAY.isoformat()
    # 510050 / 510300 / 510500 / 588000 / 588080 每个交易日各一行
    assert sse["rows"] == len(CALENDAR) * 5


def test_stale_data_is_flagged_not_hidden() -> None:
    store = MemoryStore()
    asyncio.run(sync.sync_custom_analysis(store, build_gateway(CALENDAR), NOW, years=1))
    far_future = date(2026, 12, 31)
    client = next(_client(store, far_future))

    data = client.get("/api/v1/custom-analysis/pcr/csi300").json()["data"]
    etf = data["baskets"][0]

    assert etf["status"] == "stale"
    assert "过期" in etf["reason"]
    assert etf["latest"]["trade_date"] == TODAY.isoformat()  # 仍给出最后一个真实值


def test_pcr_csv_export_uses_the_prd_columns(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/api/v1/custom-analysis/export/pcr/csi300.csv", params={"kind": "etf"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "pcr_monitor_csi300_etf.csv" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert len(lines) == len(CALENDAR) + 1
    assert "volume_pcr" in lines[0]


def test_csv_export_without_data_is_header_only_and_missing_basket_kind_is_404(
    empty_client: TestClient,
) -> None:
    empty = empty_client.get(
        "/api/v1/custom-analysis/export/pcr/csi300.csv", params={"kind": "etf"}
    )
    assert empty.status_code == 200
    assert len(empty.text.strip().splitlines()) == 1  # 只有表头，不编数据行

    # 中证1000 没有 ETF 期权（只有中金所 MO），要 ETF 篮子就是不存在
    missing = empty_client.get(
        "/api/v1/custom-analysis/export/pcr/csi1000.csv", params={"kind": "etf"}
    )
    assert missing.status_code == 404


def test_valuation_csv_export(seeded_client: TestClient) -> None:
    response = seeded_client.get("/api/v1/custom-analysis/export/valuation.csv")

    assert response.status_code == 200
    assert "macro_valuation_summary.csv" in response.headers["content-disposition"]
    assert len(response.text.strip().splitlines()) > 1
