<script setup lang="ts">
/**
 * 全球宏观分析（/macro/us，效果图 2）。
 *
 * 真实接口（与 backend/app/api/v1/macro.py、schemas/macro.py、catalog/macro_series.py 一致）：
 *  - GET /api/v1/macro/series
 *      data = { provider_code, availability, notice, series[] }
 *      availability：configured / unavailable / not_required / public_csv（无密钥时走 FRED 公开 CSV，只有最新修订值）
 *  - GET /api/v1/macro/observations?codes=&as_of=
 *      data = { pit_date, items: [{ series_code, observations[], empty_message, truncated }] }
 *      一次取回指标带与走势图所需的全部序列；limit 由后端按频率决定，observations 升序。
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value, reason, evidence[] },
 *               notice, version }
 *
 * 不编造数据（AGENTS.md 硬约束）：
 *  - 指标带口径见 api/macroTiles.ts：同比按 12 个月前同月、非农为当月新增、实际 GDP 为环比折年率，缺数据就不显示。
 *  - 宏观象限只用后端规则分数定位；分数取不到时只画坐标系，不落点。
 *  - 规则解读正文来自 assessments 的 regime_code / evidence，不是模型生成文案。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf, scaleAmount } from "../api/json"
import { cssColor, frequencyLabel, macroSourceLabel, parseEvidence, regimeText, unitLabel } from "../api/macro"
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
import MacroAiPanel from "./parts/macro/MacroAiPanel.vue"
import MacroComparePanel from "./parts/macro/MacroComparePanel.vue"
import MacroScenarioPanel from "./parts/macro/MacroScenarioPanel.vue"
import { US_COMPARE_DEFAULT, US_COMPARE_SPECS } from "../api/usPanels"
import { buildQuadrantModel } from "../api/macroStats"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SeriesPicker from "../components/ui/SeriesPicker.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"
type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

type CatalogItem = TileCatalogItem

interface DimensionScore {
  code: string
  value: number | null
  reason: string | null
  evidence: string[]
}

interface Assessment {
  pitDate: string
  regimeCode: string
  growth: DimensionScore | null
  inflation: DimensionScore | null
  liquidity: DimensionScore | null
  notice: string
  version: string
}

/** 规则打分引用的序列（backend/app/services/macro_rules.py）。 */
const RULE_SERIES: Record<string, string> = { growth: "GDP", inflation: "CPIAUCSL", liquidity: "FEDFUNDS" }

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const phase = ref<Phase>("loading")
const failure = ref("")
const availability = ref("")
const catalog = ref<CatalogItem[]>([])
const observationsByCode = ref<Record<string, Observation[]>>({})
const assessmentFailed = ref(false)
const assessment = ref<Assessment | null>(null)
const helpOpen = ref(false)
const observationsBusy = ref(false)

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`

/** 观察日：一次请求同时约束指标带、走势图与规则判断，避免两块数据对不上时点。 */
const queryAsOf = ref(browserToday)
const appliedAsOf = ref(browserToday)

const sourceLabel = computed<string>(() => macroSourceLabel(availability.value))

function catalogItem(code: string): CatalogItem | null {
  return catalog.value.find((entry) => entry.code === code) ?? null
}

function seriesName(code: string): string {
  return catalogItem(code)?.name ?? code
}

function rowsOf(code: string): Observation[] {
  return observationsByCode.value[code] ?? []
}

function formatPlain(value: number, digits = 2): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

const tileViews = computed<TileView[]>(() =>
  TILE_SPECS.map((spec) => buildTile(spec, observationsByCode.value, catalog.value)),
)

/** 情景推演：增长 = 实际 GDP 环比折年率，通胀 = CPI 同比（取该季最后一个月），用全部可得历史。 */
const scenarioModel = computed(() =>
  buildQuadrantModel(
    applyTransform("saar", rowsOf("GDPC1")),
    applyTransform("yoy", rowsOf("CPIAUCSL")),
    30,
  ),
)

const hasAnyObservation = computed<boolean>(() => Object.values(observationsByCode.value).some((rows) => rows.length))

const latestObservationDate = computed<string>(() => {
  const dates = Object.values(observationsByCode.value)
    .flat()
    .map((row) => row.date)
  return dates.length ? dates.reduce((left, right) => (left > right ? left : right)) : ""
})

/** 公开 CSV 只有最新修订值，选历史观察日时看不到「当时已公布」的数据。 */
const pastDateUnsupported = computed<boolean>(
  () => availability.value === "public_csv" && appliedAsOf.value < browserToday,
)

const emptyNotice = computed<string>(() => {
  if (pastDateUnsupported.value) {
    return "当前数据来自 FRED 公开 CSV，只有最新修订值，无法回看所选观察日当时已公布的数据。请把观察日调回今天。"
  }
  if (availability.value === "unavailable") {
    return "美国宏观数据源暂不可用，请稍后重试或联系管理员检查数据同步。"
  }
  return "所选观察日还没有已公布的美国宏观数据。"
})

const stale = computed<boolean>(
  () => phase.value === "ready" && (availability.value === "unavailable" || !hasAnyObservation.value),
)

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
    lime: "#a3e635",
    text: cssColor("--text", "#e6edf8"),
    muted: cssColor("--muted", "#94a4be"),
    line: cssColor("--line", "rgba(96,140,220,0.16)"),
    panel: cssColor("--panel", "#0d1729"),
  }
}

function percentTip(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "—"
}

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

/** 多指标图的 2% 目标线挂在第一条价格指数同比上。 */
const INFLATION_CODES = new Set<string>(["CPIAUCSL", "PCEPILFE"])

/** 批量接口一次最多 20 条。优先指标带和多指标图，其余目录序列留给指标清单。 */
const BATCH_LIMIT = 20

function codesToLoad(known: readonly string[]): string[] {
  const present = new Set(known)
  const preferred = [...new Set([...tileCodes(), ...PLOT_SPECS.map((spec) => spec.code)])].filter((code) =>
    present.has(code),
  )
  const extra = [...new Set(known.filter((code) => !preferred.includes(code)))]
  return [...preferred, ...extra].slice(0, BATCH_LIMIT)
}

const YEAR_OPTIONS = [1, 2, 3, 5] as const

interface TrendState {
  codes: string[]
  years: number
}

/** 默认看 CPI 与核心 PCE 同比；点色块可加入或移除任意百分比口径指标。 */
const overlayTrend = ref<TrendState>({ codes: ["CPIAUCSL", "PCEPILFE"], years: 5 })

function plotRows(spec: PlotSpec): Observation[] {
  return applyTransform(spec.transform, rowsOf(spec.code))
}

function toneColor(tone: PlotTone, colors: ReturnType<typeof palette>): string {
  const map: Record<PlotTone, string> = {
    accent: colors.accent,
    orange: colors.orange,
    violet: colors.violet,
    warn: colors.warn,
    ok: colors.ok,
    cyan: colors.cyan,
    pink: colors.pink,
    muted: colors.muted,
    teal: colors.teal,
    sky: colors.sky,
    lime: colors.lime,
  }
  return map[tone]
}

const pickerOptions = computed(() => {
  const colors = palette()
  return PLOT_SPECS.map((spec) => {
    const item = catalogItem(spec.code)
    return {
      code: spec.code,
      label: spec.label,
      color: toneColor(spec.tone, colors),
      disabled: plotRows(spec).length < 2,
      hint: item ? `${item.nameEn || item.name} · ${frequencyLabel(item.frequency)}` : spec.label,
    }
  })
})

function shiftYears(date: string, years: number): string {
  return `${Number(date.slice(0, 4)) - years}${date.slice(4)}`
}

interface TrendPlot {
  option: EChartsCoreOption | null
  latest: string[]
}

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

const overlayPlot = computed<TrendPlot>(() => buildTrend(overlayTrend.value))

/** 与中国页同一套陈旧阈值：日度 14 天、周度 21 天、月度 90 天、季度 215 天。 */
const STALE_DAYS: Record<string, number> = { Daily: 14, Weekly: 21, Monthly: 90, Quarterly: 215, Annual: 450 }

interface InventoryRow {
  code: string
  name: string
  unit: string
  frequency: string
  count: number
  latestDate: string
  status: "stored" | "stale" | "unavailable"
}

function ageDays(date: string): number {
  const observed = Date.parse(`${date.slice(0, 10)}T00:00:00Z`)
  const today = Date.parse(`${browserToday}T00:00:00Z`)
  if (!Number.isFinite(observed) || !Number.isFinite(today)) {
    return 0
  }
  return Math.floor((today - observed) / 86_400_000)
}

const inventory = computed<InventoryRow[]>(() =>
  catalog.value.map((item) => {
    const rows = rowsOf(item.code)
    const last = rows[rows.length - 1]
    const limit = STALE_DAYS[item.frequency] ?? 90
    const status: InventoryRow["status"] = !last ? "unavailable" : ageDays(last.date) > limit ? "stale" : "stored"
    return {
      code: item.code,
      name: item.name,
      unit: item.unit,
      frequency: item.frequency,
      count: rows.length,
      latestDate: last?.date ?? "",
      status,
    }
  }),
)

const availableInventory = computed(() => inventory.value.filter((row) => row.status !== "unavailable"))

const selectedCode = ref("CPIAUCSL")

watch(inventory, (rows) => {
  if (rows.some((row) => row.code === selectedCode.value && row.count > 0)) {
    return
  }
  const next =
    rows.find((row) => row.code === "CPIAUCSL" && row.count >= 2) ??
    rows.find((row) => row.count >= 2) ??
    rows[0]
  selectedCode.value = next?.code ?? ""
})

function selectSeries(code: string): void {
  selectedCode.value = code
}

const selectedInventory = computed(() => inventory.value.find((row) => row.code === selectedCode.value) ?? null)

function inventoryStatus(row: InventoryRow): { text: string; tone: "ok" | "warn" | "muted" } {
  if (row.status === "stored") {
    return { text: "已同步", tone: "ok" }
  }
  if (row.status === "stale") {
    return { text: "数据陈旧", tone: "warn" }
  }
  return { text: "暂无数据", tone: "muted" }
}

function inventoryNote(row: InventoryRow): string {
  if (row.status === "stored") {
    return `已同步 ${row.count} 期，最新 ${row.latestDate || "—"}`
  }
  if (row.status === "stale") {
    return `已同步 ${row.count} 期，但最新一期 ${row.latestDate || "—"} 距今超过 ${STALE_DAYS[row.frequency] ?? 90} 天`
  }
  return "所选观察日还没有已公布的观测值"
}

function latestLabel(row: InventoryRow): string {
  if (!row.latestDate) {
    return "—"
  }
  return row.frequency === "Daily" || row.frequency === "Weekly" ? row.latestDate.slice(0, 10) : row.latestDate.slice(0, 7)
}

const detailOption = computed<EChartsCoreOption | null>(() => {
  const row = selectedInventory.value
  if (!row || row.count < 2) {
    return null
  }
  const points = rowsOf(row.code)
  const accent = cssColor("--accent", "#3b82f6")
  const zoom = points.length > 80
  const unit = unitLabel(row.unit)
  return {
    grid: { left: 8, right: 16, top: 16, bottom: zoom ? 44 : 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown): string =>
        typeof value === "number"
          ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${unit}`
          : "—",
    },
    xAxis: { type: "time", axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true },
    series: [
      {
        name: row.name,
        type: "line",
        showSymbol: points.length <= 36,
        symbolSize: 5,
        data: points.map((point) => [point.date, point.value]),
        lineStyle: { color: accent, width: 2 },
        itemStyle: { color: accent },
        areaStyle: { color: accent, opacity: 0.1 },
      },
    ],
    ...(zoom
      ? {
          dataZoom: [
            { type: "inside", start: 70, end: 100 },
            { type: "slider", start: 70, end: 100, height: 18, bottom: 6 },
          ],
        }
      : {}),
  }
})

/** 宏观象限：横轴流动性分数、纵轴增长分数，各 0–100，以 50 为界划分四象限。 */
const quadrantPoint = computed<{ x: number; y: number } | null>(() => {
  const growth = assessment.value?.growth?.value
  const liquidity = assessment.value?.liquidity?.value
  if (growth === null || growth === undefined || liquidity === null || liquidity === undefined) {
    return null
  }
  return { x: liquidity, y: growth }
})

const quadrantOption = computed<EChartsCoreOption>(() => {
  const point = quadrantPoint.value
  const colors = palette()
  const corner = (text: string, sub: string, color: string, pos: Record<string, number | string>, align: string) => ({
    type: "text",
    ...pos,
    style: {
      text: `{t|${text}}\n{s|${sub}}`,
      align,
      rich: {
        t: { fill: color, fontSize: 13, fontWeight: 600, lineHeight: 20, align },
        s: { fill: colors.muted, fontSize: 11, lineHeight: 16, align },
      },
    },
  })
  return {
    grid: { left: 64, right: 64, top: 40, bottom: 40 },
    graphic: [
      corner("过热风险", "增长强 · 货币紧", colors.warn, { left: 76, top: 52 }, "left"),
      corner("扩张阶段", "增长强 · 货币松", colors.cyan, { right: 76, top: 52 }, "right"),
      corner("衰退风险", "增长弱 · 货币紧", colors.violet, { left: 76, bottom: 52 }, "left"),
      corner("复苏阶段", "增长弱 · 货币松", colors.accent, { right: 76, bottom: 52 }, "right"),
      {
        type: "text",
        left: "center",
        top: 4,
        style: { text: "经济较热（增长强）", fill: colors.text, fontSize: 12, align: "center" },
      },
      {
        type: "text",
        left: "center",
        bottom: 6,
        style: { text: "经济较冷（增长弱）", fill: colors.text, fontSize: 12, align: "center" },
      },
      {
        type: "text",
        left: 4,
        top: "middle",
        style: { text: "货币收紧\n（利率上行）", fill: colors.text, fontSize: 12, lineHeight: 17, align: "left" },
      },
      {
        type: "text",
        right: 4,
        top: "middle",
        style: { text: "货币宽松\n（利率下行）", fill: colors.text, fontSize: 12, lineHeight: 17, align: "right" },
      },
    ],
    tooltip: {
      trigger: "item",
      formatter: (params: unknown): string => {
        const value = asListValue(asRecord(params)?.value)
        if (value.length < 2) {
          return ""
        }
        return `流动性分数 ${value[0]} · 增长分数 ${value[1]}`
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: 100,
      interval: 50,
      axisLine: { show: false },
      axisLabel: { show: false },
      splitLine: { lineStyle: { color: colors.line, type: "dashed" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      interval: 50,
      axisLine: { show: false },
      axisLabel: { show: false },
      splitLine: { lineStyle: { color: colors.line, type: "dashed" } },
    },
    series: [
      {
        type: "scatter",
        symbolSize: 16,
        data: point
          ? [
              {
                name: "当前位置",
                value: [point.x, point.y],
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
                  formatter: "当前位置",
                },
              },
            ]
          : [],
        itemStyle: {
          color: colors.accent,
          borderColor: colors.text,
          borderWidth: 2,
          shadowBlur: 16,
          shadowColor: colors.accent,
        },
      },
    ],
  }
})

function asListValue(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const parsed = asNumber(item)
    return parsed === null ? [] : [parsed]
  })
}

const dimensionRows = computed<DimensionScore[]>(() => {
  const current = assessment.value
  if (!current) {
    return []
  }
  return [current.growth, current.inflation, current.liquidity].flatMap((item) => (item ? [item] : []))
})

const allInsufficient = computed<boolean>(
  () => dimensionRows.value.length > 0 && dimensionRows.value.every((item) => item.value === null),
)

const regimeLabel = computed<string>(() => regimeText(assessment.value?.regimeCode ?? "") || "—")

const regimePill = computed<{ text: string; tone: Tone }>(() => {
  const current = assessment.value
  if (!current) {
    return { text: "未取到判断", tone: "muted" }
  }
  if (current.regimeCode === "insufficient_data" || allInsufficient.value || !current.regimeCode) {
    return { text: "数据不足", tone: "muted" }
  }
  return { text: regimeLabel.value, tone: "accent" }
})

/** 维度分数：70=上行/偏松、30=下行/偏紧、50=持平；宏观指标不是涨跌，配色用中性色。 */
function scoreView(score: DimensionScore | null): { label: string; tone: Tone } {
  if (!score || score.value === null) {
    return { label: "数据不足", tone: "muted" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value >= 70) {
    return { label: liquidity ? "偏松" : "上行", tone: "accent" }
  }
  if (score.value <= 30) {
    return { label: liquidity ? "偏紧" : "下行", tone: "warn" }
  }
  return { label: "持平", tone: "muted" }
}

function dimensionLabel(code: string): string {
  if (code === "growth") {
    return "增长"
  }
  if (code === "inflation") {
    return "通胀"
  }
  if (code === "liquidity") {
    return "流动性"
  }
  return code
}

function evidenceValue(code: string, value: number | null): string {
  if (value === null) {
    return "—"
  }
  const unit = catalogItem(code)?.unit ?? ""
  if (unit === "Billions of Dollars") {
    const scaled = scaleAmount(value * 1e9)
    return `${scaled.text} ${scaled.scale}美元`
  }
  if (unit === "Percent") {
    return `${value.toFixed(2)}%`
  }
  return formatPlain(value)
}

/** 依据行 → 「GDP：2026 Q1 31.91 万亿美元 → 2026 Q2 32.56 万亿美元」。 */
function evidenceSummary(score: DimensionScore): string {
  const points = score.evidence.map(parseEvidence)
  if (!points.length) {
    return ""
  }
  if (points.some((point) => !point.code)) {
    return score.evidence.join("；")
  }
  const code = points[0]?.code ?? ""
  const frequency = catalogItem(code)?.frequency ?? ""
  const steps = points.map((point) => `${periodText(point.date, frequency)} ${evidenceValue(point.code, point.value)}`)
  return `${seriesName(code)}：${steps.join(" → ")}`
}

const latestPublished = computed<string>(() => {
  const dates = dimensionRows.value.flatMap((row) => row.evidence.map((line) => parseEvidence(line).published))
  return dates.filter(Boolean).sort().pop() ?? ""
})

/** 情景切换条件：只按规则写「下一期往哪个方向走会改变哪一维」，不给概率、不预测数值。 */
const scenarioRows = computed<{ key: string; label: string; tone: Tone; text: string }[]>(() => {
  const rows: { key: string; label: string; tone: Tone; text: string }[] = []
  if (!assessment.value || allInsufficient.value) {
    return rows
  }
  rows.push({
    key: "base",
    label: "延续",
    tone: "accent",
    text: `下一期公布值方向不变时，维持「${regimeLabel.value}」。`,
  })
  for (const score of dimensionRows.value) {
    if (score.value === null) {
      continue
    }
    const name = seriesName(RULE_SERIES[score.code] ?? "")
    const rising = score.value >= 50
    if (score.code === "liquidity") {
      rows.push({
        key: score.code,
        label: score.value <= 30 ? "转松" : "转紧",
        tone: "warn",
        text:
          score.value <= 30
            ? `若${name}下一期低于本期，流动性转为偏松。`
            : `若${name}下一期高于本期，流动性转为偏紧。`,
      })
      continue
    }
    const dim = dimensionLabel(score.code)
    rows.push({
      key: score.code,
      label: `${dim}${rising ? "转弱" : "回升"}`,
      tone: "muted",
      text: rising
        ? `若${name}下一期低于本期，${dim}维度转为下行。`
        : `若${name}下一期高于本期，${dim}维度转为上行。`,
    })
  }
  return rows
})

function parseCatalog(value: unknown): CatalogItem[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.series_code)
    const name = asString(item.name_cn)
    if (!code || !name) {
      return []
    }
    return [
      {
        code,
        name,
        nameEn: asString(item.name_en) ?? "",
        frequency: asString(item.frequency) ?? "",
        unit: asString(item.base_unit) ?? "",
        adjustment: asString(item.seasonal_adjustment) ?? "",
        provider: asString(item.provider) ?? "fred",
        sourceKey: asString(item.source_key) ?? "",
      },
    ]
  })
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

function parseScore(value: unknown): DimensionScore | null {
  const record = asRecord(value)
  const code = asString(record?.code)
  if (!code) {
    return null
  }
  const evidence = Array.isArray(record?.evidence)
    ? record.evidence.flatMap((entry) => {
        const text = asString(entry)
        return text ? [text] : []
      })
    : []
  return {
    code,
    value: asNumber(record?.value),
    reason: asString(record?.reason),
    evidence,
  }
}

async function loadObservations(): Promise<void> {
  observationsBusy.value = true
  const codes = codesToLoad(catalog.value.map((item) => item.code))
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

async function loadAssessment(): Promise<void> {
  assessmentFailed.value = false
  assessment.value = null
  try {
    const params = new URLSearchParams()
    if (appliedAsOf.value) {
      params.set("as_of", appliedAsOf.value)
    }
    const query = params.toString()
    const response = await fetch(apiUrl(`/api/v1/macro/assessments${query ? `?${query}` : ""}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    assessment.value = {
      pitDate: asString(data?.pit_date) ?? "",
      regimeCode: asString(data?.regime_code) ?? "",
      growth: parseScore(data?.growth),
      inflation: parseScore(data?.inflation),
      liquidity: parseScore(data?.liquidity),
      notice: asString(data?.notice) ?? "",
      version: asString(data?.version) ?? "",
    }
  } catch {
    // 规则判断失败不让整页失败，但如实标注「未取到」，不编造结论。
    assessmentFailed.value = true
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const seriesResponse = await fetch(apiUrl("/api/v1/macro/series"))
    const seriesBody = await readJson(seriesResponse)
    if (!seriesResponse.ok) {
      throw errorFrom(seriesBody, seriesResponse.headers.get("X-Request-ID"))
    }
    const seriesData = asRecord(asRecord(seriesBody)?.data)
    availability.value = asString(seriesData?.availability) ?? ""
    catalog.value = parseCatalog(seriesData?.series)
    phase.value = "ready"
    await Promise.all([loadObservations(), loadAssessment()])
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  }
}

/** 应用观察日：指标带、走势图与规则判断一起刷新，保持同一时点。 */
async function applyAsOf(): Promise<void> {
  // 必须先把观察日落到 appliedAsOf 再发请求：两个 load 函数都用 appliedAsOf 拼 as_of。
  appliedAsOf.value = queryAsOf.value || browserToday
  await Promise.all([loadObservations(), loadAssessment()])
}

function resetAsOf(): void {
  queryAsOf.value = browserToday
  void applyAsOf()
}

watch(queryAsOf, (value) => {
  if (!value) {
    queryAsOf.value = browserToday
  }
})

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
          :disabled="observationsBusy"
          @change="applyAsOf"
        />
        <el-button @click="helpOpen = true">数据说明</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="us" />
      </template>
      <template #filters>
        <span class="source-hint">
          <i aria-hidden="true" />数据来源：{{ sourceLabel }}<template v-if="latestObservationDate">
            · 最新观测 {{ latestObservationDate }}</template
          >
        </span>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock v-if="phase === 'loading'" state="loading" message="正在读取美国宏观数据" />

      <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

      <template v-else>
        <StateBlock v-if="!hasAnyObservation && !observationsBusy" state="empty" :message="emptyNotice" />
        <div v-if="!hasAnyObservation && pastDateUnsupported" class="reset-row">
          <el-button size="small" type="primary" @click="resetAsOf">回到今天</el-button>
        </div>

        <MacroTicker :tiles="tileViews" :loading="observationsBusy && !hasAnyObservation" />

        <div class="macro-top">
          <PanelCard
            title="宏观象限：经济冷热 × 货币松紧"
            :info="`按规则分数定位：横轴流动性、纵轴增长，各 0–100，以 50 为界。判断日 ${assessment?.pitDate || '—'}`"
          >
            <template #icon><span class="panel-icon">◎</span></template>
            <template #actions>
              <span v-if="latestPublished" class="muted small">数据截至 {{ latestPublished }}</span>
            </template>
            <VChart class="chart chart-quadrant" :option="quadrantOption" autoresize />
            <p v-if="!quadrantPoint" class="chart-note">
              {{ assessmentFailed ? "规则判断暂时不可用，象限暂不定位。" : "当前观察日数据不足，象限暂不定位。" }}
            </p>
            <div class="judgement">
              <h3 class="judgement-title">当前判断</h3>
              <template v-if="assessment && !allInsufficient">
                <p class="doc-text">
                  美国经济处于「<strong>{{ regimeLabel }}</strong>」组合：
                  <template v-for="(item, index) in dimensionRows" :key="item.code">
                    {{ dimensionLabel(item.code) }}{{ scoreView(item).label
                    }}{{ index < dimensionRows.length - 1 ? "，" : "。" }}
                  </template>
                </p>
                <p class="muted small">规则依据最近两期已公布数据的方向，不做经济预测，也不构成投资建议。</p>
              </template>
              <p v-else class="doc-text muted">
                {{ pastDateUnsupported ? "公开数据无法回看历史观察日，请调回今天。" : "当前观察日数据不足，暂不判断。" }}
              </p>
            </div>
          </PanelCard>

          <PanelCard title="规则解读" info="由增长、通胀、流动性三条规则自动生成，非模型生成内容" tone="violet">
            <template #icon><span class="panel-icon">✦</span></template>
            <div class="readout-head">
              <StatusPill :text="regimePill.text" :tone="regimePill.tone" dot />
              <span class="muted small">判断日 {{ assessment?.pitDate || "—" }}</span>
            </div>
            <StateBlock
              v-if="assessmentFailed"
              state="error"
              message="规则判断暂时不可用"
              @retry="loadAssessment"
            />
            <p v-else-if="!assessment" class="doc-text muted">正在读取规则判断…</p>
            <template v-else>
              <ul class="dimension-list">
                <li v-for="item in dimensionRows" :key="item.code">
                  <div class="dimension-head">
                    <span class="dimension-name">{{ dimensionLabel(item.code) }}</span>
                    <StatusPill :text="scoreView(item).label" :tone="scoreView(item).tone" />
                    <span v-if="item.value !== null" class="muted small tabular">{{ item.value }} 分</span>
                  </div>
                  <p v-if="evidenceSummary(item)" class="evidence">{{ evidenceSummary(item) }}</p>
                  <p v-else class="evidence">已公布数据不足两期</p>
                </li>
              </ul>

              <h3 class="doc-section">情景切换条件</h3>
              <ul v-if="scenarioRows.length" class="scenario-list">
                <li v-for="item in scenarioRows" :key="item.key" class="scenario-row">
                  <StatusPill :text="item.label" :tone="item.tone" />
                  <p class="scenario-text">{{ item.text }}</p>
                </li>
              </ul>
              <p v-else class="muted small">数据不足，暂无情景推演。</p>

              <p class="muted small footnote">
                {{ assessment.notice || "这是规则计算，不是经济定论。" }} 不给概率，不构成投资建议；更多细节见
                <router-link to="/macro/scenarios">宏观综合判断</router-link>。
              </p>
            </template>
          </PanelCard>
        </div>

        <PanelCard
          title="多指标走势"
          info="把多条百分比口径指标画在同一张图上。默认 CPI 与核心 PCE 同比；点色块可加入或移除任意指标。同比 = 当月 ÷ 12 个月前同月 − 1；实际 GDP 为环比折年率"
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
          <p v-if="overlayPlot.latest.length" class="chart-note latest">{{ overlayPlot.latest.join(" · ") }}</p>
        </PanelCard>

        <div v-if="inventory.length" class="series-board">
          <PanelCard title="指标走势" info="只显示所选观察日已经公布的原始观测值，不做同比换算">
            <template #icon><span class="panel-icon">◠</span></template>
            <template v-if="availableInventory.length" #actions>
              <el-select v-model="selectedCode" class="filter-select" size="small" @change="selectSeries">
                <el-option
                  v-for="item in availableInventory"
                  :key="item.code"
                  :value="item.code"
                  :label="item.name"
                />
              </el-select>
            </template>
            <p v-if="!availableInventory.length" class="empty-note">同步到数据后在此显示走势</p>
            <template v-else>
              <VChart v-if="detailOption" class="chart chart-trend" :option="detailOption" autoresize />
              <StateBlock v-else state="empty" message="所选观察日已公布的数据不足两期，暂不画走势" />
              <p v-if="selectedInventory && selectedInventory.count" class="chart-note">
                {{ selectedInventory.name }}：共 {{ selectedInventory.count }} 期，单位
                {{ unitLabel(selectedInventory.unit) }}
              </p>
            </template>
          </PanelCard>

          <PanelCard
            title="指标清单"
            info="已同步 / 数据陈旧（日度 14 天、周度 21 天、月度 90 天、季度 215 天未更新）/ 暂无数据"
          >
            <template #icon><span class="panel-icon">▤</span></template>
            <div class="series-scroll">
              <el-table :data="inventory" @row-click="(row: InventoryRow) => selectSeries(row.code)">
                <el-table-column prop="name" label="指标" min-width="160" />
                <el-table-column label="单位" width="88">
                  <template #default="scope">{{ scope?.row ? unitLabel(scope.row.unit) : "" }}</template>
                </el-table-column>
                <el-table-column label="状态" width="96">
                  <template #default="scope">
                    <span v-if="scope?.row" :title="inventoryNote(scope.row)">
                      <StatusPill :text="inventoryStatus(scope.row).text" :tone="inventoryStatus(scope.row).tone" />
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="最新" width="110">
                  <template #default="scope">
                    <span v-if="scope?.row" class="tabular">{{ latestLabel(scope.row) }}</span>
                  </template>
                </el-table-column>
              </el-table>
            </div>
            <p class="chart-note">已同步 {{ availableInventory.length }} / {{ inventory.length }} 项。</p>
          </PanelCard>
        </div>

        <div v-if="hasAnyObservation" class="macro-extra">
          <MacroComparePanel
            :specs="US_COMPARE_SPECS"
            :rows-by-code="observationsByCode"
            :default-codes="US_COMPARE_DEFAULT"
            default-mode="index"
            filename="us-macro-compare"
          />
          <MacroScenarioPanel :model="scenarioModel" growth-label="实际 GDP 环比折年率" inflation-label="CPI 同比" />
          <MacroAiPanel region="us" :as-of="appliedAsOf" />
        </div>

        <SourceFooter
          :as-of="latestObservationDate || appliedAsOf"
          :sources="[sourceLabel, '东方财富数据中心（ISM PMI）']"
          :stale="stale"
          note="「较上期」为相邻两期主口径之差：利率、同比类按百分点，非农与初请按千人，PMI 按点；政策利率按上一次调整计。"
        />
      </template>
    </div>

    <el-dialog v-model="helpOpen" title="数据说明" width="600px">
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
        <li>利率、失业率、利差与同比类的「较上期」单位为百分点（pct），非农与初请为千人，PMI 为点；颜色只表示最新一期的方向，不代表好坏。</li>
        <li>
          宏观象限与规则解读：比较 GDP、CPI、联邦基金利率最近两期已公布值的方向，上行记 70、下行记 30、持平记
          50；联邦基金利率上行视为流动性偏紧。
        </li>
      </ul>
    </el-dialog>
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

.reset-row {
  margin-top: -6px;
}

.small {
  font-size: 12px;
}

p.small {
  font-size: 13px;
}

.macro-top {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(0, 1.25fr) minmax(0, 0.9fr);
  align-items: start;
}

.series-board {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(0, 1.45fr) minmax(280px, 0.8fr);
  align-items: start;
}

.filter-select {
  width: 240px;
}

.series-scroll {
  max-height: 360px;
  overflow: auto;
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
  .macro-extra {
    grid-template-columns: minmax(0, 1fr);
  }

  .macro-top,
  .series-board {
    grid-template-columns: minmax(0, 1fr);
  }
}

.chart {
  width: 100%;
}

.chart-quadrant {
  height: 320px;
}

.chart-wide {
  height: 300px;
}

.chart-trend {
  height: 320px;
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

.readout-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
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

.scenario-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.scenario-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line-soft);
}

.scenario-row:last-child {
  border-bottom: 0;
}

.scenario-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.footnote {
  margin: 12px 0 0;
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
