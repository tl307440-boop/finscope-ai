<script setup lang="ts">
/**
 * 全球宏观分析（/macro/us，效果图 2）。
 *
 * 真实接口（与 backend/app/api/v1/macro.py、schemas/macro.py、catalog/macro_series.py 一致）：
 *  - GET /api/v1/macro/series
 *      data = { provider_code, availability, notice, series[] }
 *      availability：configured / unavailable / not_required / public_csv（无密钥时走 FRED 公开 CSV，只有最新修订值）
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *      observations 按观测日升序，limit 保留最近 N 条，最后一条即最新值。
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value, reason, evidence[] },
 *               notice, version }
 *
 * 不编造数据（AGENTS.md 硬约束）：
 *  - 指标带只显示真实观测值；CPI / 核心 PCE 的同比按「12 个月前同月」的观测值计算，找不到就不显示。
 *  - 宏观象限只用后端规则分数定位；分数取不到时只画坐标系，不落点。
 *  - 规则解读正文来自 assessments 的 regime_code / evidence，不是模型生成文案。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf, scaleAmount } from "../api/json"
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
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"
type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

interface CatalogItem {
  code: string
  name: string
  nameEn: string
  category: string
  frequency: string
  unit: string
  adjustment: string
}

interface Observation {
  date: string
  value: number
}

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

interface TileView {
  code: string
  label: string
  value: string
  unit: string
  period: string
  info: string
  delta: number | null
  deltaSuffix: string
  spark: number[]
}

/** 指标带顺序与效果图一致。 */
const TILE_CODES = ["GDP", "CPIAUCSL", "PCEPILFE", "UNRATE", "FEDFUNDS", "DGS10", "T10Y2Y", "DGS2"] as const

/** 价格指数在指标带与通胀图里统一换算成同比。 */
const YOY_CODES = new Set<string>(["CPIAUCSL", "PCEPILFE"])
const YOY_LABELS: Record<string, string> = { CPIAUCSL: "CPI 同比", PCEPILFE: "核心 PCE 同比" }

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

const yoyByCode = computed<Record<string, Observation[]>>(() => {
  const result: Record<string, Observation[]> = {}
  for (const code of YOY_CODES) {
    result[code] = yearOverYear(rowsOf(code))
  }
  return result
})

function formatPlain(value: number, digits = 2): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function monthText(date: string, frequency: string): string {
  if (frequency === "Quarterly") {
    const month = Number(date.slice(5, 7))
    return `${date.slice(0, 4)} Q${Math.floor((month - 1) / 3) + 1}`
  }
  if (frequency === "Monthly") {
    return date.slice(0, 7)
  }
  return date
}

const tiles = computed<TileView[]>(() =>
  TILE_CODES.map((code) => {
    const item = catalogItem(code)
    const rows = rowsOf(code)
    const frequency = item?.frequency ?? ""
    const baseInfo = item
      ? `${item.nameEn || item.name} · FRED 代码 ${code} · 单位 ${unitLabel(item.unit)} · ${adjustmentLabel(item.adjustment)}`
      : `暂未接入 ${code}`
    const yoy = YOY_CODES.has(code) ? (yoyByCode.value[code] ?? []) : []
    if (yoy.length) {
      const latest = yoy[yoy.length - 1] as Observation
      const previous = yoy.length > 1 ? (yoy[yoy.length - 2] as Observation) : null
      const level = rows[rows.length - 1]
      return {
        code,
        label: YOY_LABELS[code] ?? `${item?.name ?? code} 同比`,
        value: formatPlain(latest.value),
        unit: "%",
        period: `${monthText(latest.date, frequency)} · ${frequencyLabel(frequency)}`,
        info: `${baseInfo}。同比 = 当月指数 ÷ 12 个月前同月指数 − 1${level ? `；最新指数 ${formatPlain(level.value)}` : ""}`,
        delta: previous ? latest.value - previous.value : null,
        deltaSuffix: " pct",
        spark: yoy.slice(-36).map((row) => row.value),
      }
    }
    const latest = rows.length ? (rows[rows.length - 1] as Observation) : null
    const previous = rows.length > 1 ? (rows[rows.length - 2] as Observation) : null
    const unit = item?.unit ?? ""
    let value = "—"
    let shownUnit = ""
    if (latest) {
      if (unit === "Billions of Dollars") {
        const scaled = scaleAmount(latest.value * 1e9)
        value = scaled.text
        shownUnit = `${scaled.scale}美元`
      } else if (unit === "Percent") {
        value = formatPlain(latest.value)
        shownUnit = "%"
      } else {
        value = formatPlain(latest.value)
      }
    }
    let delta: number | null = null
    let deltaSuffix = ""
    if (latest && previous) {
      if (unit === "Percent") {
        delta = latest.value - previous.value
        deltaSuffix = " pct"
      } else if (previous.value !== 0) {
        delta = (latest.value / previous.value - 1) * 100
        deltaSuffix = "%"
      }
    }
    return {
      code,
      label: item?.name ?? code,
      value,
      unit: shownUnit,
      period: latest ? `${monthText(latest.date, frequency)} · ${frequencyLabel(frequency)}` : "暂无数据",
      info: baseInfo,
      delta,
      deltaSuffix,
      spark: rows.slice(-60).map((row) => row.value),
    }
  }),
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
    text: cssColor("--text", "#e6edf8"),
    muted: cssColor("--muted", "#94a4be"),
    line: cssColor("--line", "rgba(96,140,220,0.16)"),
    panel: cssColor("--panel", "#0d1729"),
  }
}

function percentTip(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(2)}%` : "—"
}

/** 通胀走势：CPI 与核心 PCE 的同比（%），和 2% 目标同一口径。 */
const inflationOption = computed<EChartsCoreOption | null>(() => {
  const cpi = (yoyByCode.value["CPIAUCSL"] ?? []).slice(-60)
  const pce = (yoyByCode.value["PCEPILFE"] ?? []).slice(-60)
  if (cpi.length < 2 && pce.length < 2) {
    return null
  }
  const colors = palette()
  const dates = [...new Set([...cpi, ...pce].map((row) => row.date))].sort()
  const lookup = (rows: Observation[]) => {
    const map = new Map(rows.map((row) => [row.date, row.value]))
    return dates.map((date) => map.get(date) ?? null)
  }
  return {
    grid: { left: 8, right: 44, top: 34, bottom: 4, containLabel: true },
    legend: { top: 0, right: 0, data: ["CPI 同比", "核心 PCE 同比"] },
    tooltip: { trigger: "axis", valueFormatter: percentTip },
    xAxis: {
      type: "category",
      data: dates.map((date) => date.slice(0, 7)),
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: "{value}%" },
    },
    series: [
      {
        name: "CPI 同比",
        type: "line",
        smooth: true,
        connectNulls: true,
        data: lookup(cpi),
        lineStyle: { color: colors.accent, width: 2 },
        itemStyle: { color: colors.accent },
        areaStyle: { color: colors.accent, opacity: 0.08 },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: colors.muted, type: "dashed" },
          label: { color: colors.muted, fontSize: 11, formatter: "目标 2%", position: "end" },
          data: [{ yAxis: 2 }],
        },
      },
      {
        name: "核心 PCE 同比",
        type: "line",
        smooth: true,
        connectNulls: true,
        data: lookup(pce),
        lineStyle: { color: colors.orange, width: 2 },
        itemStyle: { color: colors.orange },
      },
    ],
  }
})

const inflationLatest = computed<string>(() => {
  const parts: string[] = []
  const cpi = yoyByCode.value["CPIAUCSL"] ?? []
  const pce = yoyByCode.value["PCEPILFE"] ?? []
  const lastCpi = cpi[cpi.length - 1]
  const lastPce = pce[pce.length - 1]
  if (lastCpi) {
    parts.push(`CPI ${lastCpi.date.slice(0, 7)} ${lastCpi.value.toFixed(2)}%`)
  }
  if (lastPce) {
    parts.push(`核心 PCE ${lastPce.date.slice(0, 7)} ${lastPce.value.toFixed(2)}%`)
  }
  return parts.join(" · ")
})

/** 利率：联邦基金利率（月度阶梯）与 2Y / 10Y 美债收益率（日度），用时间轴对齐不同频率。 */
const ratesOption = computed<EChartsCoreOption | null>(() => {
  const dgs2 = rowsOf("DGS2")
  const dgs10 = rowsOf("DGS10")
  const daily = [...dgs2, ...dgs10]
  const start = daily.length ? daily.reduce((low, row) => (row.date < low ? row.date : low), daily[0]!.date) : ""
  const fed = rowsOf("FEDFUNDS").filter((row) => !start || row.date >= start.slice(0, 7))
  const lines = [
    { name: "联邦基金利率", rows: start ? fed : fed.slice(-48), step: true },
    { name: "10 年期美债", rows: dgs10, step: false },
    { name: "2 年期美债", rows: dgs2, step: false },
  ].filter((line) => line.rows.length > 1)
  if (!lines.length) {
    return null
  }
  const colors = palette()
  const tones: Record<string, string> = {
    联邦基金利率: colors.warn,
    "10 年期美债": colors.accent,
    "2 年期美债": colors.cyan,
  }
  return {
    grid: { left: 8, right: 12, top: 34, bottom: 4, containLabel: true },
    legend: { top: 0, right: 0, data: lines.map((line) => line.name) },
    tooltip: { trigger: "axis", valueFormatter: percentTip },
    xAxis: { type: "time", axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: "{value}%" } },
    series: lines.map((line) => ({
      name: line.name,
      type: "line",
      step: line.step ? "end" : undefined,
      showSymbol: false,
      data: line.rows.map((row) => [row.date, row.value]),
      lineStyle: { color: tones[line.name], width: line.step ? 2 : 1.6 },
      itemStyle: { color: tones[line.name] },
    })),
  }
})

const spreadNote = computed<string>(() => {
  const rows = rowsOf("T10Y2Y")
  const last = rows[rows.length - 1]
  if (!last) {
    return ""
  }
  const shape = last.value >= 0 ? "曲线未倒挂" : "曲线倒挂"
  return `10Y−2Y 利差 ${last.value.toFixed(2)} pct（${last.date}，${shape}）`
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
  const steps = points.map((point) => `${monthText(point.date, frequency)} ${evidenceValue(point.code, point.value)}`)
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
        category: asString(item.category) ?? "",
        frequency: asString(item.frequency) ?? "",
        unit: asString(item.base_unit) ?? "",
        adjustment: asString(item.seasonal_adjustment) ?? "",
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

/** limit 保留最近 N 条：日频取约两年交易日，月/季频取 10 年，足够算同比和画走势。 */
function limitFor(code: string): number {
  return catalogItem(code)?.frequency === "Daily" ? 520 : 120
}

async function loadObservations(): Promise<void> {
  observationsBusy.value = true
  const codes = TILE_CODES.filter((code) => catalog.value.some((item) => item.code === code))
  try {
    const results = await Promise.all(
      codes.map(async (code) => {
        const params = new URLSearchParams({ limit: String(limitFor(code)) })
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
        const rows = Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : []
        rows.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
        return { code, rows }
      }),
    )
    const next: Record<string, Observation[]> = {}
    for (const item of results) {
      next[item.code] = item.rows
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

        <div class="grid-8">
          <MetricTile
            v-for="tile in tiles"
            :key="tile.code"
            :label="tile.label"
            :value="tile.value"
            :unit="tile.unit"
            :period="tile.period"
            :info="tile.info"
            delta-label="较上期"
          >
            <template #chart>
              <MiniSpark
                v-if="tile.spark.length > 1"
                :points="tile.spark"
                kind="area"
                tone="accent"
                :width="96"
                :height="30"
              />
            </template>
            <template #delta>
              <DeltaValue :value="tile.delta" :suffix="tile.deltaSuffix" :colored="false" signed size="sm" />
            </template>
          </MetricTile>
        </div>

        <div class="macro-main">
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

          <div class="stack">
            <PanelCard title="通胀走势" info="CPI 与核心 PCE 的同比（%）；同比 = 当月指数 ÷ 12 个月前同月指数 − 1">
              <template #icon><span class="panel-icon">▲</span></template>
              <template #actions>
                <span v-if="inflationLatest" class="muted small">{{ inflationLatest }}</span>
              </template>
              <VChart v-if="inflationOption" class="chart chart-half" :option="inflationOption" autoresize />
              <StateBlock v-else state="empty" message="不足 13 个月的物价数据，暂无法计算同比" />
            </PanelCard>

            <PanelCard title="联邦基金利率与美债收益率" info="联邦基金有效利率（月度）与 2 年、10 年期美债收益率（日度）">
              <template #icon><span class="panel-icon">▤</span></template>
              <VChart v-if="ratesOption" class="chart chart-half" :option="ratesOption" autoresize />
              <StateBlock v-else state="empty" message="利率数据不足两期，暂不画走势" />
              <p v-if="spreadNote" class="chart-note">{{ spreadNote }}</p>
            </PanelCard>
          </div>

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

        <SourceFooter
          :as-of="latestObservationDate || appliedAsOf"
          :sources="[sourceLabel]"
          :stale="stale"
          note="指标「较上期」为相邻两个已公布观测值之差；利率类按百分点，其余按百分比。"
        />
      </template>
    </div>

    <el-dialog v-model="helpOpen" title="数据说明" width="600px">
      <ul class="doc-list">
        <li>数据来源：{{ sourceLabel }}，观察日 {{ appliedAsOf }}。</li>
        <li v-if="availability === 'public_csv'">
          公开 CSV 只提供最新修订值，不能回看历史某天当时公布的版本；选择过去的观察日将看不到数据。
        </li>
        <li>CPI、核心 PCE 显示同比：当月指数 ÷ 12 个月前同月指数 − 1；缺少上年同月数据的月份不显示。</li>
        <li>GDP 为现价、季调年化口径（非实际增速）；「较上期」为环比变化。</li>
        <li>利率、失业率、利差的「较上期」单位为百分点（pct）。</li>
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

.macro-main {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 1.1fr) minmax(0, 1fr);
  align-items: start;
}

@media (max-width: 1440px) {
  .macro-main {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .macro-main > :last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 1024px) {
  .macro-main {
    grid-template-columns: minmax(0, 1fr);
  }
}

.chart {
  width: 100%;
}

.chart-quadrant {
  height: 360px;
}

.chart-half {
  height: 210px;
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
</style>
