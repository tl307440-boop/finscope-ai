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

/** 「较上期」只认紧邻上一期；日度遇周末、节假日是常态，不做要求。 */
function isPreviousPeriod(previous: string, current: string, frequency: string): boolean {
  if (frequency === "Monthly") {
    return monthIndex(current) - monthIndex(previous) === 1
  }
  if (frequency === "Quarterly") {
    return monthIndex(current) - monthIndex(previous) === 3
  }
  if (frequency === "Weekly") {
    return Math.round((Date.parse(current) - Date.parse(previous)) / 86_400_000) === 7
  }
  return frequency === "Daily"
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
  if (spec.scale !== undefined) {
    parts.push(`展示值 = 原值 × ${spec.scale}（${spec.unit}）`)
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
  const sparkPoints = SPARK_POINTS[frequency] ?? 36
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
      spark: points.slice(-sparkPoints).map((point) => point.high),
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
  const delta =
    previous && isPreviousPeriod(previous.date, latest.date, frequency)
      ? roundedDelta(latest.value, previous.value, digits)
      : null
  return {
    ...base,
    value: formatNumber(latest.value, digits),
    date: periodText(latest.date, frequency),
    delta,
    deltaText: deltaText(delta, digits, spec.deltaSuffix),
    direction: directionOf(delta),
    spark: series.slice(-sparkPoints).map((row) => row.value),
  }
}
