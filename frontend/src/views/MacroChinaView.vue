<script setup lang="ts">
/**
 * 中国经济宏观（/macro/china，效果图 2 的「中国经济」页签内容）。
 *
 * 真实接口（与 backend/app/api/v1/research_board.py 的 read_china_macro 一致）：
 *  - GET /api/v1/macro/china
 *      data = { series: [{ code, name, unit, status, observation_count, latest_value,
 *                          latest_observation_date, source, published_at,
 *                          frequency, source_agency, manual_only }], notice }
 *  - GET /api/v1/macro/observations?codes=&as_of=（指标带 20 格一次取回，与美国页同一接口）
 *      data = { pit_date, items: [{ series_code, observations[], empty_message, truncated }] }
 *      · status：stored（已入库）/ stale（数据陈旧）/ unavailable（库里没有观测值）
 *      · 没有任何观测值时仍然返回 200，只是每条 status=unavailable
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *      · 与 FRED 共用同一张观测表；按观测日升序，limit 保留最近 N 条
 *
 * 不编造数据：中国宏观序列由后台任务从东方财富 / 商务部公开接口自动同步（也可由管理员 POST /api/v1/macro/china/observations 导入 CSV），
 * 导入前本页保持诚实空态；走势图只在真的取到 ≥2 个观测值时才画。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, formatAmount, listOf } from "../api/json"
import { cssColor, unitLabel } from "../api/macro"
import { buildChinaTiles } from "../api/chinaTiles"
import {
  CHINA_COMPARE_SPECS,
  CHINA_PLOT_SPECS,
  COMPARE_DEFAULT,
  INFLATION_DEFAULT,
  QUADRANT_TEXT,
  buildQuadrant,
  buildReadout,
  chinaObservationCodes,
  chunkCodes,
  latestSummary,
  trendLines,
  type PlotTone,
  type QuadrantCode,
} from "../api/chinaPanels"
import type { TileView } from "../api/macroTiles"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SeriesPicker from "../components/ui/SeriesPicker.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import MacroTicker from "./parts/macro/MacroTicker.vue"
import MacroAiPanel from "./parts/macro/MacroAiPanel.vue"
import MacroComparePanel from "./parts/macro/MacroComparePanel.vue"
import MacroScenarioPanel from "./parts/macro/MacroScenarioPanel.vue"

type Phase = "loading" | "ready" | "error"

interface ChinaSeries {
  code: string
  name: string
  unit: string
  status: string
  count: number
  latestValue: string
  latestDate: string
  frequency: string
  /** 最近一条观测写入时记录的来源名 */
  source: string
  sourceAgency: string
  /** 没有已核实的公开自动来源，只能管理员导入 CSV */
  manualOnly: boolean
}

interface Observation {
  date: string
  value: number
}

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const IMPORT_HINT = "后台每天从公开接口自动同步，首次同步完成前显示为暂无数据"
const MANUAL_HINT = "来自中国人民银行调查统计司统计表，不做自动抓取，由管理员按月导入 CSV"

const phase = ref<Phase>("loading")
const failure = ref("")
const rows = ref<ChinaSeries[]>([])

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<Observation[]>([])
const selectedCode = ref("")

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
const queryAsOf = ref(browserToday)
const appliedAsOf = ref(browserToday)

const selectedRow = computed<ChinaSeries | null>(
  () => rows.value.find((row) => row.code === selectedCode.value) ?? null,
)

const availableRows = computed<ChinaSeries[]>(() =>
  rows.value.filter((row) => row.status === "stored" || row.status === "stale"),
)

const anyAvailable = computed<boolean>(() => availableRows.value.length > 0)

const latestDate = computed<string>(
  () => availableRows.value.map((row) => row.latestDate).filter(Boolean).sort().pop() ?? "",
)

/** 与后端 public_status 的三档一一对应。 */
function statusPill(row: ChinaSeries): { text: string; tone: "ok" | "warn" | "muted" } {
  if (row.status === "stored") {
    return { text: "已同步", tone: "ok" }
  }
  if (row.status === "stale") {
    return { text: "数据陈旧", tone: "warn" }
  }
  return { text: "暂无数据", tone: "muted" }
}

/** 与后端 STALE_DAYS_BY_FREQUENCY 一致：日度 14 天、周度 21 天、月度 90 天、季度 215 天。 */
const STALE_DAYS: Record<string, number> = { Daily: 14, Weekly: 21, Monthly: 90, Quarterly: 215 }

function statusNote(row: ChinaSeries): string {
  if (row.status === "stored") {
    return `已同步 ${row.count} 期，最新 ${row.latestDate || "—"}`
  }
  if (row.status === "stale") {
    return `已同步 ${row.count} 期，但最新一期 ${row.latestDate || "—"} 距今超过 ${STALE_DAYS[row.frequency] ?? 90} 天（上游更新滞后或同步失败）`
  }
  return row.manualOnly ? `暂无数据，${MANUAL_HINT}` : `暂无数据，${IMPORT_HINT}`
}

function formatValue(value: string | number, unit: string): string {
  const parsed = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(parsed)) {
    return typeof value === "string" && value ? value : "—"
  }
  if (unit === "亿元") {
    return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
  }
  if (unit === "CNY") {
    return formatAmount(parsed, "元")
  }
  return parsed.toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 2 })
}

const chartOption = computed<EChartsCoreOption | null>(() => {
  const points = observations.value
  if (points.length < 2) {
    return null
  }
  const accent = cssColor("--accent", "#3b82f6")
  const unit = selectedRow.value?.unit ?? ""
  const zoom = points.length > 60
  return {
    grid: { left: 8, right: 16, top: 16, bottom: zoom ? 44 : 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown): string =>
        typeof value === "number" ? `${formatValue(value, unit)}${unit === "Percent" ? "%" : ""}` : "—",
    },
    xAxis: {
      type: "category",
      data: points.map((row) => row.date.slice(0, 7)),
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: "value", scale: true },
    series: [
      {
        name: selectedRow.value?.name ?? selectedCode.value,
        type: "line",
        showSymbol: points.length <= 36,
        symbolSize: 5,
        data: points.map((row) => row.value),
        lineStyle: { color: accent, width: 2 },
        itemStyle: { color: accent },
        areaStyle: { color: accent, opacity: 0.1 },
      },
    ],
    ...(zoom
      ? {
          dataZoom: [
            { type: "inside", start: 50, end: 100 },
            { type: "slider", start: 50, end: 100, height: 18, bottom: 6 },
          ],
        }
      : {}),
  }
})

function parseSeries(value: unknown): ChinaSeries[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.code)
    const name = asString(item.name)
    if (!code || !name) {
      return []
    }
    return [
      {
        code,
        name,
        unit: asString(item.unit) ?? "",
        status: asString(item.status) ?? "",
        count: asNumber(item.observation_count) ?? 0,
        latestValue: asString(item.latest_value) ?? "",
        latestDate: asString(item.latest_observation_date) ?? "",
        frequency: asString(item.frequency) ?? "",
        source: asString(item.source) ?? "",
        sourceAgency: asString(item.source_agency) ?? "",
        manualOnly: item.manual_only === true,
      },
    ]
  })
}

const tileRows = ref<Record<string, Observation[]>>({})
const tilesBusy = ref(false)
const tileError = ref("")

const tileViews = computed<TileView[]>(() =>
  buildChinaTiles(
    tileRows.value,
    rows.value.map((row) => ({
      code: row.code,
      name: row.name,
      unit: row.unit,
      frequency: row.frequency,
      source: row.source,
      sourceAgency: row.sourceAgency,
      manualOnly: row.manualOnly,
    })),
  ),
)

const hasTileData = computed<boolean>(() => Object.values(tileRows.value).some((list) => list.length > 0))

/** 设计变量 → 实际颜色（ECharts 画在 canvas 上，不认 CSS 变量）。 */
function palette() {
  return {
    accent: cssColor("--accent", "#3b82f6"),
    cyan: cssColor("--cyan", "#22d3ee"),
    violet: cssColor("--violet", "#a78bfa"),
    orange: cssColor("--orange", "#fb923c"),
    warn: cssColor("--warn", "#fbbf24"),
    ok: cssColor("--ok", "#34d399"),
    pink: "#f472b6",
    teal: "#2dd4bf",
    sky: "#38bdf8",
    text: cssColor("--text", "#e6edf8"),
    muted: cssColor("--muted", "#94a4be"),
    line: cssColor("--line", "rgba(96,140,220,0.16)"),
    panel: cssColor("--panel", "#0d1729"),
  }
}

function toneColor(tone: PlotTone, colors: ReturnType<typeof palette>): string {
  return colors[tone]
}

const YEAR_OPTIONS = [1, 2, 3, 5] as const

interface TrendState {
  codes: string[]
  years: number
}

const overlayTrend = ref<TrendState>({ codes: [...INFLATION_DEFAULT], years: 5 })

const pickerOptions = computed(() => {
  const colors = palette()
  return CHINA_PLOT_SPECS.map((spec) => ({
    code: spec.code,
    label: spec.label,
    color: toneColor(spec.tone, colors),
    disabled: (tileRows.value[spec.code]?.length ?? 0) < 2,
    hint: `${spec.label} · ${spec.frequency === "Quarterly" ? "季度" : "月度"}`,
  }))
})

function percentTip(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "—"
}

interface TrendPlot {
  option: EChartsCoreOption | null
  latest: string[]
}

function buildTrend(state: TrendState): TrendPlot {
  const lines = trendLines(tileRows.value, state.codes, state.years)
  if (!lines.length) {
    return { option: null, latest: [] }
  }
  const colors = palette()
  const option: EChartsCoreOption = {
    grid: { left: 8, right: 12, top: 12, bottom: 4, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "time", axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: "{value}%" } },
    series: lines.map(({ spec, rows: points }) => {
      const color = toneColor(spec.tone, colors)
      return {
        name: spec.label,
        type: "line",
        // 季度指标观测点稀疏，保留圆点，避免被误读成连续数据
        showSymbol: spec.frequency === "Quarterly",
        connectNulls: true,
        data: points.map((row) => [row.date, row.value]),
        tooltip: { valueFormatter: percentTip },
        lineStyle: { color, width: 1.7 },
        itemStyle: { color },
      }
    }),
  }
  return { option, latest: latestSummary(lines) }
}

const overlayPlot = computed<TrendPlot>(() => buildTrend(overlayTrend.value))

const quadrantModel = computed(() => buildQuadrant(tileRows.value, 3))
/** 情景推演用全部可得历史（核心 CPI 自 2021 年起）；分界与象限图的 3 年窗口不同，页面上会写明样本区间。 */
const scenarioModel = computed(() => buildQuadrant(tileRows.value, 30))

const quadrantOption = computed<EChartsCoreOption>(() => {
  const model = quadrantModel.value
  const colors = palette()
  const corner = (code: QuadrantCode, color: string, pos: Record<string, number | string>, align: string) => ({
    type: "text",
    ...pos,
    style: {
      text: `{t|${QUADRANT_TEXT[code].label}}\n{s|${QUADRANT_TEXT[code].sub}}`,
      align,
      rich: {
        t: { fill: color, fontSize: 13, fontWeight: 600, lineHeight: 20, align },
        s: { fill: colors.muted, fontSize: 11, lineHeight: 16, align },
      },
    },
  })
  const axisBase = {
    type: "value",
    scale: true,
    axisLine: { show: false },
    axisLabel: { color: colors.muted, formatter: "{value}%" },
    splitLine: { show: false },
  }
  const trail = model?.points.map((point) => [point.growth, point.inflation, point.period]) ?? []
  return {
    grid: { left: 48, right: 24, top: 36, bottom: 36 },
    graphic: [
      corner("stagflation", colors.violet, { left: 56, top: 44 }, "left"),
      corner("overheat", colors.warn, { right: 32, top: 44 }, "right"),
      corner("weak", colors.accent, { left: 56, bottom: 44 }, "left"),
      corner("goldilocks", colors.cyan, { right: 32, bottom: 44 }, "right"),
    ],
    tooltip: {
      trigger: "item",
      formatter: (params: unknown): string => {
        const value = asRecord(params)?.value
        if (!Array.isArray(value) || value.length < 3) {
          return ""
        }
        return `${String(value[2])}<br/>GDP 当季同比 ${Number(value[0]).toFixed(1)}% · 核心 CPI ${Number(value[1]).toFixed(1)}%`
      },
    },
    xAxis: { ...axisBase, name: "实际 GDP 当季同比", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: colors.muted } },
    yAxis: { ...axisBase, name: "核心 CPI 同比", nameTextStyle: { color: colors.muted } },
    series: model
      ? [
          {
            type: "line",
            data: trail,
            symbolSize: 6,
            lineStyle: { color: colors.muted, width: 1, type: "dashed" },
            itemStyle: { color: colors.muted },
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: colors.line, type: "dashed" },
              label: { show: false },
              data: [{ xAxis: model.growthMid }, { yAxis: model.inflationMid }],
            },
          },
          {
            type: "scatter",
            symbolSize: 16,
            data: [
              {
                name: "最新",
                value: [model.latest.growth, model.latest.inflation, model.latest.period],
                label: {
                  show: true,
                  position: "right",
                  distance: 10,
                  backgroundColor: colors.panel,
                  borderColor: colors.accent,
                  borderWidth: 1,
                  borderRadius: 6,
                  padding: [4, 8],
                  color: colors.text,
                  fontSize: 12,
                  formatter: `最新 ${model.latest.period}`,
                },
              },
            ],
            itemStyle: { color: colors.accent, borderColor: colors.text, borderWidth: 2, shadowBlur: 16, shadowColor: colors.accent },
          },
        ]
      : [],
  }
})

const readout = computed(() => buildReadout(tileRows.value))
const readoutEmpty = computed<boolean>(() => readout.value.every((item) => item.verdict === "数据不足"))

/** 指标带里没有数据的指标：解读里明确列出，说明它们没有参与判断。 */
const unavailableTiles = computed<string[]>(() =>
  tileViews.value.filter((tile) => tile.value === "—").map((tile) => tile.label),
)

async function fetchBatch(codes: readonly string[]): Promise<Record<string, Observation[]>> {
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
    const list = Array.isArray(item.observations) ? item.observations.flatMap(parseObservation) : []
    list.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
    next[code] = list
  }
  return next
}

/**
 * 指标带 20 格 ∪ 走势图序列，按批量接口上限 20 分块取回（只请求目录里有的序列）；
 * 失败只影响这些面板，不拖垮整页。
 */
async function loadTiles(): Promise<void> {
  const known = new Set(rows.value.map((row) => row.code))
  const codes = chinaObservationCodes().filter((code) => known.has(code))
  tilesBusy.value = true
  tileError.value = ""
  try {
    if (!codes.length) {
      tileRows.value = {}
      return
    }
    const parts = await Promise.all(chunkCodes(codes).map((chunk) => fetchBatch(chunk)))
    tileRows.value = Object.assign({}, ...parts) as Record<string, Observation[]>
  } catch (caught) {
    tileRows.value = {}
    tileError.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
  } finally {
    tilesBusy.value = false
  }
}

function parseObservation(value: unknown): Observation[] {
  const record = asRecord(value)
  const date = asString(record?.observation_date)
  const amount = asNumber(record?.value)
  if (!date || amount === null) {
    return []
  }
  return [{ date, value: amount }]
}

async function loadObservations(): Promise<void> {
  const code = selectedCode.value
  if (!code || !anyAvailable.value) {
    detailPhase.value = "idle"
    observations.value = []
    return
  }
  detailPhase.value = "loading"
  detailError.value = ""
  observations.value = []
  try {
    const params = new URLSearchParams({ limit: "240" })
    if (appliedAsOf.value) {
      params.set("as_of", appliedAsOf.value)
    }
    const response = await fetch(
      apiUrl(`/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const parsed = Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : []
    parsed.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
    observations.value = parsed
    detailPhase.value = "ready"
  } catch (caught) {
    detailError.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    detailPhase.value = "error"
  }
}

function selectSeries(code: string): void {
  selectedCode.value = code
  void loadObservations()
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const response = await fetch(apiUrl("/api/v1/macro/china"))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    rows.value = parseSeries(data?.series)
    selectedCode.value = (availableRows.value[0] ?? rows.value[0])?.code ?? ""
    phase.value = "ready"
    await Promise.all([loadObservations(), loadTiles()])
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  }
}

async function applyAsOf(): Promise<void> {
  appliedAsOf.value = queryAsOf.value || browserToday
  await Promise.all([loadObservations(), loadTiles()])
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader title="全球宏观分析" subtitle="把握全球经济周期，洞察宏观变量，发现大类资产配置机会">
      <template #actions>
        <el-date-picker
          v-model="queryAsOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日"
          class="asof-select"
          :disabled="!anyAvailable"
          @change="applyAsOf"
        />
        <el-button @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="china" />
      </template>
      <template #filters>
        <span class="source-hint" :class="{ off: !anyAvailable }">
          <i aria-hidden="true" />数据来源：国家统计局 / 东方财富 / 商务部公开接口（{{ anyAvailable ? `已同步，最新 ${latestDate}` : "尚未同步" }}）
        </span>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock v-if="phase === 'loading'" state="loading" message="正在读取中国宏观数据" />

      <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

      <template v-else>
        <StateBlock v-if="!rows.length" state="empty" message="暂无中国宏观指标" />

        <template v-else>
          <StateBlock
            v-if="!anyAvailable"
            state="empty"
            title="中国宏观数据尚未同步"
            :message="`中国宏观指标暂无数据：${IMPORT_HINT}。`"
          />

          <StateBlock v-if="tileError" state="error" :message="tileError" @retry="loadTiles" />
          <MacroTicker
            v-else
            :tiles="tileViews"
            :loading="tilesBusy && !hasTileData"
            label="中国宏观指标"
          />

          <div v-if="!tileError" class="macro-top">
            <PanelCard
              title="宏观象限：增长 × 通胀"
              info="横轴实际 GDP 当季同比，纵轴核心 CPI 同比（取该季最后一个月）；分界线是最近 3 年内两条轴各自的中位数，不是官方目标值"
            >
              <template #icon><span class="panel-icon">◎</span></template>
              <template v-if="quadrantModel" #actions>
                <span class="muted small">数据截至 {{ quadrantModel.latest.period }}</span>
              </template>
              <VChart class="chart chart-quadrant" :option="quadrantOption" autoresize />
              <p v-if="!quadrantModel" class="chart-note">
                实际 GDP 与核心 CPI 配对的季度不足 4 个，象限暂不定位。
              </p>
              <div v-else class="judgement">
                <h3 class="judgement-title">当前位置</h3>
                <p class="doc-text">
                  {{ quadrantModel.latest.period }}：实际 GDP 当季同比 {{ quadrantModel.latest.growth.toFixed(1) }}%，核心 CPI
                  同比 {{ quadrantModel.latest.inflation.toFixed(1) }}%，落在「<strong>{{
                    QUADRANT_TEXT[quadrantModel.code].label
                  }}</strong>」（{{ QUADRANT_TEXT[quadrantModel.code].sub }}）。
                </p>
                <p class="muted small">
                  分界：GDP {{ quadrantModel.growthMid.toFixed(1) }}% · 核心 CPI {{ quadrantModel.inflationMid.toFixed(1) }}%（窗口内中位数，共
                  {{ quadrantModel.points.length }} 个季度）。虚线为季度轨迹；规则描述，不做预测，不构成投资建议。
                </p>
              </div>
            </PanelCard>

            <PanelCard title="规则解读" info="按最近两期已公布数据的方向自动生成，非模型生成内容" tone="violet">
              <template #icon><span class="panel-icon">✦</span></template>
              <p v-if="readoutEmpty" class="doc-text muted">已公布数据不足，暂不解读。</p>
              <ul v-else class="dimension-list">
                <li v-for="item in readout" :key="item.key">
                  <div class="dimension-head">
                    <span class="dimension-name">{{ item.label }}</span>
                    <StatusPill :text="item.verdict" :tone="item.tone" />
                  </div>
                  <p v-for="entry in item.indicators" :key="entry.code" class="evidence">
                    {{ entry.label }}：{{ entry.text }}
                  </p>
                  <p v-if="item.missing.length" class="evidence">
                    暂无可比数据，未参与判断：{{ item.missing.join("、") }}
                  </p>
                </li>
              </ul>
              <p v-if="unavailableTiles.length" class="muted small footnote">
                指标带中暂无数据：{{ unavailableTiles.join("、") }}。这些指标没有参与上述判断，也不会被估算。
              </p>
              <p class="muted small footnote">
                这是规则计算，不是经济定论：只比较方向，不给概率，不构成投资建议。PMI 以 50 为荣枯线。
              </p>
            </PanelCard>
          </div>

          <PanelCard
            v-if="!tileError"
            title="多指标走势"
            info="把多条同比口径指标画在同一张图上。默认 CPI、核心 CPI、PPI；点色块可加入或移除。数据均为公布的同比值，不再二次换算"
          >
            <template #icon><span class="panel-icon">▲</span></template>
            <SeriesPicker
              v-model="overlayTrend.codes"
              v-model:years="overlayTrend.years"
              :options="pickerOptions"
              :year-options="YEAR_OPTIONS"
            />
            <VChart v-if="overlayPlot.option" class="chart chart-wide" :option="overlayPlot.option" autoresize />
            <StateBlock
              v-else
              state="empty"
              :message="overlayTrend.codes.length ? '所选指标数据不足两期，暂不画走势' : '请在上方至少选择一个指标'"
            />
            <p v-if="overlayPlot.latest.length" class="chart-note">{{ overlayPlot.latest.join(" · ") }}</p>
          </PanelCard>

          <div v-if="!tileError" class="macro-extra">
            <MacroComparePanel
              :specs="CHINA_COMPARE_SPECS"
              :rows-by-code="tileRows"
              :default-codes="COMPARE_DEFAULT"
              filename="china-macro-compare"
            />
            <MacroScenarioPanel :model="scenarioModel" growth-label="实际 GDP 当季同比" inflation-label="核心 CPI 同比" />
            <MacroAiPanel region="cn" :as-of="appliedAsOf" />
          </div>

          <div class="series-board">
            <PanelCard title="指标走势" info="只显示所选观察日已经公布的观测值">
              <template #icon><span class="panel-icon">◠</span></template>
              <template v-if="anyAvailable" #actions>
                <el-select v-model="selectedCode" class="filter-select" size="small" @change="selectSeries">
                  <el-option
                    v-for="item in availableRows"
                    :key="item.code"
                    :value="item.code"
                    :label="item.name"
                  />
                </el-select>
              </template>

              <p v-if="!anyAvailable" class="empty-note">同步到数据后在此显示走势</p>
              <StateBlock v-else-if="detailPhase === 'loading'" state="loading" message="正在查询观测值" />
              <StateBlock
                v-else-if="detailPhase === 'error'"
                state="error"
                :message="detailError"
                @retry="loadObservations"
              />
              <template v-else-if="detailPhase === 'ready'">
                <VChart v-if="chartOption" class="chart chart-trend" :option="chartOption" autoresize />
                <StateBlock v-else state="empty" message="所选观察日已公布的数据不足两期，暂不画走势" />
                <p v-if="observations.length" class="chart-note">
                  {{ selectedRow?.name }}：共 {{ observations.length }} 期，单位 {{ unitLabel(selectedRow?.unit ?? "") }}
                </p>
              </template>
            </PanelCard>

            <PanelCard
              title="指标清单"
              info="已同步 / 数据陈旧（日度 14 天、周度 21 天、月度 90 天、季度 215 天未更新）/ 暂无数据"
            >
              <template #icon><span class="panel-icon">▤</span></template>
              <div class="series-scroll">
              <el-table :data="rows" @row-click="(row: ChinaSeries) => anyAvailable && selectSeries(row.code)">
                <el-table-column prop="name" label="指标" min-width="170" />
                <el-table-column label="单位" width="70">
                  <template #default="scope">{{ unitLabel(scope.row.unit) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="96">
                  <template #default="scope">
                    <span :title="statusNote(scope.row)">
                      <StatusPill :text="statusPill(scope.row).text" :tone="statusPill(scope.row).tone" />
                    </span>
                  </template>
                </el-table-column>
                <el-table-column v-if="anyAvailable" label="最新" width="100">
                  <template #default="scope">
                    <span class="tabular">{{ scope.row.latestDate ? scope.row.latestDate.slice(0, 7) : "—" }}</span>
                  </template>
                </el-table-column>
              </el-table>
              </div>
              <p class="chart-note">已同步 {{ availableRows.length }} / {{ rows.length }} 项。</p>
            </PanelCard>
          </div>
        </template>

        <SourceFooter
          :as-of="latestDate || appliedAsOf"
          :sources="[
            '国家统计局数据发布库',
            '东方财富数据中心（国家统计局、海关总署、中国人民银行）',
            '商务部数据中心（社会融资规模）',
          ]"
          :stale="!anyAvailable"
          :note="anyAvailable ? '公布日按保守上界计算（如 CPI 取次月 15 日），回看只会晚看到、不会提前看到。社融存量、企业中长期贷款、政府债券净融资、逆回购利率、DR007 暂无已核实的公开自动来源，显示为暂无数据。' : IMPORT_HINT"
        />
      </template>
    </div>
  </main>
</template>

<style scoped>
.asof-select {
  width: 180px;
}

.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.source-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.source-hint i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.source-hint.off i {
  background: var(--faint);
}

.filter-select {
  width: 220px;
}

.chart {
  width: 100%;
}

.chart-trend {
  height: 320px;
}

.chart-wide {
  height: 300px;
}

.chart-quadrant {
  height: 320px;
}

.series-scroll {
  max-height: 360px;
  overflow: auto;
}

.small {
  font-size: 12px;
}

p.small {
  font-size: 13px;
}

.macro-top,
.series-board {
  display: grid;
  gap: var(--gap);
  align-items: start;
}

.macro-top {
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 0.9fr);
}

.series-board {
  grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.8fr);
}

.macro-extra {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr);
  align-items: start;
}

.macro-extra > :first-child {
  grid-row: span 2;
}

@media (max-width: 1024px) {
  .macro-top,
  .series-board,
  .macro-extra {
    grid-template-columns: minmax(0, 1fr);
  }
}

.judgement {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}

.judgement-title {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--accent);
}

.judgement .doc-text {
  margin-bottom: 4px;
}

.dimension-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.dimension-list li {
  padding: 8px 0;
  border-bottom: 1px solid var(--line-soft);
}

.dimension-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.dimension-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.evidence {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
  font-variant-numeric: tabular-nums;
}

.footnote {
  margin: 12px 0 0;
  line-height: 1.7;
}

.empty-note {
  display: grid;
  place-items: center;
  min-height: 200px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.chart-note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.el-table {
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
  cursor: pointer;
}
</style>
