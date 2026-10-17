# 美国经济页改版 · 子项目 1+2：宏观数据扩充与指标带滑动复刻

- 日期：2026-10-17
- 分支 / 工作树：`feat/macro-us-ticker` · `d:\finscrope\finscope-ai-macro-ticker`
- 页面：`/macro/us`（`frontend/src/views/MacroView.vue`）
- 参考图：用户提供的「全球宏观分析 / 美国经济」效果图（顶部 12 格滑动指标带 + 通胀走势 + 宏观象限轨迹 + AI 解读 + 对比分析 + 情景推演）

## 1. 背景与整体拆分

用户要求整页按参考图复刻。整页涉及多个相互独立的子系统，拆为 5 个子项目，各自走「设计 → 计划 → 实现」：

| 序号 | 子项目 | 依赖 | 本文档 |
|---|---|---|---|
| 1 | 数据层扩充：FRED 新增 8 条序列 + 东方财富 ISM 两条 PMI + 批量观测接口 | — | ✅ 覆盖 |
| 2 | 顶部指标带：15 格、横向滑动、参考图样式 | 1 | ✅ 覆盖 |
| 3 | 通胀走势图 + 多指标对比分析（区间、频率、导出、2020-01=100 指数化、双轴） | 1 | 后续 |
| 4 | 宏观象限升级：横轴实际 GDP 增速、纵轴通胀压力，画最新点 + 1 年 / 3 年轨迹 | 1 | 后续 |
| 5 | AI 宏观解读（已配置模型 + 入库数据，标注模型生成）+ 情景推演（概率用历史象限转移频率、区间用历史分位数，禁止凭空写概率） | 1、4 | 后续 |

参考图中「基准 60% / 逆风 25% / 乐观 15%」这类无出处的概率违反 AGENTS.md「不编造数据」，子项目 5 必须改为可复现的统计口径。

## 2. 本次目标与验收点

1. 指标带展示 15 个指标，顺序固定如下表；超出宽度可横向滑动（滚轮、箭头、触屏拖动、键盘方向键）。
2. 每格显示：名称 + 口径小字、迷你走势、主数值、▲/▼ 较上期、最新观测日期；颜色按最新一期方向：上升红、下降绿、持平灰。
3. 新增序列可通过现有同步任务 / `bootstrap_data` 入库，ISM 来自东方财富并带发布日，观察日过滤生效。
4. 页面观测数据改为一次批量请求取回。
5. loading / empty / error / stale 四态齐全；单格缺数据只影响该格。
6. 后端、前端均有正常与缺数据 / 异常用例，lint、typecheck、tests 通过。

### 2.1 指标与口径

| # | 卡片 | 序列代码 | 来源 | 主数值 | 较上期 |
|---|---|---|---|---|---|
| 1 | 核心 PCE | `PCEPILFE` | FRED（已有） | 同比 % | pct |
| 2 | CPI | `CPIAUCSL` | FRED（已有） | 同比 % | pct |
| 3 | 非农就业 | `PAYEMS` | FRED（已有） | 当月新增（千人）= 本月 − 上月 | 千人 |
| 4 | 失业率 | `UNRATE` | FRED（已有） | % | pct |
| 5 | 平均时薪 | `CES0500000003` | FRED（新增） | 同比 % | pct |
| 6 | 实际 GDP | `GDPC1` | FRED（新增） | 环比折年率 % = ((本季 ÷ 上季)^4 − 1) × 100 | pct |
| 7 | 实际个人消费支出 | `PCEC96` | FRED（新增） | 同比 % | pct |
| 8 | ISM 服务业 PMI | `ISM_SVC` | 东方财富 `EMG00002791`（新增） | 指数点 | 点 |
| 9 | ISM 制造业 PMI | `ISM_MFG` | 东方财富 `EMG00002790`（新增） | 指数点 | 点 |
| 10 | 美联储政策利率 | `DFEDTARL` + `DFEDTARU` | FRED（新增） | 目标区间「下限–上限 %」 | 上限的 pct 变化 |
| 11 | 5 年期美债 | `DGS5` | FRED（新增） | % | pct |
| 12 | 10 年期美债 | `DGS10` | FRED（已有） | % | pct |
| 13 | 20 年期美债 | `DGS20` | FRED（新增） | % | pct |
| 14 | 10Y−2Y 利差 | `T10Y2Y` | FRED（已有） | pct | pct |
| 15 | 初请失业金人数 | `ICSA` | FRED（新增） | 周度，千人 | 千人 |

从指标带撤下：名义 GDP（`GDP`）、2 年期美债（`DGS2`）、联邦基金有效利率（`FEDFUNDS`）。三者仍留在目录与数据库里，宏观规则（`macro_rules.py` 依赖 GDP / CPI / FEDFUNDS）不受影响。

「较上期」统一取主数值口径下相邻两期之差：同比类为同比的 pct 差，非农为当月新增之差，折年率为折年率之差，PMI 为点差。只和紧邻上一期比：月度差 1 个月、季度差 3 个月、周度差 7 天，日度取前一个观测点；中间缺期显示「—」，频率未知不计算。政策利率取最近一次调整的变化，取回窗口内没有调整时显示「—」。

## 3. 后端设计

### 3.1 序列目录 `backend/app/catalog/macro_series.py`

`MacroSeriesSeed` 新增字段：

- `provider: Literal["fred", "eastmoney"] = "fred"`
- `source_key: str = ""`：东方财富指标 ID；FRED 序列留空（序列代码即 FRED ID）。

新增函数 `fred_codes()`（仅 `provider == "fred"`）与 `seed_for(series_code) -> MacroSeriesSeed | None`。`catalog_codes()` 保持返回全部代码，供接口校验。

新增 seed：

| series_code | name_cn | category | frequency | base_unit | seasonal_adjustment | source_agency |
|---|---|---|---|---|---|---|
| `CES0500000003` | 平均时薪（私营非农） | labor | Monthly | Dollars per Hour | Seasonally Adjusted | U.S. Bureau of Labor Statistics |
| `GDPC1` | 实际国内生产总值 | output | Quarterly | Billions of Chained 2017 Dollars | Seasonally Adjusted Annual Rate | U.S. Bureau of Economic Analysis |
| `PCEC96` | 实际个人消费支出 | consumption | Monthly | Billions of Chained 2017 Dollars | Seasonally Adjusted Annual Rate | U.S. Bureau of Economic Analysis |
| `DGS5` | 美国5年期国债收益率 | rates | Daily | Percent | Not Seasonally Adjusted | Board of Governors of the Federal Reserve System (US) |
| `DGS20` | 美国20年期国债收益率 | rates | Daily | Percent | Not Seasonally Adjusted | 同上 |
| `DFEDTARL` | 联邦基金目标利率下限 | rates | Daily | Percent | Not Seasonally Adjusted | 同上 |
| `DFEDTARU` | 联邦基金目标利率上限 | rates | Daily | Percent | Not Seasonally Adjusted | 同上 |
| `ICSA` | 初请失业金人数 | labor | Weekly | Number | Seasonally Adjusted | U.S. Employment and Training Administration |
| `ISM_MFG` | ISM 制造业 PMI | sentiment | Monthly | Index | Seasonally Adjusted | Institute for Supply Management（经东方财富） |
| `ISM_SVC` | ISM 服务业 PMI | sentiment | Monthly | Index | Seasonally Adjusted | Institute for Supply Management（经东方财富） |

`base_unit` / `unit` 列为 `String(32)`；最长值 `Billions of Chained 2017 Dollars` 恰为 32 字符，需在 seed 测试里断言 `len <= 32`。

### 3.2 FRED 侧改动

`providers/fred.py`、`providers/fred_public.py`、`services/fred_sync.py`、`api/v1/jobs.py`、`scripts/bootstrap_data.py` 中原先对 `catalog_codes()` 的判断改为 `fred_codes()`，避免把 `ISM_*` 发往 FRED。

### 3.3 东方财富美国宏观 provider `backend/app/providers/eastmoney_us_macro.py`

只做 URL 构造与解析（与 `eastmoney_rates.py` 同风格），HTTP 超时 / 重试 / 限速复用现有东方财富工具。

- 接口：`https://datacenter-web.eastmoney.com/api/data/v1/get`，`reportName=RPT_ECONOMICVALUE_USA`，`columns=REPORT_DATE,PUBLISH_DATE,VALUE,INDICATOR_ID`，`filter=(INDICATOR_ID="<id>")`，按 `REPORT_DATE` 倒序，`pageSize` 300（2008-01 起约 226 行，2026-10 实测）。
- 解析为 `UsMacroPoint(observation_date, published_on, value: Decimal)`：
  - `observation_date` = `REPORT_DATE` 的日期（所属月首日）。
  - `VALUE` 为 null 的行是财经日历预排（`PUBLISH_DATE` 在未来），跳过。
  - 缺 `REPORT_DATE` / `PUBLISH_DATE` 或数值不可解析的行跳过；报文结构异常抛结构化 provider 错误。
  - 没有一行能解析（含 `data: null`、空列表）时抛 `UPSTREAM_PAYLOAD`，不当作「无数据」静默写 0 行。
  - 同一观测月多行时只留 `PUBLISH_DATE` 最晚的一行。
- 可见性与 FRED 完全一致：`realtime_start = PUBLISH_DATE`，发布日当天不可见、纽约次日零点起可见（`vintage_visible` / `published_at_from_vintage`），不另立时刻规则。

### 3.4 入库与修订 `repositories/macro.py`

- `ensure_catalog` 按 seed 的 `provider` 关联 `data_providers`：东方财富用 `uuid5(NAMESPACE_URL, "https://finscope.local/providers/eastmoney-us-macro")` 固定 ID。
- 纯函数 `plan_latest_revisions(existing, fresh, fetched_on)`（`providers/latest_revisions.py`）把「只有最新值」的来源换成版本行（`FredVintage`），再交给现有 `upsert_vintages` 入库，复用其去重键与修订编号：
  - 某观测月无记录 → 一行 `realtime_start = published_on`、`realtime_end = 9999-12-31`。
  - 当前有效版本（`realtime_end = 9999-12-31`）值相同 → 不写（幂等）。
  - 值不同（ISM 每年 1 月重估季节因子会改写历史）→ 旧版本 `realtime_end = fetched_on`（纽约日历日），新增 `realtime_start = fetched_on` 的开放版本；若 `fetched_on <= 旧版本起点` 则直接改写旧版本的值。可见性为 `realtime_start < pit <= realtime_end`，所以旧值看到抓取日当天，新值从次日起可见，中间不留空档（`fetched_on − 1 天` 会让抓取日当天两版都看不到）。
  - FRED 原生版本仍沿用 FRED 自己的 `realtime_end = 下一版起点 − 1 天`，FRED 修订的观测在修订当天不可见；这是既有行为，不在本次范围。
- 新方法 `upsert_latest_values(series_code, points, fetched_at)`：读已有版本 → 调用上面的纯函数 → `upsert_vintages`。
- `source_record_key` 沿用现有格式 `{series_code}|{观测日}|{realtime_start}`；东方财富指标 ID 记在序列 `metadata_json.source_key` 与 `source_url`。
- 东方财富只提供最新修订值、无历史快照：首次同步之前的历史只能是最新修订值，从首次同步起由本系统自行积累版本。

### 3.5 同步分派

沿用现有任务类型 `fred_series`（含义扩为「美国宏观序列同步」），不新增任务类型：`services/fred_sync.py::sync_fred_series` 按 seed 的 `provider` 分派——FRED 走原 `fetch`，东方财富走新参数 `fetch_latest`（默认真实请求，测试可注入），只保留观测窗内的点，再 `upsert_latest_values`。worker、管理接口、定时任务、`bootstrap_data` 因此无需改调用方式。配套：

- `schemas/macro.py::FredSyncRequest.series_code` 正则放宽为 `^[A-Z0-9_]+$`（`ISM_MFG` 含下划线）。
- `core/config.py::scheduler_fred_series` 默认值追加 10 个新代码，定时任务每天一并同步。
- 东方财富不可用时任务返回结构化错误，已入库数据不变。

序列目录接口 `GET /macro/series` 每项追加 `provider`、`source_key`，前端据此写来源说明。

### 3.6 批量观测接口

`GET /api/v1/macro/observations?codes=UNRATE,DGS10,...&as_of=YYYY-MM-DD`

- `codes`：逗号分隔，1–20 个，去重；任一不在 `catalog_codes()` → 422 `VALIDATION_ERROR`，`details` 列出未知代码。
- 每条序列 limit 按频率：Daily 1000、Weekly 260、Monthly / Quarterly 120。
- 响应 `data = { pit_date, items: [{ series_code, time_precision, observations, empty_message, truncated }] }`，`items` 顺序与请求一致；单条结构与现有单序列接口相同，复用 `build_observations` 与同一套可见性过滤（`published_at <= as_of`）。
- 路由只做参数解析，组装逻辑放 `services/macro_batch.py`。仓库新增 `list_vintages_many(codes)`：只做一次 `ensure_catalog` 和一次联表查询（单序列 `list_vintages` 每次都会同步目录，循环调用 20 次代价过高）。单序列接口保留。

## 4. 前端设计

### 4.1 口径计算模块 `frontend/src/api/macroTiles.ts`（纯函数，无副作用）

```ts
type TileTransform = "level" | "yoy" | "diff" | "saar" | "range"

interface TileSpec {
  key: string            // 卡片唯一键
  codes: string[]        // range 用两条：[下限, 上限]
  label: string          // 「核心 PCE」
  caption: string        // 「同比 (%)」「新增 (千人)」「(点)」
  transform: TileTransform
  unit: "%" | "pct" | "千人" | "点"
  deltaSuffix: string    // " pct" / " 千人" / " 点"
  scale?: number         // ICSA 人数 → 千人：0.001
}

interface TileView {
  key: string; label: string; caption: string
  value: string          // 已格式化；无数据为 "—"
  date: string           // 最新观测日（日度/周度 YYYY-MM-DD，月度 YYYY-MM，季度 YYYY Qn）；无数据为 "暂无数据"
  unit: string           // 数值后的单位小字
  delta: number | null   // 已按展示位数取整；-0 归一为 0
  deltaText: string      // 「+0.10 pct」「-6 千人」；无数据为「—」
  direction: "up" | "down" | "flat" | "none"
  spark: number[]        // 主数值口径的最近 N 期（日度 120、周度 52、月度 36、季度 20）
  info: string           // 代码 / 来源 / 单位 / 季调 / 公式；ISM 追加「历史值为最新修订值」
}

export const TILE_SPECS: readonly TileSpec[]
export function buildTile(spec: TileSpec, rowsByCode: Record<string, Observation[]>, catalog: CatalogItem[]): TileView
```

- `yoy`：复用 `api/macro.ts` 的 `yearOverYear`（12 个月前同月，找不到则不出该期）。
- `diff`：相邻两期差（非农当月新增）。
- `saar`：相邻两季 `((x_t / x_{t-1})^4 − 1) × 100`，上季为 0 或缺失则不出该期。
- `range`：下限、上限按日期对齐取最近一个两者都有的日期，显示「4.00–4.25」；`delta` 用最近一次调整时上限的变化，窗口内没有调整为 `null`；迷你图用上限。
- `direction`：`delta > 0` up、`< 0` down、`=== 0` flat、`null` none。

### 4.2 组件

不改通用 `MetricTile.vue`（10+ 页面在用）。新建：

- `frontend/src/views/parts/macro/MacroTickerTile.vue`：单格。第一行：名称（加粗）+ 口径小字，右上迷你图（复用 `MiniSpark`）；中间大号数值；底部左 ▲/▼ + 较上期，右侧日期。`direction` 决定数值 / 三角 / 迷你图颜色：up 用 `--rise` 红、down 用 `--fall` 绿（沿用现有涨跌令牌，浅色主题自动适配）、flat / none 用 `--muted`。`?` 悬停显示 `info`。
- `frontend/src/views/parts/macro/MacroTicker.vue`：滑动容器。
  - 卡片固定宽 172px，`overflow-x: auto`，不做 scroll-snap（捕捉对齐会把滚轮横移吸回，手感发顿），隐藏原生滚动条。
  - 滚轮：判定放在纯函数 `frontend/src/views/parts/macro/tickerWheel.ts`。悬停时把纵向 `deltaY` 按 `deltaMode` 换算成像素后横向滚动并 `preventDefault`；Ctrl / 捏合缩放、横向为主的滑动、已到对应端点、内容不超宽时都不拦截，交给浏览器。
  - 箭头与方向键的平滑滚动用 CSS `scroll-behavior: smooth`，`prefers-reduced-motion: reduce` 时关闭。
  - 左右箭头按钮悬浮两侧，按滚动位置显隐，点击滚动一屏（`clientWidth − 一格宽`），`aria-label`「向左 / 向右滚动指标」。
  - 容器 `tabindex="0"`，左右方向键滚动一格；列表 `role="list"`、卡片 `role="listitem"`。
  - `loading` 时渲染 8 个骨架格。

### 4.3 `MacroView.vue` 改动

- 删除 `TILE_CODES`、`tiles` 计算与 `grid-8` 模板，改为 `<MacroTicker :tiles="tileViews" :loading="observationsBusy" />`，`tileViews = TILE_SPECS.map(buildTile)`。
- `loadObservations` 改调批量接口，代码集合 = 指标带所需代码 ∪ 走势图可选代码；按目录过滤掉后端未提供的代码。
- `PLOT_SPECS` 改为只含百分比口径指标：CPI 同比、核心 PCE 同比、失业率、平均时薪同比、实际 GDP 折年率、实际 PCE 同比、政策利率上限、5Y、10Y、20Y、10Y−2Y。PMI / 初请 / 非农留给子项目 3 的指数化对比。`plotRows` 的同比、折年率换算改用 `macroTiles.ts` 的同名函数，保证与指标带同口径。删掉仅为 GDP 右轴服务的 amount 分支。
- 默认组合：通胀图「CPI 同比 + 核心 PCE 同比」；利率图「政策利率上限 + 5Y + 10Y + 20Y」。
- 象限、规则解读、数据说明逻辑不改；数据说明补充新口径（非农当月新增、实际 GDP 折年率、ISM 来源与修订说明）。
- `frontend/e2e/core.spec.ts` 用例 04 等待的请求改为 `/api/v1/macro/observations`（原先等单序列接口）。

### 4.4 状态

| 状态 | 表现 |
|---|---|
| loading | 指标带骨架；图表沿用现有 loading |
| empty（单格） | 该格「—」+「暂无数据」，direction none |
| empty（全部） | 沿用现有 `emptyNotice` |
| error | 批量接口失败沿用整页错误 + 重试 |
| stale | 数据源 unavailable 或全部无观测时页脚标注过期（沿用 `SourceFooter`） |

## 5. 测试

后端（pytest，离线 fixture）：

- seed：新增 10 条字段齐全，`base_unit` 长度 ≤ 32，`ISM_*` 的 provider 为 eastmoney 且 `source_key` 非空；`fred_codes()` 不含 `ISM_*`。
- 东方财富解析：正常报文；含 `VALUE` null 的预排行被跳过；结构异常报文抛 provider 错误。
- 修订：同值重复同步幂等；值变化时关闭旧记录并新增修订行。
- 批量接口：正常返回顺序与请求一致；未知代码 422；`as_of` 早于 ISM 某期 `published_at` 时该期不可见。

前端（vitest）：

- `yoy` 找 12 个月前同月、缺上年同月不出值；`diff` 非农新增；`saar` 季度折年率；`range` 上下限按日期对齐；全缺数据返回「—」/「暂无数据」/ direction none；ICSA 换算千人。

验证命令：后端 `ruff`、`mypy`、`pytest`；前端 `npm run typecheck`、`npm run test`、`npm run build`；本地启动后同步新序列并打开 `/macro/us` 截图核对。

## 6. 不在本次范围

- 通胀走势图 / 对比分析改版、宏观象限轨迹、AI 解读与情景推演（子项目 3–5）。
- 指标带点击联动图表。
- 数据库迁移（现有表字段足够）。

## 7. 风险

- 东方财富为页面匿名接口，可能改字段或限流：provider 解析失败时结构化报错、保留已入库数据，卡片显示最近一次入库值并在 `?` 中标明来源。
- ISM 首次同步前的历史为最新修订值，回看过去观察日时 PMI 数值可能与当时公布值不同：在卡片 `info` 与数据说明中写明。
- FRED 原生版本在修订当天不可见（沿用 FRED 的 `下一版起点 − 1 天`），既有行为，未改。
- 页面观测数据量增加（约 17 条序列）：批量接口一次往返；日度序列 limit 1000 保持不变。
