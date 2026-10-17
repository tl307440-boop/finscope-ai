# 美国宏观数据扩充与指标带滑动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/macro/us` 顶部指标带换成 15 个美国宏观指标、可横向滑动并按参考图着色；后端补齐 8 条 FRED 序列与东方财富 ISM 两条 PMI，并提供批量观测接口。

**Architecture:** 新序列并入现有宏观目录（`MacroSeriesSeed` 增加 `provider` / `source_key`），东方财富 ISM 经纯函数换成版本行后复用 `upsert_vintages` 入库，同步沿用 `fred_series` 任务并在 `sync_fred_series` 内按 provider 分派。前端口径换算集中在纯函数模块 `api/macroTiles.ts`，指标带拆成 `MacroTicker`（滑动容器）+ `MacroTickerTile`（单格），页面一次调用批量接口取数。

**Tech Stack:** FastAPI · Pydantic v2 · SQLAlchemy 2 · pytest · Vue 3 `<script setup>` + TypeScript strict · vitest + @testing-library/vue · ECharts

**Spec:** `docs/superpowers/specs/2026-10-17-macro-us-ticker-design.md`

## Global Constraints

- 工作目录：工作树 `d:\finscrope\finscope-ai-macro-ticker`，分支 `feat/macro-us-ticker`。不要动主目录 `d:\finscrope\finscope-ai` 的未提交改动。
- Python 解释器：本机没有 `uv`，统一用 `$py = "d:\finscrope\finscope-ai\backend\.venv\Scripts\python.exe"`，且必须在工作树的 `backend` 目录下执行（已验证此时导入的是工作树的 `app`）。
- 不编造数据（AGENTS.md）：缺数据显示「—」/「暂无数据」，不插值、不补点、不写演示数字。
- 时点规则：`realtime_start < pit <= realtime_end` 才可见，发布日当天不可见；开放版本 `realtime_end = 9999-12-31`。
- 代码与字段英文命名，UI 文案与注释中文；接口前缀 `/api/v1`；错误统一 `AppError` / `ProviderError` 结构化返回。
- `base_unit` / `unit` 列 `String(32)`，`seasonal_adjustment` `String(32)`，`frequency` `String(16)`。
- 指标带配色：上升 `--rise`、下降 `--fall`、持平 / 无数据 `--muted`。
- 批量接口单次最多 20 个代码；limit：Daily 1000、Weekly 260、其他 120。
- 提交信息用中文短句，格式与仓库历史一致；不要 `git add .env`。

## File Structure

后端：

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/app/catalog/macro_series.py` | 改 | 19 条序列定义；`fred_codes()`、`seed_for()` |
| `backend/app/providers/fred.py`、`fred_public.py` | 改 | 只接受 FRED 序列 |
| `backend/app/providers/eastmoney_us_macro.py` | 新 | 东方财富美国经济报表 URL / 解析 / GET |
| `backend/app/providers/latest_revisions.py` | 新 | 纯函数：只有最新值的来源 → 版本行 |
| `backend/app/repositories/macro.py` | 改 | 多 provider 目录、`upsert_latest_values`、`list_vintages_many` |
| `backend/app/services/fred_sync.py` | 改 | 按 provider 分派 FRED / 东方财富 |
| `backend/app/services/macro_batch.py` | 新 | 批量观测：代码校验、limit、组装 |
| `backend/app/schemas/macro.py` | 改 | 目录项加 provider / source_key；`ObservationBatch`；同步请求正则 |
| `backend/app/api/v1/macro.py` | 改 | `GET /macro/observations` |
| `backend/app/core/config.py` | 改 | 定时同步默认序列 |
| `backend/app/scripts/bootstrap_data.py` | 改 | 打印文案 |
| `backend/tests/unit/test_fred.py` | 改 | 目录与分派用例 |
| `backend/tests/unit/test_eastmoney_us_macro.py` | 新 | 解析与版本规划 |
| `backend/tests/unit/test_macro_batch.py` | 新 | 批量服务 |
| `backend/tests/integration/test_us_macro_eastmoney.py` | 新 | 入库修订与批量接口（需 PostgreSQL，缺库自动 skip） |

前端：

| 文件 | 动作 | 职责 |
|---|---|---|
| `frontend/src/api/macroTiles.ts` | 新 | 15 格定义与口径换算（纯函数） |
| `frontend/src/api/macro.ts` | 改 | `unitLabel` 认新单位 |
| `frontend/src/views/parts/macro/MacroTickerTile.vue` | 新 | 单格 |
| `frontend/src/views/parts/macro/MacroTicker.vue` | 新 | 滑动容器 |
| `frontend/src/views/MacroView.vue` | 改 | 接入指标带、批量取数、走势图选项 |
| `frontend/e2e/core.spec.ts` | 改 | 用例 04 等批量接口 |
| `frontend/tests/unit/macroTiles.test.ts` | 新 | 口径用例 |
| `frontend/tests/unit/macroTicker.test.ts` | 新 | 组件用例 |
| `frontend/tests/unit/macro.test.ts` | 改 | 新单位 |

文档：`docs/API.md`、`docs/IMPLEMENTATION_LOG.md`。

---

### Task 1: 序列目录扩充与 FRED 守卫

**Files:**
- Modify: `backend/app/catalog/macro_series.py`（整文件替换）
- Modify: `backend/app/providers/fred.py:9,97`
- Modify: `backend/app/providers/fred_public.py:13,91`
- Test: `backend/tests/unit/test_fred.py`

**Interfaces:**
- Produces:
  - `MacroProvider = Literal["fred", "eastmoney"]`
  - `MacroSeriesSeed(..., provider: MacroProvider = "fred", source_key: str = "")`
  - `fred_series_catalog() -> tuple[MacroSeriesSeed, ...]`（19 条）
  - `catalog_codes() -> frozenset[str]`（全部）、`fred_codes() -> frozenset[str]`（仅 FRED）、`seed_for(series_code: str) -> MacroSeriesSeed | None`

- [ ] **Step 1: 改写目录测试（先失败）**

在 `backend/tests/unit/test_fred.py` 中把 `test_catalog_keeps_verified_units_and_no_observations` 整个函数替换为：

```python
def test_catalog_keeps_verified_units_and_no_observations() -> None:
    seeds = {item.series_code: item for item in fred_series_catalog()}
    assert set(seeds) == {
        "GDP",
        "UNRATE",
        "PAYEMS",
        "CPIAUCSL",
        "PCEPILFE",
        "FEDFUNDS",
        "DGS2",
        "DGS10",
        "T10Y2Y",
        "CES0500000003",
        "GDPC1",
        "PCEC96",
        "DGS5",
        "DGS20",
        "DFEDTARL",
        "DFEDTARU",
        "ICSA",
        "ISM_MFG",
        "ISM_SVC",
    }
    assert seeds["GDP"].frequency == "Quarterly"
    assert seeds["GDP"].base_unit == "Billions of Dollars"
    assert seeds["GDP"].seasonal_adjustment == "Seasonally Adjusted Annual Rate"
    assert seeds["UNRATE"].frequency == "Monthly"
    assert seeds["UNRATE"].base_unit == "Percent"
    assert seeds["UNRATE"].seasonal_adjustment == "Seasonally Adjusted"
    assert seeds["PAYEMS"].base_unit == "Thousands of Persons"
    assert seeds["CPIAUCSL"].base_unit == "Index 1982-1984=100"
    assert seeds["PCEPILFE"].base_unit == "Index 2017=100"
    assert seeds["FEDFUNDS"].seasonal_adjustment == "Not Seasonally Adjusted"
    assert seeds["DGS10"].frequency == "Daily"
    assert seeds["T10Y2Y"].frequency == "Daily"
    assert seeds["CES0500000003"].base_unit == "Dollars per Hour"
    assert seeds["GDPC1"].base_unit == "Billions of Chained 2017 Dollars"
    assert seeds["GDPC1"].frequency == "Quarterly"
    assert seeds["PCEC96"].frequency == "Monthly"
    assert seeds["ICSA"].frequency == "Weekly"
    assert seeds["ICSA"].base_unit == "Number"
    assert seeds["DFEDTARL"].frequency == "Daily"
    assert seeds["ISM_MFG"].provider == "eastmoney"
    assert seeds["ISM_MFG"].source_key == "EMG00002790"
    assert seeds["ISM_SVC"].source_key == "EMG00002791"
    for seed in seeds.values():
        assert len(seed.name_en) <= 128
        assert len(seed.name_cn) <= 128
        assert len(seed.base_unit) <= 32
        assert len(seed.seasonal_adjustment) <= 32
        assert len(seed.frequency) <= 16
        assert len(seed.series_code) <= 32
        assert (seed.provider == "eastmoney") == bool(seed.source_key)


def test_fred_codes_exclude_eastmoney_series() -> None:
    assert "ISM_MFG" not in fred_codes()
    assert "ISM_SVC" not in fred_codes()
    assert "GDPC1" in fred_codes()
    assert {"ISM_MFG", "ISM_SVC"} <= catalog_codes()
    found = seed_for("ISM_SVC")
    assert found is not None and found.provider == "eastmoney"
    assert seed_for("NOT_A_SERIES") is None


def test_fred_adapters_refuse_eastmoney_series_before_any_request() -> None:
    transport = ScriptTransport(b"{}")
    client = ResilientHttpClient(transport, sleeper=_sleep, max_attempts=1)

    async def keyed() -> None:
        await FredAdapter(client, "k").fetch_vintages(
            "ISM_MFG", date(2026, 1, 1), date(2026, 2, 1)
        )

    async def public() -> None:
        await fetch_public_csv(
            client,
            "ISM_MFG",
            date(2026, 1, 1),
            date(2026, 2, 1),
            fetched_at=datetime(2026, 10, 16, 6, 0, tzinfo=UTC),
        )

    for action in (keyed, public):
        with pytest.raises(ProviderError) as caught:
            asyncio.run(action())
        assert caught.value.code == "VALIDATION_ERROR"
    assert transport.calls == 0
```

并把文件顶部的导入

```python
from app.catalog.macro_series import fred_series_catalog
```

改为

```python
from app.catalog.macro_series import catalog_codes, fred_codes, fred_series_catalog, seed_for
```

- [ ] **Step 2: 运行，确认失败**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker\backend
$py = "d:\finscrope\finscope-ai\backend\.venv\Scripts\python.exe"
& $py -m pytest tests/unit/test_fred.py -q
```

Expected: 收集阶段 `ImportError: cannot import name 'fred_codes'`。

- [ ] **Step 3: 整文件替换 `backend/app/catalog/macro_series.py`**

```python
from dataclasses import dataclass
from typing import Literal

MacroProvider = Literal["fred", "eastmoney"]


@dataclass(frozen=True)
class MacroSeriesSeed:
    series_code: str
    name_cn: str
    name_en: str
    category: str
    frequency: str
    base_unit: str
    seasonal_adjustment: str
    source_agency: str
    provider: MacroProvider = "fred"
    # 东方财富指标 ID；FRED 序列留空（序列代码即 FRED ID）。
    source_key: str = ""


_FED_BOARD = "Board of Governors of the Federal Reserve System (US)"
_ISM_VIA_EASTMONEY = "Institute for Supply Management (via Eastmoney)"


def fred_series_catalog() -> tuple[MacroSeriesSeed, ...]:
    """序列定义来自 FRED 系列页与东方财富数据中心报表。这里没有观测值。"""

    return (
        MacroSeriesSeed(
            series_code="GDP",
            name_cn="美国国内生产总值",
            name_en="Gross Domestic Product",
            category="output",
            frequency="Quarterly",
            base_unit="Billions of Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="UNRATE",
            name_cn="美国失业率",
            name_en="Unemployment Rate",
            category="labor",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="PAYEMS",
            name_cn="非农就业人数",
            name_en="All Employees, Total Nonfarm",
            category="labor",
            frequency="Monthly",
            base_unit="Thousands of Persons",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="CPIAUCSL",
            name_cn="美国消费者物价指数",
            name_en=(
                "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average"
            ),
            category="inflation",
            frequency="Monthly",
            base_unit="Index 1982-1984=100",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="PCEPILFE",
            name_cn="核心个人消费支出价格指数",
            name_en=(
                "Personal Consumption Expenditures Excluding Food and Energy "
                "(Chain-Type Price Index)"
            ),
            category="inflation",
            frequency="Monthly",
            base_unit="Index 2017=100",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="FEDFUNDS",
            name_cn="联邦基金有效利率",
            name_en="Federal Funds Effective Rate",
            category="rates",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS2",
            name_cn="美国2年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 2-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS10",
            name_cn="美国10年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="T10Y2Y",
            name_cn="10年与2年期国债利差",
            name_en="10-Year Treasury Constant Maturity Minus 2-Year Treasury Constant Maturity",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="Federal Reserve Bank of St. Louis",
        ),
        MacroSeriesSeed(
            series_code="CES0500000003",
            name_cn="平均时薪（私营非农）",
            name_en="Average Hourly Earnings of All Employees, Total Private",
            category="labor",
            frequency="Monthly",
            base_unit="Dollars per Hour",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="GDPC1",
            name_cn="实际国内生产总值",
            name_en="Real Gross Domestic Product",
            category="output",
            frequency="Quarterly",
            base_unit="Billions of Chained 2017 Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="PCEC96",
            name_cn="实际个人消费支出",
            name_en="Real Personal Consumption Expenditures",
            category="consumption",
            frequency="Monthly",
            base_unit="Billions of Chained 2017 Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="DGS5",
            name_cn="美国5年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 5-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS20",
            name_cn="美国20年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 20-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DFEDTARL",
            name_cn="联邦基金目标利率下限",
            name_en="Federal Funds Target Range - Lower Limit",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DFEDTARU",
            name_cn="联邦基金目标利率上限",
            name_en="Federal Funds Target Range - Upper Limit",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="ICSA",
            name_cn="初请失业金人数",
            name_en="Initial Claims",
            category="labor",
            frequency="Weekly",
            base_unit="Number",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Employment and Training Administration",
        ),
        MacroSeriesSeed(
            series_code="ISM_MFG",
            name_cn="ISM 制造业 PMI",
            name_en="ISM Manufacturing PMI",
            category="sentiment",
            frequency="Monthly",
            base_unit="Index",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency=_ISM_VIA_EASTMONEY,
            provider="eastmoney",
            source_key="EMG00002790",
        ),
        MacroSeriesSeed(
            series_code="ISM_SVC",
            name_cn="ISM 服务业 PMI",
            name_en="ISM Services PMI",
            category="sentiment",
            frequency="Monthly",
            base_unit="Index",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency=_ISM_VIA_EASTMONEY,
            provider="eastmoney",
            source_key="EMG00002791",
        ),
    )


def catalog_codes() -> frozenset[str]:
    return frozenset(seed.series_code for seed in fred_series_catalog())


def fred_codes() -> frozenset[str]:
    """只发往 FRED 的序列；东方财富序列走自己的 provider。"""

    return frozenset(seed.series_code for seed in fred_series_catalog() if seed.provider == "fred")


def seed_for(series_code: str) -> MacroSeriesSeed | None:
    for seed in fred_series_catalog():
        if seed.series_code == series_code:
            return seed
    return None
```

- [ ] **Step 4: FRED 适配器只认 FRED 序列**

`backend/app/providers/fred.py`：第 9 行 `from app.catalog.macro_series import catalog_codes` 改为 `from app.catalog.macro_series import fred_codes`；第 97 行 `if series_id not in catalog_codes():` 改为 `if series_id not in fred_codes():`。

`backend/app/providers/fred_public.py`：第 13 行同样改导入为 `fred_codes`；第 91 行 `if series_id not in catalog_codes():` 改为 `if series_id not in fred_codes():`。

- [ ] **Step 5: 运行，确认通过**

```powershell
& $py -m pytest tests/unit/test_fred.py -q
```

Expected: 全部 PASS（原 13 条 + 新 2 条）。

- [ ] **Step 6: 提交**

```powershell
git add backend/app/catalog/macro_series.py backend/app/providers/fred.py backend/app/providers/fred_public.py backend/tests/unit/test_fred.py
git commit -m "宏观目录新增 8 条 FRED 序列与东方财富 ISM 两条 PMI，FRED 适配器只认 FRED 序列"
```

---

### Task 2: 东方财富 ISM provider 与版本规划纯函数

**Files:**
- Create: `backend/app/providers/eastmoney_us_macro.py`
- Create: `backend/app/providers/latest_revisions.py`
- Test: `backend/tests/unit/test_eastmoney_us_macro.py`

**Interfaces:**
- Consumes: `FredVintage(observation_date, value, realtime_start, realtime_end)`（`app/providers/fred.py`）、`VintagePoint`（`app/providers/vintage.py`）、`ResilientHttpClient.get(url, headers=...) -> bytes`
- Produces:
  - `LatestValue(observation_date: date, published_on: date, value: Decimal)`（frozen dataclass）
  - `indicator_url(indicator_id: str) -> str`
  - `parse_indicator(payload: bytes, indicator_id: str) -> tuple[LatestValue, ...]`（按观测月升序）
  - `async fetch_indicator(client: ResilientHttpClient, indicator_id: str) -> tuple[LatestValue, ...]`
  - `OPEN_ENDED = date(9999, 12, 31)`
  - `plan_latest_revisions(existing: Sequence[VintagePoint], fresh: Sequence[LatestValue], *, fetched_on: date) -> tuple[FredVintage, ...]`

- [ ] **Step 1: 写失败测试 `backend/tests/unit/test_eastmoney_us_macro.py`**

```python
import json
from datetime import date
from decimal import Decimal

import pytest
from app.providers.eastmoney_us_macro import LatestValue, indicator_url, parse_indicator
from app.providers.errors import ProviderError
from app.providers.fred import FredVintage
from app.providers.latest_revisions import OPEN_ENDED, plan_latest_revisions
from app.providers.vintage import VintagePoint

MFG = "EMG00002790"


def _payload(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"success": True, "message": "ok", "result": {"count": len(rows), "data": rows}}
    ).encode()


def _row(report: str, publish: str, value: object, indicator: str = MFG) -> dict[str, object]:
    return {
        "INDICATOR_ID": indicator,
        "REPORT_DATE": f"{report} 00:00:00",
        "PUBLISH_DATE": f"{publish} 00:00:00",
        "VALUE": value,
    }


def test_url_filters_one_indicator_of_the_us_report() -> None:
    url = indicator_url(MFG)
    assert "reportName=RPT_ECONOMICVALUE_USA" in url
    assert "EMG00002790" in url
    assert "PUBLISH_DATE" in url


def test_parse_keeps_published_values_in_month_order() -> None:
    rows = parse_indicator(
        _payload(
            [
                _row("2026-09-01", "2026-10-01", 54.5),
                _row("2026-08-01", "2026-09-01", 54.6),
            ]
        ),
        MFG,
    )
    assert rows == (
        LatestValue(date(2026, 8, 1), date(2026, 9, 1), Decimal("54.6")),
        LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.5")),
    )


def test_parse_skips_calendar_placeholders_and_other_indicators() -> None:
    rows = parse_indicator(
        _payload(
            [
                _row("2026-10-01", "2026-11-02", None),
                _row("2026-09-01", "2026-10-05", 54.9, indicator="EMG00002791"),
                _row("2026-09-01", "2026-10-01", "54.5"),
                {"INDICATOR_ID": MFG, "REPORT_DATE": None, "PUBLISH_DATE": None, "VALUE": 1},
            ]
        ),
        MFG,
    )
    assert [row.value for row in rows] == [Decimal("54.5")]


@pytest.mark.parametrize(
    "payload",
    [
        b"<html>blocked</html>",
        json.dumps({"success": False, "message": "返回数据为空", "result": None}).encode(),
        json.dumps({"success": True, "result": {"data": "oops"}}).encode(),
    ],
)
def test_parse_rejects_bad_payloads_without_echoing_them(payload: bytes) -> None:
    with pytest.raises(ProviderError) as caught:
        parse_indicator(payload, MFG)
    assert caught.value.code == "UPSTREAM_PAYLOAD"
    assert "blocked" not in caught.value.message


def _held(value: str, start: date, end: date = OPEN_ENDED) -> VintagePoint:
    return VintagePoint(
        observation_date=date(2026, 9, 1),
        value=Decimal(value),
        unit="Index",
        realtime_start=start,
        realtime_end=end,
        revision_no=1,
    )


FRESH = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.5"))


def test_first_sight_opens_a_version_from_the_publish_day() -> None:
    assert plan_latest_revisions([], [FRESH], fetched_on=date(2026, 10, 17)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.5"),
            realtime_start=date(2026, 10, 1),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_same_value_writes_nothing() -> None:
    held = _held("54.500000", date(2026, 10, 1))
    assert plan_latest_revisions([held], [FRESH], fetched_on=date(2026, 10, 17)) == ()


def test_changed_value_closes_old_version_and_opens_new_one() -> None:
    held = _held("54.5", date(2026, 10, 1))
    revised = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.2"))
    assert plan_latest_revisions([held], [revised], fetched_on=date(2027, 1, 10)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.5"),
            realtime_start=date(2026, 10, 1),
            realtime_end=date(2027, 1, 9),
        ),
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.2"),
            realtime_start=date(2027, 1, 10),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_change_on_or_before_the_open_day_overwrites_in_place() -> None:
    held = _held("54.5", date(2026, 10, 1))
    revised = LatestValue(date(2026, 9, 1), date(2026, 10, 1), Decimal("54.4"))
    assert plan_latest_revisions([held], [revised], fetched_on=date(2026, 10, 1)) == (
        FredVintage(
            observation_date=date(2026, 9, 1),
            value=Decimal("54.4"),
            realtime_start=date(2026, 10, 1),
            realtime_end=OPEN_ENDED,
        ),
    )


def test_closed_versions_are_not_treated_as_current() -> None:
    closed = _held("53.0", date(2026, 10, 1), date(2026, 10, 9))
    current = _held("54.5", date(2026, 10, 10))
    assert plan_latest_revisions([closed, current], [FRESH], fetched_on=date(2026, 10, 17)) == ()
```

- [ ] **Step 2: 运行，确认失败**

```powershell
& $py -m pytest tests/unit/test_eastmoney_us_macro.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.providers.eastmoney_us_macro'`。

- [ ] **Step 3: 新建 `backend/app/providers/eastmoney_us_macro.py`**

```python
"""东方财富数据中心「美国经济数据」报表：目前只取 ISM 制造业 / 服务业 PMI。

只做 URL 构造、解析与一次 GET。
报表 `RPT_ECONOMICVALUE_USA` 每行是一个指标的一个所属月（2026-10 实测）：

- `REPORT_DATE` 所属月首日；`PUBLISH_DATE` 美国当地发布日；`VALUE` 公布值。
- 财经日历会预排下一期：`VALUE` 为 null、`PUBLISH_DATE` 在未来，跳过。
- 只给当前值、不给修订历史；版本由 `latest_revisions` 自首次同步起记录。
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_ECONOMICVALUE_USA"
HEADERS = {"Referer": "https://data.eastmoney.com/"}
PAGE_SIZE = 300  # 2008-01 起约 226 行


@dataclass(frozen=True)
class LatestValue:
    observation_date: date
    published_on: date
    value: Decimal


def indicator_url(indicator_id: str) -> str:
    query = urlencode(
        {
            "reportName": REPORT_NAME,
            "columns": "INDICATOR_ID,REPORT_DATE,PUBLISH_DATE,VALUE",
            "filter": f'(INDICATOR_ID="{indicator_id}")',
            "pageNumber": 1,
            "pageSize": PAGE_SIZE,
            "sortColumns": "REPORT_DATE",
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        }
    )
    return f"{DATACENTER_URL}?{query}"


def parse_indicator(payload: bytes, indicator_id: str) -> tuple[LatestValue, ...]:
    try:
        body = json.loads(payload)
    except ValueError as exc:
        raise _bad(indicator_id, "返回的不是 JSON") from exc
    if not isinstance(body, dict) or body.get("success") is not True:
        raise _bad(indicator_id, "没有给出数据")
    result = body.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise _bad(indicator_id, "缺少数据列表")
    points: list[LatestValue] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("INDICATOR_ID") != indicator_id:
            continue
        observed = _day(row.get("REPORT_DATE"))
        published = _day(row.get("PUBLISH_DATE"))
        value = _decimal(row.get("VALUE"))
        if observed is None or published is None or value is None:
            continue
        points.append(LatestValue(observed.replace(day=1), published, value))
    return tuple(sorted(points, key=lambda item: item.observation_date))


async def fetch_indicator(
    client: ResilientHttpClient, indicator_id: str
) -> tuple[LatestValue, ...]:
    return parse_indicator(await client.get(indicator_url(indicator_id), headers=HEADERS), indicator_id)


def _bad(indicator_id: str, reason: str) -> ProviderError:
    return ProviderError(
        code="UPSTREAM_PAYLOAD", message=f"东方财富 {indicator_id} {reason}", status_code=502
    )


def _day(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _decimal(raw: object) -> Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() else None
```

- [ ] **Step 4: 新建 `backend/app/providers/latest_revisions.py`**

```python
"""只给最新值的来源 → 版本行。纯函数，无副作用，可离线测试。

来源本身没有修订历史，所以：
- 第一次见到某观测期：从发布日开始的开放版本；
- 之后同值：不写；
- 值变了：旧版本在抓取日前一天截止，新版本从抓取日开始；
  抓取日不晚于旧版本起点时直接改写旧版本。
首次同步之前的历史只能是来源的最新修订值。
"""

from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from app.providers.eastmoney_us_macro import LatestValue
from app.providers.fred import FredVintage
from app.providers.vintage import VintagePoint

OPEN_ENDED = date(9999, 12, 31)


def plan_latest_revisions(
    existing: Sequence[VintagePoint],
    fresh: Sequence[LatestValue],
    *,
    fetched_on: date,
) -> tuple[FredVintage, ...]:
    current: dict[date, VintagePoint] = {}
    for row in existing:
        if row.realtime_end != OPEN_ENDED:
            continue
        held = current.get(row.observation_date)
        if held is None or row.realtime_start > held.realtime_start:
            current[row.observation_date] = row
    planned: list[FredVintage] = []
    for point in fresh:
        held = current.get(point.observation_date)
        if held is None:
            planned.append(_open(point.observation_date, point.value, point.published_on))
            continue
        if held.value == point.value:
            continue
        if fetched_on <= held.realtime_start:
            planned.append(_open(point.observation_date, point.value, held.realtime_start))
            continue
        planned.append(
            FredVintage(
                observation_date=held.observation_date,
                value=held.value,
                realtime_start=held.realtime_start,
                realtime_end=fetched_on - timedelta(days=1),
            )
        )
        planned.append(_open(point.observation_date, point.value, fetched_on))
    return tuple(planned)


def _open(observed: date, value: Decimal, start: date) -> FredVintage:
    return FredVintage(
        observation_date=observed,
        value=value,
        realtime_start=start,
        realtime_end=OPEN_ENDED,
    )
```

- [ ] **Step 5: 运行，确认通过**

```powershell
& $py -m pytest tests/unit/test_eastmoney_us_macro.py -q
```

Expected: 11 passed。

- [ ] **Step 6: 实网冒烟（只读，不入库）**

```powershell
& $py -c "import asyncio,httpx; from app.providers.http_client import ResilientHttpClient; from app.providers.httpx_transport import HttpxTransport; from app.providers.eastmoney_us_macro import fetch_indicator
async def m():
    async with httpx.AsyncClient(timeout=15) as c:
        rows = await fetch_indicator(ResilientHttpClient(HttpxTransport(c), sleeper=asyncio.sleep), 'EMG00002790')
        print(len(rows), rows[0], rows[-1])
asyncio.run(m())"
```

Expected: 约 `225` 行，首行 2008-01，末行为最近已公布月份（`VALUE` 非空）。网络不通时记录下来，不阻塞后续任务。

- [ ] **Step 7: 提交**

```powershell
git add backend/app/providers/eastmoney_us_macro.py backend/app/providers/latest_revisions.py backend/tests/unit/test_eastmoney_us_macro.py
git commit -m "接入东方财富美国经济报表的 ISM PMI，并用纯函数把最新值换成可回放的版本行"
```

---

### Task 3: 多 provider 入库与同步分派

**Files:**
- Modify: `backend/app/repositories/macro.py`
- Modify: `backend/app/services/fred_sync.py`
- Modify: `backend/app/schemas/macro.py:59`（`FredSyncRequest`）
- Modify: `backend/app/core/config.py:94`
- Modify: `backend/app/scripts/bootstrap_data.py:129`
- Test: `backend/tests/unit/test_fred.py`、Create `backend/tests/integration/test_us_macro_eastmoney.py`

**Interfaces:**
- Consumes: Task 1 `seed_for`、`MacroProvider`；Task 2 `LatestValue`、`fetch_indicator`、`plan_latest_revisions`、`indicator_url`
- Produces:
  - `MacroRepository.upsert_latest_values(series_code: str, points: Sequence[LatestValue], *, fetched_at: datetime) -> int`
  - `EASTMONEY_US_PROVIDER_ID: uuid.UUID`
  - `MacroSeriesSeed` 从数据库读回时带 `provider` / `source_key`（存于 `metadata_json`）
  - `LatestFetcher = Callable[[str], Awaitable[tuple[LatestValue, ...]]]`
  - `sync_fred_series(..., fetch_latest: LatestFetcher | None = None) -> int`
  - `async fetch_from_eastmoney(indicator_id: str) -> tuple[LatestValue, ...]`

- [ ] **Step 1: 写失败的单元测试（追加到 `backend/tests/unit/test_fred.py` 末尾）**

```python
class LatestRepository(MacroRepository):
    def __init__(self) -> None:
        self.latest: list[tuple[str, list[LatestValue]]] = []

    def upsert_latest_values(
        self,
        series_code: str,
        points: Sequence[LatestValue],
        *,
        fetched_at: datetime,
    ) -> int:
        self.latest.append((series_code, list(points)))
        return len(points)


def test_ism_sync_goes_to_eastmoney_and_keeps_only_the_window() -> None:
    repository = LatestRepository()

    async def no_fred(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
        raise AssertionError("ISM 不应请求 FRED")

    async def latest(indicator_id: str) -> tuple[LatestValue, ...]:
        assert indicator_id == "EMG00002791"
        return (
            LatestValue(date(2026, 7, 1), date(2026, 8, 5), Decimal("54.1")),
            LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("54.9")),
        )

    written = asyncio.run(
        sync_fred_series(
            repository,
            series_code="ISM_SVC",
            observation_start=date(2026, 8, 1),
            observation_end=date(2026, 10, 17),
            fetch=no_fred,
            fetched_at=datetime(2026, 10, 17, 15, tzinfo=UTC),
            fetch_latest=latest,
        )
    )
    assert written == 1
    assert repository.latest == [
        ("ISM_SVC", [LatestValue(date(2026, 9, 1), date(2026, 10, 5), Decimal("54.9"))])
    ]


def test_sync_request_accepts_underscored_codes() -> None:
    body = FredSyncRequest(
        series_code="ISM_MFG",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 2, 1),
    )
    assert body.series_code == "ISM_MFG"


def test_scheduler_syncs_every_catalog_series_by_default() -> None:
    from app.core.config import Settings

    assert set(Settings().scheduler_series_codes()) == set(catalog_codes())
```

在文件顶部导入区追加：

```python
from collections.abc import Sequence

from app.providers.eastmoney_us_macro import LatestValue
from app.schemas.macro import FredSyncRequest, build_observations
```

（原有 `from app.schemas.macro import build_observations` 一行删除，避免重复导入。）

> 若 `Settings()` 因缺环境变量无法构造，按 `tests/conftest.py` 已设置的变量即可；仍失败则改用 `get_settings()`。

- [ ] **Step 2: 运行，确认失败**

```powershell
& $py -m pytest tests/unit/test_fred.py -q
```

Expected: `test_ism_sync_goes_to_eastmoney...` 报 `TypeError: sync_fred_series() got an unexpected keyword argument 'fetch_latest'`；`test_sync_request_accepts_underscored_codes` 报 `ValidationError`；`test_scheduler_syncs...` 断言失败。

- [ ] **Step 3: `backend/app/services/fred_sync.py` 整文件替换**

```python
import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime

import httpx

from app.catalog.macro_series import seed_for
from app.providers.eastmoney_us_macro import LatestValue, fetch_indicator
from app.providers.errors import ProviderError
from app.providers.fred import FredAdapter, FredVintage
from app.providers.fred_public import fetch_public_csv
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.repositories.macro import MacroRepository
from app.utils.time import utc_now

VintageFetcher = Callable[[str, date, date], Awaitable[tuple[FredVintage, ...]]]
LatestFetcher = Callable[[str], Awaitable[tuple[LatestValue, ...]]]
_MAX_WINDOW_DAYS = 3660


async def sync_fred_series(
    repository: MacroRepository,
    *,
    series_code: str,
    observation_start: date,
    observation_end: date,
    fetch: VintageFetcher,
    fetched_at: datetime,
    fetch_latest: LatestFetcher | None = None,
) -> int:
    """美国宏观序列同步，按序列的数据源分派。

    FRED：有密钥时 fetch 走带修订历史的 observations 接口，
    没有时走公开 CSV，由调用方决定。
    东方财富（ISM PMI）：fetch_latest 只给最新值，版本由仓库按抓取日记录。
    """
    seed = seed_for(series_code)
    if seed is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
        )
    if observation_end < observation_start or _window_days(observation_start, observation_end) > (
        _MAX_WINDOW_DAYS
    ):
        raise ProviderError(
            code="VALIDATION_ERROR",
            message="观测区间无效或超过 10 年",
            status_code=422,
        )
    if seed.provider == "eastmoney":
        loader = fetch_latest or fetch_from_eastmoney
        points = [
            point
            for point in await loader(seed.source_key)
            if observation_start <= point.observation_date <= observation_end
        ]
        return repository.upsert_latest_values(series_code, points, fetched_at=fetched_at)
    vintages = await fetch(series_code, observation_start, observation_end)
    return repository.upsert_vintages(series_code, vintages, fetched_at=fetched_at)


def _window_days(start: date, end: date) -> int:
    return (end - start).days


async def fetch_from_fred(
    api_key: str,
    series_code: str,
    observation_start: date,
    observation_end: date,
) -> tuple[FredVintage, ...]:
    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.2,
        )
        if not api_key.strip():
            return await fetch_public_csv(
                http,
                series_code,
                observation_start,
                observation_end,
                fetched_at=utc_now(),
            )
        return await FredAdapter(http, api_key).fetch_vintages(
            series_code,
            observation_start,
            observation_end,
        )


async def fetch_from_eastmoney(indicator_id: str) -> tuple[LatestValue, ...]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.5,
        )
        return await fetch_indicator(http, indicator_id)
```

- [ ] **Step 4: `backend/app/repositories/macro.py` 改为多 provider**

4a. 导入区替换为：

```python
import uuid
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.catalog.macro_series import MacroProvider, MacroSeriesSeed, fred_series_catalog
from app.db.models.macro import MacroObservation, MacroSeries
from app.db.models.provider import DataProvider
from app.providers.eastmoney_us_macro import LatestValue, indicator_url
from app.providers.fred import FredVintage
from app.providers.latest_revisions import plan_latest_revisions
from app.providers.vintage import VintagePoint, pit_date, published_at_from_vintage

FRED_PROVIDER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://finscope.local/providers/fred")
EASTMONEY_US_PROVIDER_ID = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://finscope.local/providers/eastmoney-us-macro"
)
_PROVIDERS: dict[MacroProvider, tuple[uuid.UUID, str, str, dict[str, object]]] = {
    "fred": (
        FRED_PROVIDER_ID,
        "fred",
        "FRED",
        {"transport": "https://api.stlouisfed.org", "credential": "environment"},
    ),
    "eastmoney": (
        EASTMONEY_US_PROVIDER_ID,
        "eastmoney_us_macro",
        "东方财富数据中心（美国经济数据）",
        {"transport": "https://datacenter-web.eastmoney.com", "credential": "none"},
    ),
}
```

4b. `ensure_catalog` 替换为：

```python
    def ensure_catalog(self) -> int:
        seeds = fred_series_catalog()
        with Session(self.engine) as session:
            providers = {code: _provider(session, code) for code in {seed.provider for seed in seeds}}
            for seed in seeds:
                _upsert_series(session, provider_id=providers[seed.provider].id, seed=seed)
            session.commit()
        return len(seeds)
```

4c. 在 `upsert_vintages` 方法之后新增：

```python
    def upsert_latest_values(
        self,
        series_code: str,
        points: Sequence[LatestValue],
        *,
        fetched_at: datetime,
    ) -> int:
        """只有最新值的来源：与已入库版本比对后，只写新增或变化的版本。"""

        existing = self.list_vintages(series_code, start=None, end=None) or []
        planned = plan_latest_revisions(existing, points, fetched_on=pit_date(fetched_at))
        if not planned:
            return 0
        return self.upsert_vintages(series_code, planned, fetched_at=fetched_at)
```

4d. `list_vintages` 里构造 `points` 的循环替换为使用公共函数：

```python
            points: list[VintagePoint] = []
            for row in rows:
                point = _point(row)
                if point is not None:
                    points.append(point)
            return points
```

并在模块级（`_provider` 之前）新增：

```python
def _point(row: MacroObservation) -> VintagePoint | None:
    if row.realtime_start is None or row.realtime_end is None:
        return None
    return VintagePoint(
        observation_date=row.observation_date,
        value=row.value,
        unit=row.unit,
        realtime_start=row.realtime_start,
        realtime_end=row.realtime_end,
        revision_no=row.revision_no,
    )
```

4e. `_provider` 整体替换为：

```python
def _provider(session: Session, code: MacroProvider) -> DataProvider:
    provider_id, provider_code, name, config = _PROVIDERS[code]
    found = session.scalars(
        select(DataProvider).where(DataProvider.code == provider_code)
    ).one_or_none()
    if found is not None:
        return found
    provider = DataProvider(
        id=provider_id,
        code=provider_code,
        name=name,
        enabled=True,
        config_public=config,
    )
    session.add(provider)
    session.flush()
    return provider
```

4f. `_upsert_series` 中更新已有记录的分支，在 `existing.name_cn = seed.name_cn` 之前加一行：

```python
    existing.provider_id = provider_id
```

4g. `_metadata` 替换为：

```python
def _metadata(seed: MacroSeriesSeed) -> dict[str, object]:
    source_url = (
        indicator_url(seed.source_key)
        if seed.provider == "eastmoney"
        else f"https://fred.stlouisfed.org/series/{seed.series_code}"
    )
    return {
        "official_title": seed.name_en,
        "unit_label": seed.base_unit,
        "seasonal_label": seed.seasonal_adjustment,
        "time_precision": "date",
        "source_url": source_url,
        "source_agency": seed.source_agency,
        "provider": seed.provider,
        "source_key": seed.source_key,
        "vintage_calendar": "America/New_York",
        "note": "频率和单位不同的序列不能直接相加",
    }
```

4h. `_seed_from_row` 替换为：

```python
def _seed_from_row(row: MacroSeries) -> MacroSeriesSeed:
    agency = row.metadata_json.get("source_agency")
    source_key = row.metadata_json.get("source_key")
    provider: MacroProvider = (
        "eastmoney" if row.metadata_json.get("provider") == "eastmoney" else "fred"
    )
    return MacroSeriesSeed(
        series_code=row.series_code,
        name_cn=row.name_cn,
        name_en=row.name_en,
        category=row.category,
        frequency=row.frequency,
        base_unit=row.base_unit,
        seasonal_adjustment=row.seasonal_adjustment,
        source_agency=agency if isinstance(agency, str) else "",
        provider=provider,
        source_key=source_key if isinstance(source_key, str) else "",
    )
```

- [ ] **Step 5: 同步请求正则、定时任务默认序列、灌数脚本文案**

`backend/app/schemas/macro.py` 的 `FredSyncRequest`：

```python
class FredSyncRequest(BaseModel):
    series_code: str = Field(pattern=r"^[A-Z0-9_]+$", min_length=1, max_length=32)
```

`backend/app/core/config.py` 第 94 行替换为：

```python
    scheduler_fred_series: str = (
        "GDP,UNRATE,PAYEMS,CPIAUCSL,PCEPILFE,FEDFUNDS,DGS2,DGS10,T10Y2Y,"
        "CES0500000003,GDPC1,PCEC96,DGS5,DGS20,DFEDTARL,DFEDTARU,ICSA,ISM_MFG,ISM_SVC"
    )
```

`backend/app/scripts/bootstrap_data.py`：模块 docstring 中 `- 美国宏观：FRED（有密钥走 observations，否则走公开 CSV）。` 改为 `- 美国宏观：FRED（有密钥走 observations，否则走公开 CSV）+ 东方财富 ISM PMI。`；第 129 行改为：

```python
    print(
        f"\n[美国宏观] {len(catalog_codes())} 条序列（FRED + 东方财富 ISM），"
        f"{start} ~ {today}，{mode}"
    )
```

- [ ] **Step 6: 单元测试通过**

```powershell
& $py -m pytest tests/unit/test_fred.py tests/unit/test_eastmoney_us_macro.py -q
```

Expected: 全部 PASS。

- [ ] **Step 7: 写集成测试 `backend/tests/integration/test_us_macro_eastmoney.py`**

```python
import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from app.core.config import get_settings
from app.db.session import create_db_engine
from app.providers.eastmoney_us_macro import LatestValue
from app.providers.fred import FredVintage
from app.repositories.macro import MacroRepository
from app.services.fred_sync import sync_fred_series
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

OBSERVED = date(1899, 1, 1)
OPEN = date(9999, 12, 31)


@pytest.fixture
def engine() -> Iterator[Engine]:
    created = create_db_engine(get_settings().database_url)
    try:
        with created.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        created.dispose()
        pytest.skip("PostgreSQL 不可用")
    _cleanup(created)
    try:
        yield created
    finally:
        _cleanup(created)
        created.dispose()


def _cleanup(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM macro_observations WHERE observation_date = :observed"),
            {"observed": OBSERVED},
        )


async def _no_fred(series_code: str, start: date, end: date) -> tuple[FredVintage, ...]:
    raise AssertionError("ISM 不应请求 FRED")


def _sync(repository: MacroRepository, value: str, fetched_at: datetime) -> int:
    async def latest(indicator_id: str) -> tuple[LatestValue, ...]:
        assert indicator_id == "EMG00002790"
        return (LatestValue(OBSERVED, date(1899, 2, 1), Decimal(value)),)

    return asyncio.run(
        sync_fred_series(
            repository,
            series_code="ISM_MFG",
            observation_start=date(1898, 12, 1),
            observation_end=date(1899, 12, 31),
            fetch=_no_fred,
            fetched_at=fetched_at,
            fetch_latest=latest,
        )
    )


@pytest.mark.integration
def test_ism_revision_is_recorded_from_first_sync(engine: Engine) -> None:
    repository = MacroRepository(engine)
    assert _sync(repository, "50.3", datetime(2026, 10, 1, 15, tzinfo=UTC)) == 1
    assert _sync(repository, "50.3", datetime(2026, 10, 2, 15, tzinfo=UTC)) == 0
    assert _sync(repository, "49.8", datetime(2027, 1, 10, 15, tzinfo=UTC)) == 2
    rows = repository.list_vintages("ISM_MFG", start=OBSERVED, end=OBSERVED) or []
    rows.sort(key=lambda row: row.realtime_start)
    assert [(row.value, row.realtime_start, row.realtime_end) for row in rows] == [
        (Decimal("50.3"), date(1899, 2, 1), date(2027, 1, 9)),
        (Decimal("49.8"), date(2027, 1, 10), OPEN),
    ]
    seeds = {seed.series_code: seed for seed in repository.list_series()}
    assert seeds["ISM_MFG"].provider == "eastmoney"
    assert seeds["ISM_MFG"].source_key == "EMG00002790"
```

- [ ] **Step 8: 运行集成测试**

```powershell
& $py -m pytest tests/integration/test_us_macro_eastmoney.py -q -rs
```

Expected: 有 PostgreSQL 时 `1 passed`；没有时 `1 skipped (PostgreSQL 不可用)`，并在最终报告里如实写明未在真实库上跑过。

- [ ] **Step 9: 全量后端单测回归**

```powershell
& $py -m pytest tests/unit -q
```

Expected: 全部 PASS（不得引入新的失败；若有与本任务无关的既有失败，记录原文）。

- [ ] **Step 10: 提交**

```powershell
git add backend/app/repositories/macro.py backend/app/services/fred_sync.py backend/app/schemas/macro.py backend/app/core/config.py backend/app/scripts/bootstrap_data.py backend/tests/unit/test_fred.py backend/tests/integration/test_us_macro_eastmoney.py
git commit -m "宏观同步按数据源分派：ISM 走东方财富并自首次同步起记录修订，定时任务默认同步全部序列"
```

---

### Task 4: 批量观测接口

**Files:**
- Create: `backend/app/services/macro_batch.py`
- Modify: `backend/app/repositories/macro.py`（新增 `list_vintages_many`）
- Modify: `backend/app/schemas/macro.py`（目录项字段、`ObservationBatch`）
- Modify: `backend/app/api/v1/macro.py`
- Test: Create `backend/tests/unit/test_macro_batch.py`；追加到 `backend/tests/integration/test_us_macro_eastmoney.py`

**Interfaces:**
- Consumes: Task 1 `catalog_codes`、`seed_for`；Task 3 `MacroRepository`、`_point`；`select_visible`、`build_observations`
- Produces:
  - `MacroRepository.list_vintages_many(series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]`
  - `MAX_BATCH_CODES = 20`
  - `parse_codes(raw: str) -> list[str]`（去空、大写、去重保序；数量或未知代码不合规抛 `AppError(VALIDATION_ERROR, 422)`，未知代码时 `details={"unknown_codes": [...]}`）
  - `limit_for(series_code: str) -> int`
  - `build_observation_batch(source: VintageSource, codes: Sequence[str], pit: date) -> ObservationBatch`
  - `ObservationBatch(pit_date: date, items: list[ObservationPage])`
  - `MacroSeriesItem` 新增 `provider: str`、`source_key: str`
  - `GET /api/v1/macro/observations?codes=&as_of=`

- [ ] **Step 1: 写失败测试 `backend/tests/unit/test_macro_batch.py`**

```python
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

import pytest
from app.catalog.macro_series import fred_series_catalog
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint
from app.schemas.macro import build_catalog
from app.services.macro_batch import build_observation_batch, limit_for, parse_codes

OPEN = date(9999, 12, 31)


class FakeSource:
    def __init__(self, rows: dict[str, list[VintagePoint]]) -> None:
        self.rows = rows
        self.calls: list[list[str]] = []

    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]:
        self.calls.append(list(series_codes))
        return {code: self.rows.get(code, []) for code in series_codes}


def _point(observed: date, start: date, value: str) -> VintagePoint:
    return VintagePoint(
        observation_date=observed,
        value=Decimal(value),
        unit="Index",
        realtime_start=start,
        realtime_end=OPEN,
        revision_no=1,
    )


def test_parse_codes_dedupes_and_uppercases_in_order() -> None:
    assert parse_codes(" ism_mfg,UNRATE,,ISM_MFG ") == ["ISM_MFG", "UNRATE"]


def test_parse_codes_lists_unknown_codes() -> None:
    with pytest.raises(AppError) as caught:
        parse_codes("UNRATE,NOPE")
    assert caught.value.code == "VALIDATION_ERROR"
    assert caught.value.status_code == 422
    assert caught.value.details == {"unknown_codes": ["NOPE"]}


@pytest.mark.parametrize("raw", [" , ", ",".join(f"X{index}" for index in range(21))])
def test_parse_codes_rejects_empty_or_too_many(raw: str) -> None:
    with pytest.raises(AppError) as caught:
        parse_codes(raw)
    assert caught.value.code == "VALIDATION_ERROR"
    assert "1–20" in caught.value.message


def test_limit_follows_frequency() -> None:
    assert limit_for("DGS10") == 1000
    assert limit_for("ICSA") == 260
    assert limit_for("UNRATE") == 120
    assert limit_for("GDPC1") == 120


def test_batch_keeps_request_order_and_hides_release_day() -> None:
    source = FakeSource({"ISM_MFG": [_point(date(2026, 9, 1), date(2026, 10, 1), "54.5")]})
    release_day = build_observation_batch(source, ["UNRATE", "ISM_MFG"], date(2026, 10, 1))
    assert [item.series_code for item in release_day.items] == ["UNRATE", "ISM_MFG"]
    assert release_day.items[1].observations == []
    assert release_day.items[0].empty_message is not None
    next_day = build_observation_batch(source, ["ISM_MFG"], date(2026, 10, 2))
    assert [item.value for item in next_day.items[0].observations] == ["54.5"]
    assert next_day.pit_date == date(2026, 10, 2)
    assert source.calls == [["UNRATE", "ISM_MFG"], ["ISM_MFG"]]


def test_catalog_items_carry_provider_and_source_key() -> None:
    catalog = build_catalog(list(fred_series_catalog()), availability="public_csv")
    items = {item.series_code: item for item in catalog.series}
    assert (items["ISM_MFG"].provider, items["ISM_MFG"].source_key) == ("eastmoney", "EMG00002790")
    assert (items["DGS5"].provider, items["DGS5"].source_key) == ("fred", "")
```

- [ ] **Step 2: 运行，确认失败**

```powershell
& $py -m pytest tests/unit/test_macro_batch.py -q
```

Expected: `ModuleNotFoundError: No module named 'app.services.macro_batch'`。

- [ ] **Step 3: schema 改动 `backend/app/schemas/macro.py`**

`MacroSeriesItem` 追加两个字段：

```python
class MacroSeriesItem(BaseModel):
    series_code: str
    name_cn: str
    name_en: str
    category: str
    frequency: str
    base_unit: str
    seasonal_adjustment: str
    provider: str
    source_key: str
```

`build_catalog` 里构造 `MacroSeriesItem(...)` 时追加：

```python
                provider=item.provider,
                source_key=item.source_key,
```

在 `ObservationPage` 之后新增：

```python
class ObservationBatch(BaseModel):
    pit_date: date
    items: list[ObservationPage]
```

- [ ] **Step 4: 新建 `backend/app/services/macro_batch.py`**

```python
"""一次取回多条美国宏观序列在某观察日可见的观测。可见性与单序列接口同一套规则。"""

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.catalog.macro_series import catalog_codes, seed_for
from app.core.exceptions import AppError
from app.providers.vintage import VintagePoint, select_visible
from app.schemas.macro import ObservationBatch, build_observations

MAX_BATCH_CODES = 20
DEFAULT_LIMIT = 120
_LIMIT_BY_FREQUENCY = {"Daily": 1000, "Weekly": 260}


class VintageSource(Protocol):
    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]: ...


def parse_codes(raw: str) -> list[str]:
    codes = list(dict.fromkeys(item.strip().upper() for item in raw.split(",") if item.strip()))
    if not codes or len(codes) > MAX_BATCH_CODES:
        raise AppError(
            code="VALIDATION_ERROR",
            message=f"codes 需要 1–{MAX_BATCH_CODES} 个序列代码",
            status_code=422,
        )
    known = catalog_codes()
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise AppError(
            code="VALIDATION_ERROR",
            message="不在已核对的宏观序列里",
            status_code=422,
            details={"unknown_codes": unknown},
        )
    return codes


def limit_for(series_code: str) -> int:
    """日度约四年交易日，周度约五年，月度 / 季度十年：够算同比和画 1–5 年走势。"""

    seed = seed_for(series_code)
    if seed is None:
        return DEFAULT_LIMIT
    return _LIMIT_BY_FREQUENCY.get(seed.frequency, DEFAULT_LIMIT)


def build_observation_batch(
    source: VintageSource, codes: Sequence[str], pit: date
) -> ObservationBatch:
    rows_by_code = source.list_vintages_many(codes)
    return ObservationBatch(
        pit_date=pit,
        items=[
            build_observations(
                series_code=code,
                pit=pit,
                rows=select_visible(rows_by_code.get(code, []), pit),
                limit=limit_for(code),
            )
            for code in codes
        ],
    )
```

- [ ] **Step 5: 仓库新增 `list_vintages_many`（`backend/app/repositories/macro.py`，放在 `list_vintages` 之后）**

```python
    def list_vintages_many(self, series_codes: Sequence[str]) -> dict[str, list[VintagePoint]]:
        """批量读取：只同步一次目录、只查一次库。"""

        self.ensure_catalog()
        found: dict[str, list[VintagePoint]] = {code: [] for code in series_codes}
        if not found:
            return found
        with Session(self.engine) as session:
            rows = session.execute(
                select(MacroSeries.series_code, MacroObservation)
                .join(MacroSeries, MacroObservation.series_id == MacroSeries.id)
                .where(MacroSeries.series_code.in_(list(found)))
                .order_by(MacroObservation.observation_date)
            ).all()
            for series_code, row in rows:
                point = _point(row)
                if point is not None:
                    found[series_code].append(point)
        return found
```

- [ ] **Step 6: 路由 `backend/app/api/v1/macro.py`**

导入区 `from app.schemas.macro import build_assessment, build_catalog, build_observations` 之后追加：

```python
from app.services.macro_batch import build_observation_batch, parse_codes
```

在 `read_macro_observations` 函数之后新增：

```python
@router.get("/observations", summary="一次取回多条宏观序列在某时点可见的观测")
def read_macro_observation_batch(
    request: Request,
    codes: Annotated[str, Query(min_length=1, max_length=400)],
    as_of: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    pit = _parse_as_of(as_of)
    payload = build_observation_batch(_repository(request), parse_codes(codes), pit)
    return JSONResponse(status_code=200, content=success_response(request, payload))
```

- [ ] **Step 7: 单测通过**

```powershell
& $py -m pytest tests/unit/test_macro_batch.py tests/unit/test_fred.py -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 追加集成测试到 `backend/tests/integration/test_us_macro_eastmoney.py`**

文件顶部导入追加：

```python
from app.main import create_app
from fastapi.testclient import TestClient
```

文件末尾追加：

```python
@pytest.mark.integration
def test_batch_endpoint_hides_ism_until_the_day_after_release(engine: Engine) -> None:
    MacroRepository(engine).upsert_latest_values(
        "ISM_MFG",
        [LatestValue(OBSERVED, date(1899, 2, 1), Decimal("50.3"))],
        fetched_at=datetime(2026, 10, 1, 15, tzinfo=UTC),
    )
    with TestClient(create_app()) as client:
        release = client.get(
            "/api/v1/macro/observations", params={"codes": "ISM_MFG,UNRATE", "as_of": "1899-02-01"}
        )
        after = client.get(
            "/api/v1/macro/observations", params={"codes": "ISM_MFG", "as_of": "1899-02-02"}
        )
        unknown = client.get("/api/v1/macro/observations", params={"codes": "ISM_MFG,NOPE"})
    assert release.status_code == 200
    items = release.json()["data"]["items"]
    assert [item["series_code"] for item in items] == ["ISM_MFG", "UNRATE"]
    assert items[0]["observations"] == []
    assert [row["value"] for row in after.json()["data"]["items"][0]["observations"]] == ["50.3"]
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"]["unknown_codes"] == ["NOPE"]
```

- [ ] **Step 9: 运行集成测试**

```powershell
& $py -m pytest tests/integration/test_us_macro_eastmoney.py -q -rs
```

Expected: 有库 `2 passed`；无库 `2 skipped`（如实记录）。

- [ ] **Step 10: 后端静态检查**

```powershell
& $py -m ruff check .
& $py -m ruff format --check .
& $py -m mypy app
```

Expected: 三条都无错误。`ruff format --check` 报格式差异时运行 `& $py -m ruff format <文件>` 后重跑。行宽上限 100 且中文按双宽计，`E501` 出现在中文注释 / 文档字符串上时把该行拆成两行。

- [ ] **Step 11: 提交**

```powershell
git add backend/app/services/macro_batch.py backend/app/repositories/macro.py backend/app/schemas/macro.py backend/app/api/v1/macro.py backend/tests/unit/test_macro_batch.py backend/tests/integration/test_us_macro_eastmoney.py
git commit -m "新增宏观批量观测接口，目录项带数据源与指标 ID"
```

---

### Task 5: 前端口径模块 `macroTiles.ts`

**Files:**
- Create: `frontend/src/api/macroTiles.ts`
- Modify: `frontend/src/api/macro.ts`（`unitLabel`）
- Test: Create `frontend/tests/unit/macroTiles.test.ts`；Modify `frontend/tests/unit/macro.test.ts`

**Interfaces:**
- Consumes: `yearOverYear`、`unitLabel`、`adjustmentLabel`（`src/api/macro.ts`）
- Produces（全部从 `src/api/macroTiles.ts` 导出）：
  - `interface Observation { date: string; value: number }`
  - `interface TileCatalogItem { code; name; nameEn; frequency; unit; adjustment; provider; sourceKey }`（全是 `string`）
  - `type TileTransform = "level" | "yoy" | "diff" | "saar" | "range"`
  - `type TileDirection = "up" | "down" | "flat" | "none"`
  - `interface TileSpec`、`interface TileView`（字段见代码）
  - `TILE_SPECS: readonly TileSpec[]`（15 项）
  - `tileCodes(): string[]`
  - `diffSeries`、`saarSeries`、`applyTransform(transform: Exclude<TileTransform, "range">, rows)`、`alignedRange`、`periodText(date, frequency)`、`buildTile(spec, rowsByCode, catalog)`

- [ ] **Step 0: 安装前端依赖（工作树里还没有 node_modules）**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker\frontend
npm ci
```

Expected: 安装成功；`npm run test` 能跑出现有 `macro.test.ts` 全部通过。

- [ ] **Step 1: 写失败测试 `frontend/tests/unit/macroTiles.test.ts`**

```ts
import { describe, expect, it } from "vitest"
import {
  alignedRange,
  buildTile,
  diffSeries,
  periodText,
  saarSeries,
  TILE_SPECS,
  tileCodes,
  type TileCatalogItem,
  type TileSpec,
} from "../../src/api/macroTiles"

function spec(key: string): TileSpec {
  const found = TILE_SPECS.find((entry) => entry.key === key)
  if (!found) {
    throw new Error(`缺少卡片 ${key}`)
  }
  return found
}

function item(code: string, frequency: string, unit = "Percent", provider = "fred", sourceKey = ""): TileCatalogItem {
  return { code, name: code, nameEn: code, frequency, unit, adjustment: "Seasonally Adjusted", provider, sourceKey }
}

describe("指标带口径", () => {
  it("15 格顺序固定，所需代码不超过批量接口上限", () => {
    expect(TILE_SPECS.map((entry) => entry.key)).toEqual([
      "core-pce",
      "cpi",
      "nfp",
      "unrate",
      "ahe",
      "real-gdp",
      "real-pce",
      "ism-svc",
      "ism-mfg",
      "policy",
      "dgs5",
      "dgs10",
      "dgs20",
      "spread",
      "claims",
    ])
    expect(tileCodes()).toHaveLength(16)
    expect(tileCodes().length).toBeLessThanOrEqual(20)
  })

  it("非农当月新增只算相邻月份，断档不出值", () => {
    expect(
      diffSeries([
        { date: "2026-06-01", value: 100 },
        { date: "2026-07-01", value: 150 },
        { date: "2026-09-01", value: 170 },
      ]),
    ).toEqual([{ date: "2026-07-01", value: 50 }])
  })

  it("实际 GDP 折年率按相邻季度四次方，跨季断档不出值", () => {
    const [point] = saarSeries([
      { date: "2026-01-01", value: 100 },
      { date: "2026-04-01", value: 101 },
    ])
    expect(point?.date).toBe("2026-04-01")
    expect(point?.value).toBeCloseTo(4.060401, 5)
    expect(
      saarSeries([
        { date: "2025-10-01", value: 100 },
        { date: "2026-04-01", value: 101 },
      ]),
    ).toEqual([])
  })

  it("政策利率上下限按日期对齐，缺一边的日期丢弃", () => {
    expect(
      alignedRange(
        [
          { date: "2026-09-16", value: 4.25 },
          { date: "2026-09-17", value: 4.0 },
        ],
        [
          { date: "2026-09-17", value: 4.25 },
          { date: "2026-09-18", value: 4.25 },
        ],
      ),
    ).toEqual([{ date: "2026-09-17", low: 4.0, high: 4.25 }])
  })

  it("CPI 显示同比与百分点变化，上升记为 up", () => {
    const tile = buildTile(
      spec("cpi"),
      {
        CPIAUCSL: [
          { date: "2025-08-01", value: 100 },
          { date: "2025-09-01", value: 100 },
          { date: "2026-08-01", value: 103 },
          { date: "2026-09-01", value: 103.72 },
        ],
      },
      [item("CPIAUCSL", "Monthly", "Index 1982-1984=100")],
    )
    expect(tile.value).toBe("3.72")
    expect(tile.unit).toBe("%")
    expect(tile.date).toBe("2026-09")
    expect(tile.deltaText).toBe("+0.72 pct")
    expect(tile.direction).toBe("up")
    expect(tile.spark).toHaveLength(2)
    expect(tile.info).toContain("FRED 代码 CPIAUCSL")
    expect(tile.info).toContain("同比")
  })

  it("政策利率显示目标区间，较上期按上一次调整计", () => {
    const tile = buildTile(
      spec("policy"),
      {
        DFEDTARL: [
          { date: "2026-09-16", value: 4.25 },
          { date: "2026-09-17", value: 4.0 },
          { date: "2026-09-18", value: 4.0 },
        ],
        DFEDTARU: [
          { date: "2026-09-16", value: 4.5 },
          { date: "2026-09-17", value: 4.25 },
          { date: "2026-09-18", value: 4.25 },
        ],
      },
      [item("DFEDTARL", "Daily"), item("DFEDTARU", "Daily")],
    )
    expect(tile.value).toBe("4.00–4.25")
    expect(tile.date).toBe("2026-09-18")
    expect(tile.deltaText).toBe("-0.25 pct")
    expect(tile.direction).toBe("down")
  })

  it("初请换算成千人；ISM 来源写明东方财富与最新修订", () => {
    const claims = buildTile(
      spec("claims"),
      {
        ICSA: [
          { date: "2026-10-04", value: 225000 },
          { date: "2026-10-11", value: 231000 },
        ],
      },
      [item("ICSA", "Weekly", "Number")],
    )
    expect(claims.value).toBe("231")
    expect(claims.unit).toBe("千人")
    expect(claims.deltaText).toBe("+6 千人")
    expect(claims.date).toBe("2026-10-11")

    const ism = buildTile(
      spec("ism-mfg"),
      {
        ISM_MFG: [
          { date: "2026-08-01", value: 54.6 },
          { date: "2026-09-01", value: 54.5 },
        ],
      },
      [item("ISM_MFG", "Monthly", "Index", "eastmoney", "EMG00002790")],
    )
    expect(ism.value).toBe("54.5")
    expect(ism.deltaText).toBe("-0.1 点")
    expect(ism.info).toContain("EMG00002790")
    expect(ism.info).toContain("最新修订")
  })

  it("没有数据时不编造", () => {
    const tile = buildTile(spec("ism-mfg"), {}, [])
    expect(tile.value).toBe("—")
    expect(tile.date).toBe("暂无数据")
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
    expect(tile.spark).toEqual([])
    expect(tile.info).toContain("暂未接入 ISM_MFG")
  })

  it("持平记为 flat，变化文案不出现负零", () => {
    const tile = buildTile(
      spec("unrate"),
      {
        UNRATE: [
          { date: "2026-08-01", value: 4.2 },
          { date: "2026-09-01", value: 4.2000001 },
        ],
      },
      [item("UNRATE", "Monthly")],
    )
    expect(tile.direction).toBe("flat")
    expect(tile.deltaText).toBe("0.00 pct")
  })

  it("季度观测日显示为 Qn，月度显示年月，日度原样", () => {
    expect(periodText("2026-04-01", "Quarterly")).toBe("2026 Q2")
    expect(periodText("2026-09-01", "Monthly")).toBe("2026-09")
    expect(periodText("2026-10-15", "Daily")).toBe("2026-10-15")
  })
})
```

- [ ] **Step 2: 运行，确认失败**

```powershell
npx vitest run tests/unit/macroTiles.test.ts
```

Expected: `Failed to resolve import "../../src/api/macroTiles"`。

- [ ] **Step 3: 新建 `frontend/src/api/macroTiles.ts`**

```ts
/**
 * 美国宏观指标带：15 格定义与口径换算。纯函数，无副作用，可离线测试。
 *
 * 不编造数据（AGENTS.md）：缺上年同月、断档的月份 / 季度不出值，不插值、不外推；
 * 一格没有可用观测时显示「—」与「暂无数据」。
 */
import { adjustmentLabel, unitLabel, yearOverYear } from "./macro"

export interface Observation {
  date: string
  value: number
}

export interface TileCatalogItem {
  code: string
  name: string
  nameEn: string
  frequency: string
  unit: string
  adjustment: string
  provider: string
  sourceKey: string
}

export type TileTransform = "level" | "yoy" | "diff" | "saar" | "range"
export type TileDirection = "up" | "down" | "flat" | "none"

export interface TileSpec {
  key: string
  /** range 用两条：[下限, 上限] */
  codes: readonly string[]
  label: string
  caption: string
  transform: TileTransform
  /** 数值后的单位小字 */
  unit: string
  deltaSuffix: string
  /** 观测值换算到展示单位的倍数，例如初请人数 → 千人 */
  scale?: number
  digits?: number
}

export interface TileView {
  key: string
  label: string
  caption: string
  value: string
  unit: string
  date: string
  delta: number | null
  deltaText: string
  direction: TileDirection
  spark: number[]
  info: string
}

export const TILE_SPECS: readonly TileSpec[] = [
  { key: "core-pce", codes: ["PCEPILFE"], label: "核心 PCE", caption: "同比 (%)", transform: "yoy", unit: "%", deltaSuffix: " pct" },
  { key: "cpi", codes: ["CPIAUCSL"], label: "CPI", caption: "同比 (%)", transform: "yoy", unit: "%", deltaSuffix: " pct" },
  { key: "nfp", codes: ["PAYEMS"], label: "非农就业", caption: "新增 (千人)", transform: "diff", unit: "千人", deltaSuffix: " 千人", digits: 0 },
  { key: "unrate", codes: ["UNRATE"], label: "失业率", caption: "(%)", transform: "level", unit: "%", deltaSuffix: " pct" },
  { key: "ahe", codes: ["CES0500000003"], label: "平均时薪", caption: "同比 (%)", transform: "yoy", unit: "%", deltaSuffix: " pct" },
  { key: "real-gdp", codes: ["GDPC1"], label: "实际 GDP", caption: "环比折年 (%)", transform: "saar", unit: "%", deltaSuffix: " pct" },
  { key: "real-pce", codes: ["PCEC96"], label: "实际个人消费支出", caption: "同比 (%)", transform: "yoy", unit: "%", deltaSuffix: " pct" },
  { key: "ism-svc", codes: ["ISM_SVC"], label: "ISM 服务业 PMI", caption: "(点)", transform: "level", unit: "", deltaSuffix: " 点", digits: 1 },
  { key: "ism-mfg", codes: ["ISM_MFG"], label: "ISM 制造业 PMI", caption: "(点)", transform: "level", unit: "", deltaSuffix: " 点", digits: 1 },
  { key: "policy", codes: ["DFEDTARL", "DFEDTARU"], label: "美联储政策利率", caption: "目标区间 (%)", transform: "range", unit: "%", deltaSuffix: " pct" },
  { key: "dgs5", codes: ["DGS5"], label: "5 年期美债", caption: "收益率 (%)", transform: "level", unit: "%", deltaSuffix: " pct" },
  { key: "dgs10", codes: ["DGS10"], label: "10 年期美债", caption: "收益率 (%)", transform: "level", unit: "%", deltaSuffix: " pct" },
  { key: "dgs20", codes: ["DGS20"], label: "20 年期美债", caption: "收益率 (%)", transform: "level", unit: "%", deltaSuffix: " pct" },
  { key: "spread", codes: ["T10Y2Y"], label: "10Y−2Y 利差", caption: "(pct)", transform: "level", unit: "pct", deltaSuffix: " pct" },
  { key: "claims", codes: ["ICSA"], label: "初请失业金", caption: "周度 (千人)", transform: "level", unit: "千人", deltaSuffix: " 千人", scale: 0.001, digits: 0 },
]

const SPARK_POINTS: Record<string, number> = { Daily: 120, Weekly: 52, Monthly: 36, Quarterly: 20 }

const FORMULAS: Record<TileTransform, string> = {
  level: "",
  yoy: "同比 = 当期 ÷ 12 个月前同月 − 1",
  diff: "当月新增 = 本月就业人数 − 上月就业人数",
  saar: "环比折年率 = (本季 ÷ 上季)^4 − 1",
  range: "目标区间 = 下限–上限；较上期按上一次调整计",
}

const REVISION_NOTE = "历史值为东方财富当前值（最新修订），本系统自首次同步起记录修订"

export function tileCodes(): string[] {
  return [...new Set(TILE_SPECS.flatMap((spec) => spec.codes))]
}

function monthIndex(date: string): number {
  return Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7)) - 1
}

/** 相邻月份之差；中间断档的月份不出值。 */
export function diffSeries(rows: readonly Observation[]): Observation[] {
  const result: Observation[] = []
  for (let index = 1; index < rows.length; index += 1) {
    const previous = rows[index - 1]
    const current = rows[index]
    if (!previous || !current || monthIndex(current.date) - monthIndex(previous.date) !== 1) {
      continue
    }
    result.push({ date: current.date, value: current.value - previous.value })
  }
  return result
}

/** 相邻季度环比折年率；上季缺失或为 0 时不出值。 */
export function saarSeries(rows: readonly Observation[]): Observation[] {
  const result: Observation[] = []
  for (let index = 1; index < rows.length; index += 1) {
    const previous = rows[index - 1]
    const current = rows[index]
    if (!previous || !current || previous.value === 0 || monthIndex(current.date) - monthIndex(previous.date) !== 3) {
      continue
    }
    result.push({ date: current.date, value: ((current.value / previous.value) ** 4 - 1) * 100 })
  }
  return result
}

export function applyTransform(transform: Exclude<TileTransform, "range">, rows: readonly Observation[]): Observation[] {
  if (transform === "yoy") {
    return yearOverYear(rows)
  }
  if (transform === "diff") {
    return diffSeries(rows)
  }
  if (transform === "saar") {
    return saarSeries(rows)
  }
  return [...rows]
}

export interface RangePoint {
  date: string
  low: number
  high: number
}

export function alignedRange(lower: readonly Observation[], upper: readonly Observation[]): RangePoint[] {
  const highs = new Map(upper.map((row) => [row.date, row.value]))
  return lower.flatMap((row) => {
    const high = highs.get(row.date)
    return high === undefined ? [] : [{ date: row.date, low: row.value, high }]
  })
}

export function periodText(date: string, frequency: string): string {
  if (frequency === "Quarterly") {
    const month = Number(date.slice(5, 7))
    return `${date.slice(0, 4)} Q${Math.floor((month - 1) / 3) + 1}`
  }
  if (frequency === "Monthly") {
    return date.slice(0, 7)
  }
  return date
}

function formatNumber(value: number, digits: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** 按展示位数取整后再判方向，避免 +0.00 被标成上升；-0 统一成 0。 */
function roundedDelta(current: number, previous: number, digits: number): number {
  const rounded = Number((current - previous).toFixed(digits))
  return rounded === 0 ? 0 : rounded
}

function directionOf(delta: number | null): TileDirection {
  if (delta === null) {
    return "none"
  }
  if (delta === 0) {
    return "flat"
  }
  return delta > 0 ? "up" : "down"
}

function deltaText(delta: number | null, digits: number, suffix: string): string {
  if (delta === null) {
    return "—"
  }
  return `${delta > 0 ? "+" : ""}${formatNumber(delta, digits)}${suffix}`
}

function sourceText(item: TileCatalogItem): string {
  return item.provider === "eastmoney" ? `东方财富指标 ${item.sourceKey}（转载 ISM）` : `FRED 代码 ${item.code}`
}

function tileInfo(spec: TileSpec, items: readonly (TileCatalogItem | null)[]): string {
  const parts = items.map((item, index) =>
    item
      ? `${item.nameEn || item.name} · ${sourceText(item)} · 单位 ${unitLabel(item.unit)} · ${adjustmentLabel(item.adjustment)}`
      : `暂未接入 ${spec.codes[index] ?? ""}`,
  )
  const formula = FORMULAS[spec.transform]
  if (formula) {
    parts.push(formula)
  }
  if (items.some((item) => item?.provider === "eastmoney")) {
    parts.push(REVISION_NOTE)
  }
  return parts.join("；")
}

export function buildTile(
  spec: TileSpec,
  rowsByCode: Readonly<Record<string, readonly Observation[]>>,
  catalog: readonly TileCatalogItem[],
): TileView {
  const items = spec.codes.map((code) => catalog.find((entry) => entry.code === code) ?? null)
  const frequency = items[0]?.frequency ?? ""
  const digits = spec.digits ?? 2
  const base = { key: spec.key, label: spec.label, caption: spec.caption, unit: spec.unit, info: tileInfo(spec, items) }
  const empty: TileView = { ...base, value: "—", date: "暂无数据", delta: null, deltaText: "—", direction: "none", spark: [] }

  if (spec.transform === "range") {
    const points = alignedRange(rowsByCode[spec.codes[0] ?? ""] ?? [], rowsByCode[spec.codes[1] ?? ""] ?? [])
    const latest = points[points.length - 1]
    if (!latest) {
      return empty
    }
    const previous = [...points].reverse().find((point) => point.high !== latest.high)
    const delta = previous ? roundedDelta(latest.high, previous.high, digits) : null
    return {
      ...base,
      value: `${formatNumber(latest.low, digits)}–${formatNumber(latest.high, digits)}`,
      date: periodText(latest.date, frequency),
      delta,
      deltaText: deltaText(delta, digits, spec.deltaSuffix),
      direction: directionOf(delta),
      spark: points.map((point) => point.high),
    }
  }

  const scale = spec.scale ?? 1
  const series = applyTransform(spec.transform, rowsByCode[spec.codes[0] ?? ""] ?? []).map((row) => ({
    date: row.date,
    value: row.value * scale,
  }))
  const latest = series[series.length - 1]
  if (!latest) {
    return empty
  }
  const previous = series[series.length - 2]
  const delta = previous ? roundedDelta(latest.value, previous.value, digits) : null
  return {
    ...base,
    value: formatNumber(latest.value, digits),
    date: periodText(latest.date, frequency),
    delta,
    deltaText: deltaText(delta, digits, spec.deltaSuffix),
    direction: directionOf(delta),
    spark: series.slice(-(SPARK_POINTS[frequency] ?? 36)).map((row) => row.value),
  }
}
```

- [ ] **Step 4: `frontend/src/api/macro.ts` 的 `unitLabel` 认新单位**

在 `if (unit === "Thousands of Persons") { return "千人" }` 之后插入：

```ts
  if (unit === "Billions of Chained 2017 Dollars") {
    return "十亿美元（2017 年不变价）"
  }
  if (unit === "Dollars per Hour") {
    return "美元/小时"
  }
  if (unit === "Number") {
    return "人"
  }
```

在 `frontend/tests/unit/macro.test.ts` 的「规则依据行与组合状态翻成中文」用例末尾（`expect(unitLabel("Index 1982-1984=100"))...` 之后）追加：

```ts
    expect(unitLabel("Billions of Chained 2017 Dollars")).toBe("十亿美元（2017 年不变价）")
    expect(unitLabel("Dollars per Hour")).toBe("美元/小时")
    expect(unitLabel("Number")).toBe("人")
```

- [ ] **Step 5: 运行，确认通过**

```powershell
npx vitest run tests/unit/macroTiles.test.ts tests/unit/macro.test.ts
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```powershell
git add frontend/src/api/macroTiles.ts frontend/src/api/macro.ts frontend/tests/unit/macroTiles.test.ts frontend/tests/unit/macro.test.ts
git commit -m "指标带口径集中到纯函数模块：同比、当月新增、折年率、政策利率区间"
```

---

### Task 6: 指标带组件

**Files:**
- Create: `frontend/src/views/parts/macro/MacroTickerTile.vue`
- Create: `frontend/src/views/parts/macro/MacroTicker.vue`
- Test: Create `frontend/tests/unit/macroTicker.test.ts`

**Interfaces:**
- Consumes: Task 5 `TileView`、`TileDirection`；`MiniSpark`（props `points`、`kind`、`tone: "up" | "down" | "muted" | ...`、`width`、`height`）
- Produces:
  - `<MacroTickerTile :tile="TileView" />`：根元素 `article.ticker-tile.is-{direction}`，`role="listitem"`
  - `<MacroTicker :tiles="readonly TileView[]" :loading="boolean" />`：滚动容器 `.track`（`role="list"`、`tabindex="0"`），加载中渲染 8 个 `.skeleton`

- [ ] **Step 1: 写失败测试 `frontend/tests/unit/macroTicker.test.ts`**

```ts
import { render, screen } from "@testing-library/vue"
import { describe, expect, it } from "vitest"
import type { TileDirection, TileView } from "../../src/api/macroTiles"
import MacroTicker from "../../src/views/parts/macro/MacroTicker.vue"

function tile(key: string, direction: TileDirection): TileView {
  return {
    key,
    label: `指标${key}`,
    caption: "(%)",
    value: "1.00",
    unit: "%",
    date: "2026-09",
    delta: direction === "none" ? null : 0.1,
    deltaText: direction === "none" ? "—" : "+0.10 pct",
    direction,
    spark: [1, 2],
    info: "说明",
  }
}

function track(container: Element): HTMLElement {
  const found = container.querySelector(".track")
  if (!(found instanceof HTMLElement)) {
    throw new Error("缺少滚动容器")
  }
  return found
}

describe("美国宏观指标带", () => {
  it("每格都渲染，并按最新方向着色", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down"), tile("c", "none")] } })
    expect(screen.getByText("指标a")).toBeTruthy()
    expect(container.querySelectorAll(".ticker-tile")).toHaveLength(3)
    expect(container.querySelectorAll(".ticker-tile.is-up")).toHaveLength(1)
    expect(container.querySelectorAll(".ticker-tile.is-down")).toHaveLength(1)
    expect(screen.getAllByText("▲")).toHaveLength(1)
    expect(screen.getAllByText("▼")).toHaveLength(1)
  })

  it("加载中显示骨架而不是空白", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")], loading: true } })
    expect(container.querySelectorAll(".skeleton")).toHaveLength(8)
    expect(container.querySelectorAll(".ticker-tile")).toHaveLength(0)
  })

  it("已在最左端时向上滚轮不拦截，页面照常滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    const event = new WheelEvent("wheel", { deltaY: -120, cancelable: true })
    track(container).dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })

  it("右侧还有内容时把纵向滚轮转成横向滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    Object.defineProperty(element, "scrollWidth", { value: 1000, configurable: true })
    Object.defineProperty(element, "clientWidth", { value: 300, configurable: true })
    const event = new WheelEvent("wheel", { deltaY: 120, cancelable: true })
    element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it("容器可聚焦，箭头按钮有可读名称", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    expect(track(container).getAttribute("tabindex")).toBe("0")
    expect(container.querySelector('[aria-label="向左滚动指标"]')).not.toBeNull()
    expect(container.querySelector('[aria-label="向右滚动指标"]')).not.toBeNull()
  })
})
```

- [ ] **Step 2: 运行，确认失败**

```powershell
npx vitest run tests/unit/macroTicker.test.ts
```

Expected: `Failed to resolve import "../../src/views/parts/macro/MacroTicker.vue"`。

- [ ] **Step 3: 新建 `frontend/src/views/parts/macro/MacroTickerTile.vue`**

```vue
<script setup lang="ts">
/**
 * 指标带单格（参考图顶部那一排）：名称 + 口径、迷你走势、大数值、▲/▼ 较上期、最新观测日。
 * 颜色只表示最新一期的方向（上升红、下降绿），不代表好坏。
 */
import MiniSpark from "../../../components/ui/MiniSpark.vue"
import type { TileDirection, TileView } from "../../../api/macroTiles"

defineProps<{ tile: TileView }>()

const SPARK_TONE: Record<TileDirection, "up" | "down" | "muted"> = {
  up: "up",
  down: "down",
  flat: "muted",
  none: "muted",
}
const ARROW: Record<TileDirection, string> = { up: "▲", down: "▼", flat: "", none: "" }
</script>

<template>
  <article class="ticker-tile" :class="`is-${tile.direction}`" role="listitem">
    <header>
      <div class="titles">
        <span class="label">
          {{ tile.label }}
          <span v-if="tile.info" class="info" :title="tile.info" aria-hidden="true">?</span>
        </span>
        <span class="caption">{{ tile.caption }}</span>
      </div>
      <MiniSpark
        v-if="tile.spark.length > 1"
        class="spark"
        :points="tile.spark"
        kind="area"
        :tone="SPARK_TONE[tile.direction]"
        :width="64"
        :height="28"
      />
    </header>
    <p class="value">
      {{ tile.value }}<small v-if="tile.unit && tile.value !== '—'">{{ tile.unit }}</small>
    </p>
    <footer>
      <span class="delta">
        <span v-if="ARROW[tile.direction]" class="arrow">{{ ARROW[tile.direction] }}</span>
        {{ tile.deltaText }}
      </span>
      <span class="date">{{ tile.date }}</span>
    </footer>
  </article>
</template>

<style scoped>
.ticker-tile {
  --tone: var(--muted);
  flex: 0 0 172px;
  scroll-snap-align: start;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 112px;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: linear-gradient(180deg, rgb(96 140 220 / 7%), rgb(96 140 220 / 2%));
}

.ticker-tile.is-up {
  --tone: var(--rise);
}

.ticker-tile.is-down {
  --tone: var(--fall);
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 6px;
}

.titles {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  overflow: hidden;
  color: var(--text-strong);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.info {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border: 1px solid var(--line);
  border-radius: 50%;
  color: var(--muted);
  font-size: 9px;
  font-weight: 400;
  cursor: help;
}

.caption {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

.spark {
  flex: none;
}

.value {
  margin: 0;
  overflow: hidden;
  color: var(--tone);
  font-family: var(--font-num);
  font-size: 22px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.ticker-tile.is-flat .value,
.ticker-tile.is-none .value {
  color: var(--text-strong);
}

.value small {
  margin-left: 2px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: auto;
  font-size: 12px;
}

.delta {
  color: var(--tone);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.arrow {
  font-size: 10px;
}

.date {
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}
</style>
```

- [ ] **Step 4: 新建 `frontend/src/views/parts/macro/MacroTicker.vue`**

```vue
<script setup lang="ts">
/**
 * 指标带滑动容器：放不下时横向滚动。
 * 滚轮：悬停时纵向滚轮转成横向；已到对应端点就不拦截，让页面照常上下滚动。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"
import MacroTickerTile from "./MacroTickerTile.vue"
import type { TileView } from "../../../api/macroTiles"

const props = withDefaults(defineProps<{ tiles: readonly TileView[]; loading?: boolean }>(), { loading: false })

/** 单格宽 172 + 间距 10 */
const STEP = 182

const track = ref<HTMLElement | null>(null)
const canLeft = ref(false)
const canRight = ref(false)

function refresh(): void {
  const element = track.value
  if (!element) {
    return
  }
  canLeft.value = element.scrollLeft > 1
  canRight.value = element.scrollLeft + element.clientWidth < element.scrollWidth - 1
}

function page(direction: 1 | -1): void {
  const element = track.value
  if (!element) {
    return
  }
  element.scrollBy({ left: direction * Math.max(STEP, element.clientWidth - STEP), behavior: "smooth" })
}

function onWheel(event: WheelEvent): void {
  const element = track.value
  if (!element) {
    return
  }
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
  if (delta === 0) {
    return
  }
  const atStart = element.scrollLeft <= 0
  const atEnd = element.scrollLeft + element.clientWidth >= element.scrollWidth - 1
  if ((delta < 0 && atStart) || (delta > 0 && atEnd)) {
    return
  }
  event.preventDefault()
  element.scrollLeft += delta
}

function onKey(event: KeyboardEvent): void {
  if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") {
    return
  }
  event.preventDefault()
  track.value?.scrollBy({ left: event.key === "ArrowRight" ? STEP : -STEP, behavior: "smooth" })
}

let observer: ResizeObserver | null = null

onMounted(() => {
  refresh()
  if (typeof ResizeObserver !== "undefined" && track.value) {
    observer = new ResizeObserver(refresh)
    observer.observe(track.value)
  }
})

onBeforeUnmount(() => {
  observer?.disconnect()
})

watch(
  () => [props.tiles.length, props.loading],
  () => {
    void nextTick(refresh)
  },
)
</script>

<template>
  <section class="ticker" aria-label="美国宏观指标">
    <button v-show="canLeft" type="button" class="arrow left" aria-label="向左滚动指标" @click="page(-1)">‹</button>
    <div ref="track" class="track" role="list" tabindex="0" @wheel="onWheel" @scroll.passive="refresh" @keydown="onKey">
      <template v-if="loading">
        <div v-for="index in 8" :key="index" class="skeleton" aria-hidden="true" />
      </template>
      <template v-else>
        <MacroTickerTile v-for="tile in tiles" :key="tile.key" :tile="tile" />
      </template>
    </div>
    <button v-show="canRight" type="button" class="arrow right" aria-label="向右滚动指标" @click="page(1)">›</button>
  </section>
</template>

<style scoped>
.ticker {
  position: relative;
}

.track {
  display: flex;
  gap: 10px;
  overflow-x: auto;
  scroll-snap-type: x proximity;
  scrollbar-width: none;
  outline: none;
}

.track::-webkit-scrollbar {
  display: none;
}

.track:focus-visible {
  box-shadow: 0 0 0 2px var(--accent);
  border-radius: var(--radius-sm);
}

.skeleton {
  flex: 0 0 172px;
  height: 112px;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, rgb(96 140 220 / 6%), rgb(96 140 220 / 14%), rgb(96 140 220 / 6%));
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}

@keyframes shimmer {
  from {
    background-position: 100% 0;
  }

  to {
    background-position: -100% 0;
  }
}

.arrow {
  position: absolute;
  top: 50%;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 44px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel);
  color: var(--text);
  font-size: 20px;
  line-height: 1;
  transform: translateY(-50%);
  cursor: pointer;
  box-shadow: 0 4px 14px rgb(0 0 0 / 35%);
}

.arrow:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.arrow.left {
  left: -6px;
}

.arrow.right {
  right: -6px;
}

@media (prefers-reduced-motion: reduce) {
  .skeleton {
    animation: none;
  }
}
</style>
```

- [ ] **Step 5: 运行，确认通过**

```powershell
npx vitest run tests/unit/macroTicker.test.ts
```

Expected: 5 passed。若 `v-show` 隐藏的箭头导致 `aria-label` 查询失败，检查是否用了 `v-if`——必须用 `v-show`（元素保留在 DOM，测试与读屏都能找到）。

- [ ] **Step 6: 提交**

```powershell
git add frontend/src/views/parts/macro frontend/tests/unit/macroTicker.test.ts
git commit -m "新增可横向滑动的宏观指标带组件：滚轮横移、两侧箭头、键盘方向键与骨架屏"
```

---

### Task 7: `MacroView.vue` 接入指标带与批量取数

**Files:**
- Modify: `frontend/src/views/MacroView.vue`
- Modify: `frontend/e2e/core.spec.ts:63-67`

**Interfaces:**
- Consumes: Task 4 `GET /api/v1/macro/observations` 与目录项 `provider` / `source_key`；Task 5 `TILE_SPECS`、`buildTile`、`tileCodes`、`applyTransform`、`periodText`、`Observation`、`TileCatalogItem`、`TileView`；Task 6 `MacroTicker`
- Produces: 页面行为（无新导出）

- [ ] **Step 1: 替换导入（文件开头 `import { ... } from "../api/macro"` 到 `import MiniSpark ...` 这段）**

原文：

```ts
import {
  adjustmentLabel,
  cssColor,
  frequencyLabel,
  macroSourceLabel,
  parseEvidence,
  regimeText,
  unitLabel,
  yearOverYear,
} from "../api/macro"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
```

替换为：

```ts
import { cssColor, frequencyLabel, macroSourceLabel, parseEvidence, regimeText } from "../api/macro"
import {
  applyTransform,
  buildTile,
  periodText,
  TILE_SPECS,
  tileCodes,
  type Observation,
  type TileCatalogItem,
  type TileView,
} from "../api/macroTiles"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MacroTicker from "./parts/macro/MacroTicker.vue"
```

同时把文件顶部 docstring 中「指标带只显示真实观测值；CPI / 核心 PCE 的同比按「12 个月前同月」的观测值计算，找不到就不显示。」改为「指标带口径见 api/macroTiles.ts：同比按 12 个月前同月、非农为当月新增、实际 GDP 为环比折年率，缺数据就不显示。」，并把 `GET /api/v1/macro/series/{series_code}/observations` 那段说明改为：

```ts
 *  - GET /api/v1/macro/observations?codes=&as_of=
 *      data = { pit_date, items: [{ series_code, observations[], empty_message, truncated }] }
 *      一次取回指标带与走势图所需的全部序列；limit 由后端按频率决定，observations 升序。
```

- [ ] **Step 2: 类型与常量**

删除 `interface CatalogItem { ... }`、`interface Observation { ... }`、`interface TileView { ... }` 三个定义，改为一行：

```ts
type CatalogItem = TileCatalogItem
```

删除以下常量块：

```ts
/** 指标带顺序与效果图一致。 */
const TILE_CODES = ["GDP", "CPIAUCSL", "PCEPILFE", "UNRATE", "FEDFUNDS", "DGS10", "T10Y2Y", "DGS2"] as const

/** 价格指数在指标带与通胀图里统一换算成同比。 */
const YOY_CODES = new Set<string>(["CPIAUCSL", "PCEPILFE"])
const YOY_LABELS: Record<string, string> = { CPIAUCSL: "CPI 同比", PCEPILFE: "核心 PCE 同比" }
```

删除 `const yoyByCode = computed<...>(() => { ... })` 整段，删除 `function monthText(...) { ... }` 整段。

- [ ] **Step 3: 指标带视图**

把 `const tiles = computed<TileView[]>(() => TILE_CODES.map((code) => { ... }),)` 整段替换为：

```ts
const tileViews = computed<TileView[]>(() =>
  TILE_SPECS.map((spec) => buildTile(spec, observationsByCode.value, catalog.value)),
)
```

`evidenceSummary` 中 `monthText(point.date, frequency)` 改为 `periodText(point.date, frequency)`。

- [ ] **Step 4: 走势图指标改为百分比口径**

`palette()` 的返回对象追加三种颜色：

```ts
    teal: "#2dd4bf",
    sky: "#38bdf8",
    lime: "#a3e635",
```

把从 `/**\n * 走势图可聚合的指标（与指标带一一对应，共 8 个）。` 开始，到 `const PLOT_SPECS ... ]` 结束的整段替换为：

```ts
/**
 * 走势图可聚合的指标：只放百分比口径，共用一根左轴（PMI、非农、初请留给后续的指数化对比）。
 *  - transform 与指标带同一套换算（api/macroTiles.ts），保证两处数字对得上；
 *  - step：政策利率、实际 GDP 是「公布后一直有效」的阶梯，其余按观测点连线。
 * 不同频率（日 / 周 / 月 / 季）都放在时间轴上，各画各的观测点，不插值、不补点。
 */
type PlotTone = "accent" | "orange" | "violet" | "warn" | "ok" | "cyan" | "pink" | "muted" | "teal" | "sky" | "lime"

interface PlotSpec {
  code: string
  label: string
  tone: PlotTone
  transform: "level" | "yoy" | "saar"
  step: boolean
}

const PLOT_SPECS: readonly PlotSpec[] = [
  { code: "CPIAUCSL", label: "CPI 同比", tone: "accent", transform: "yoy", step: false },
  { code: "PCEPILFE", label: "核心 PCE 同比", tone: "orange", transform: "yoy", step: false },
  { code: "UNRATE", label: "失业率", tone: "violet", transform: "level", step: false },
  { code: "CES0500000003", label: "平均时薪同比", tone: "pink", transform: "yoy", step: false },
  { code: "GDPC1", label: "实际 GDP 环比折年", tone: "muted", transform: "saar", step: true },
  { code: "PCEC96", label: "实际 PCE 同比", tone: "teal", transform: "yoy", step: false },
  { code: "DFEDTARU", label: "政策利率上限", tone: "warn", transform: "level", step: true },
  { code: "DGS5", label: "5 年期美债", tone: "sky", transform: "level", step: false },
  { code: "DGS10", label: "10 年期美债", tone: "ok", transform: "level", step: false },
  { code: "DGS20", label: "20 年期美债", tone: "lime", transform: "level", step: false },
  { code: "T10Y2Y", label: "10Y−2Y 利差", tone: "cyan", transform: "level", step: false },
]

/** 通胀图的 2% 目标线挂在第一条价格指数同比上。 */
const INFLATION_CODES = new Set<string>(["CPIAUCSL", "PCEPILFE"])

/** 页面一次性取回的序列：指标带 ∪ 走势图（共 16 条，不超过批量接口上限 20）。 */
const OBSERVATION_CODES: readonly string[] = [...new Set([...tileCodes(), ...PLOT_SPECS.map((spec) => spec.code)])]
```

`ratesTrend` 默认值改为：

```ts
const ratesTrend = ref<TrendState>({ codes: ["DFEDTARU", "DGS5", "DGS10", "DGS20"], years: 2 })
```

`plotRows` 整个函数替换为：

```ts
function plotRows(spec: PlotSpec): Observation[] {
  return applyTransform(spec.transform, rowsOf(spec.code))
}
```

`toneColor` 的 `map` 追加：

```ts
    teal: colors.teal,
    sky: colors.sky,
    lime: colors.lime,
```

`pickerOptions` 中 `hint` 改为：

```ts
      hint: item ? `${item.nameEn || item.name} · ${frequencyLabel(item.frequency)}` : spec.label,
```

删除 `function amountTip(...) { ... }`。

`buildTrend` 整个函数替换为：

```ts
/** 把选中的任意多个指标聚合进同一张图。 */
function buildTrend(state: TrendState): TrendPlot {
  const picked = PLOT_SPECS.filter((spec) => state.codes.includes(spec.code))
    .map((spec) => ({ spec, rows: plotRows(spec) }))
    .filter((entry) => entry.rows.length > 1)
  if (!picked.length) {
    return { option: null, latest: [] }
  }
  const colors = palette()
  const newest = picked.reduce((high, entry) => {
    const last = entry.rows[entry.rows.length - 1]!.date
    return last > high ? last : high
  }, "")
  const cutoff = shiftYears(newest, state.years)
  const windowed = picked.map((entry) => ({ ...entry, rows: entry.rows.filter((row) => row.date >= cutoff) }))
  const targetOwner = windowed.find((entry) => INFLATION_CODES.has(entry.spec.code))?.spec.code

  const option: EChartsCoreOption = {
    grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "time", axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: "{value}%" } },
    series: windowed.map(({ spec, rows }) => {
      const color = toneColor(spec.tone, colors)
      return {
        name: spec.label,
        type: "line",
        step: spec.step ? "end" : undefined,
        showSymbol: false,
        connectNulls: true,
        data: rows.map((row) => [row.date, row.value]),
        tooltip: { valueFormatter: percentTip },
        lineStyle: { color, width: spec.step ? 2 : 1.7 },
        itemStyle: { color },
        markLine:
          spec.code === targetOwner
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { color: colors.muted, type: "dashed" },
                label: { color: colors.muted, fontSize: 11, formatter: "通胀目标 2%", position: "insideEndTop" },
                data: [{ yAxis: 2 }],
              }
            : undefined,
      }
    }),
  }

  const latest = windowed.flatMap(({ spec, rows }) => {
    const last = rows[rows.length - 1]
    if (!last) {
      return []
    }
    const shown = periodText(last.date, catalogItem(spec.code)?.frequency ?? "")
    return [`${spec.label} ${shown} ${percentTip(last.value)}`]
  })
  return { option, latest }
}
```

删除 `PlotKind` 类型（若仍有残留引用，一并删除）。

- [ ] **Step 5: 目录解析带上数据源**

`parseCatalog` 返回对象追加两个字段：

```ts
        provider: asString(item.provider) ?? "fred",
        sourceKey: asString(item.source_key) ?? "",
```

- [ ] **Step 6: 批量取数**

删除 `function limitFor(...) { ... }`，把 `loadObservations` 整个函数替换为：

```ts
async function loadObservations(): Promise<void> {
  observationsBusy.value = true
  const codes = OBSERVATION_CODES.filter((code) => catalog.value.some((item) => item.code === code))
  try {
    if (!codes.length) {
      observationsByCode.value = {}
      return
    }
    const params = new URLSearchParams({ codes: codes.join(",") })
    if (appliedAsOf.value) {
      params.set("as_of", appliedAsOf.value)
    }
    const response = await fetch(apiUrl(`/api/v1/macro/observations?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const next: Record<string, Observation[]> = {}
    for (const item of listOf(data?.items)) {
      const code = asString(item.series_code)
      if (!code) {
        continue
      }
      const rows = Array.isArray(item.observations) ? item.observations.flatMap(parseObservation) : []
      rows.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
      next[code] = rows
    }
    observationsByCode.value = next
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  } finally {
    observationsBusy.value = false
  }
}
```

（`scaleAmount` 仍被 `evidenceValue` 使用，保留其导入；`asNumber`、`listOf` 保持导入。）

- [ ] **Step 7: 模板**

把整个 `<div class="grid-8"> ... </div>`（含 `MetricTile`、`MiniSpark`、`DeltaValue`）替换为：

```vue
        <MacroTicker :tiles="tileViews" :loading="observationsBusy && !hasAnyObservation" />
```

通胀面板 `info` 改为：

```vue
              info="默认 CPI 与核心 PCE 的同比（%）；点上方色块可加入或移除任意百分比口径指标（不限数量）。同比 = 当月 ÷ 12 个月前同月 − 1；实际 GDP 为环比折年率"
```

利率面板 `title` 改为 `政策利率与美债收益率`，`info` 改为：

```vue
              info="默认联邦基金目标区间上限与 5、10、20 年期美债收益率（均为日度）；点上方色块可加入或移除任意指标（不限数量），不同频率按时间轴对齐、不插值"
```

`SourceFooter` 改为：

```vue
        <SourceFooter
          :as-of="latestObservationDate || appliedAsOf"
          :sources="[sourceLabel, '东方财富数据中心（ISM PMI）']"
          :stale="stale"
          note="「较上期」为相邻两期主口径之差：利率、同比类按百分点，非农与初请按千人，PMI 按点；政策利率按上一次调整计。"
        />
```

数据说明弹窗的 `<ul class="doc-list"> ... </ul>` 整体替换为：

```vue
      <ul class="doc-list">
        <li>数据来源：{{ sourceLabel }}；ISM PMI 来自东方财富数据中心（转载 ISM）。观察日 {{ appliedAsOf }}。</li>
        <li v-if="availability === 'public_csv'">
          公开 CSV 只提供最新修订值，不能回看历史某天当时公布的版本；选择过去的观察日将看不到数据。
        </li>
        <li>CPI、核心 PCE、平均时薪、实际个人消费支出显示同比：当月 ÷ 12 个月前同月 − 1；缺少上年同月数据的月份不显示。</li>
        <li>非农就业显示当月新增（本月 − 上月，千人）；初请失业金为周度人数，换算成千人。</li>
        <li>实际 GDP 显示环比折年率：(本季 ÷ 上季)^4 − 1，与美国经济分析局公布口径一致。</li>
        <li>美联储政策利率显示联邦基金目标区间（下限–上限），「较上期」按上一次调整计。</li>
        <li>ISM PMI 历史值为东方财富当前值（最新修订）；本系统自首次同步起记录修订，此后回看观察日才与当时公布值一致。</li>
        <li>利率、失业率、利差与同比类的「较上期」单位为百分点（pct），PMI 为点；颜色只表示最新一期的方向，不代表好坏。</li>
        <li>
          宏观象限与规则解读：比较 GDP、CPI、联邦基金利率最近两期已公布值的方向，上行记 70、下行记 30、持平记
          50；联邦基金利率上行视为流动性偏紧。
        </li>
      </ul>
```

- [ ] **Step 8: e2e 用例 04 等批量接口**

`frontend/e2e/core.spec.ts` 中：

```ts
          reply.url().includes("/api/v1/macro/series/") && reply.url().includes("as_of=2024-06-01"),
```

改为：

```ts
          reply.url().includes("/api/v1/macro/observations") && reply.url().includes("as_of=2024-06-01"),
```

- [ ] **Step 9: 类型检查、单测、构建**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker\frontend
npm run typecheck
npm run test
npm run build
```

Expected: 三条都成功；`typecheck` 无报错（若提示 `MetricTile` / `DeltaValue` / `MiniSpark` / `scaleAmount` 未使用或 `yearOverYear` 残留，按提示删掉对应导入或代码）。

- [ ] **Step 10: 提交**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker
git add frontend/src/views/MacroView.vue frontend/e2e/core.spec.ts
git commit -m "美国经济页接入 15 格滑动指标带，观测改为批量接口一次取回，走势图切到新口径"
```

---

### Task 8: 文档与端到端验证

**Files:**
- Modify: `docs/API.md:77`
- Modify: `docs/IMPLEMENTATION_LOG.md`（末尾追加）

- [ ] **Step 1: `docs/API.md`**

在第 77 行（单序列 observations 说明）之后插入：

```markdown
`GET /api/v1/macro/observations?codes=UNRATE,ISM_MFG&as_of=2026-10-17` 一次取回多条序列在该观察日可见的观测，`data = { pit_date, items[] }`，`items` 顺序与请求一致，每项结构同单序列接口。`codes` 1–20 个，不认识的代码返回 422 且 `error.details.unknown_codes` 列出；limit 由频率决定（日度 1000、周度 260、月 / 季度 120）。

美国宏观目录共 19 条：FRED 17 条 + 东方财富数据中心 `RPT_ECONOMICVALUE_USA` 转载的 ISM 制造业（`ISM_MFG` / `EMG00002790`）与服务业（`ISM_SVC` / `EMG00002791`）PMI。目录项带 `provider`（`fred` / `eastmoney`）与 `source_key`（东方财富指标 ID）。ISM 以发布日为 `realtime_start`，与 FRED 同样次日可见；来源只给最新值，首次同步前的历史为最新修订值，之后每次同步发现变化即新增修订版本。同步仍用 `fred_series` 任务，按序列的数据源分派。
```

- [ ] **Step 2: `docs/IMPLEMENTATION_LOG.md` 追加一节**

先看文件末尾一节的标题层级与格式，按同样格式追加，内容包括：

```markdown
## 2026-10-17 · 美国宏观数据扩充与指标带滑动（feat/macro-us-ticker）

- 变更文件：`backend/app/catalog/macro_series.py`、`providers/eastmoney_us_macro.py`（新）、`providers/latest_revisions.py`（新）、`providers/fred.py`、`providers/fred_public.py`、`repositories/macro.py`、`services/fred_sync.py`、`services/macro_batch.py`（新）、`schemas/macro.py`、`api/v1/macro.py`、`core/config.py`、`scripts/bootstrap_data.py`；`frontend/src/api/macroTiles.ts`（新）、`api/macro.ts`、`views/parts/macro/MacroTicker*.vue`（新）、`views/MacroView.vue`、`e2e/core.spec.ts`。
- 迁移：无（沿用 macro_series / macro_observations 现有字段）。
- 新增用例：后端 `test_eastmoney_us_macro.py`、`test_macro_batch.py`、`test_fred.py` 追加、集成 `test_us_macro_eastmoney.py`；前端 `macroTiles.test.ts`、`macroTicker.test.ts`、`macro.test.ts` 追加。
- 测试结果：<粘贴 Step 3 的实际输出摘要；集成测试若因无 PostgreSQL 被跳过，写明 skipped>。
- 已知局限：ISM 首次同步前的历史为东方财富最新修订值；东方财富为页面匿名接口，字段变化会以 UPSTREAM_PAYLOAD 失败并保留已入库数据。
- 下一项建议：子项目 3（通胀走势图 + 多指标对比分析，2020-01=100 指数化、双轴、导出）。
```

（「测试结果」一行必须填 Step 3 的真实输出，不得留尖括号。）

- [ ] **Step 3: 全量验证**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker\backend
$py = "d:\finscrope\finscope-ai\backend\.venv\Scripts\python.exe"
& $py -m ruff check .
& $py -m ruff format --check .
& $py -m mypy app
& $py -m pytest -q -rs
cd ..\frontend
npm run typecheck
npm run test
npm run build
```

Expected: 全部通过；记录 pytest 的 passed / skipped 数量与 vitest 的用例数。

- [ ] **Step 4: 本地运行与截图核对**

前提：本地 PostgreSQL 与后端配置可用（工作树没有 `.env`，按主目录启动方式提供环境变量或复制本机 `.env` 到工作树，**不要提交**）。

```powershell
cd d:\finscrope\finscope-ai-macro-ticker\backend
& $py -m app.scripts.bootstrap_data --only fred
```

Expected: 打印 `[美国宏观] 19 条序列（FRED + 东方财富 ISM）`，每条写入条数 > 0（`ISM_MFG` / `ISM_SVC` 约 70 条，六年窗口）。

启动后端与前端（与主目录相同的命令，例如 `& $py -m uvicorn app.main:app --port 8000` 与 `npm run dev`），浏览器打开 `http://localhost:5173/macro/us`，核对：

1. 指标带 15 格，顺序与 spec §2.1 一致；悬停滚轮可横移，到两端后页面恢复纵向滚动；两侧箭头按位置显隐。
2. 上升格为红、下降格为绿；ISM 两格 `?` 提示含 `EMG0000279x` 与「最新修订」。
3. 观察日改为过去日期后，网络面板只发一次 `/api/v1/macro/observations?...as_of=...`。
4. 两张走势图默认组合正确（CPI + 核心 PCE；政策利率上限 + 5Y/10Y/20Y）。

截图保存并在最终报告中附上。数据库不可用时，如实写明「未做真实数据运行验证」，只报告自动化测试结果。

- [ ] **Step 5: 提交**

```powershell
cd d:\finscrope\finscope-ai-macro-ticker
git add docs/API.md docs/IMPLEMENTATION_LOG.md docs/superpowers/specs/2026-10-17-macro-us-ticker-design.md
git commit -m "补充批量观测接口与 ISM 数据源说明，记录本轮实施日志"
```
