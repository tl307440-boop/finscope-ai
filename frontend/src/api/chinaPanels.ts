/**
 * 中国经济页指标带以外的三块：走势图、宏观象限、规则解读。全部是纯函数，不访问网络。
 *
 * 不编造数据（AGENTS.md）：
 *  - 走势只画库里已公布的观测点，不同频率各画各的，不插值、不补点。
 *  - 象限的分界线不是人为设定的「目标值」，而是所选窗口内历史观测的中位数，可复现、可解释。
 *  - 规则解读只比较最近两期已公布值的方向，不做预测、不给概率；没有数据的指标明确列出，不参与判断。
 */
import { chinaTileCodes } from "./chinaTiles"
import type { CompareSpec } from "./macroCompare"
import { buildQuadrantModel, shiftYears, type QuadrantModel } from "./macroStats"
import { periodText, type Observation } from "./macroTiles"

/** 批量接口一次最多取回的序列数（与后端 MAX_BATCH_CODES 一致）。 */
export const BATCH_LIMIT = 20

export type PlotTone = "accent" | "orange" | "violet" | "warn" | "ok" | "cyan" | "pink" | "muted" | "teal" | "sky"

export interface PlotSpec {
  code: string
  label: string
  tone: PlotTone
  frequency: "Monthly" | "Quarterly"
}

/** 走势图可选的指标：全部是「同比 %」口径，共用一根左轴。 */
export const CHINA_PLOT_SPECS: readonly PlotSpec[] = [
  { code: "CPI_CN", label: "CPI 同比", tone: "accent", frequency: "Monthly" },
  { code: "CORE_CPI_CN", label: "核心 CPI 同比", tone: "orange", frequency: "Monthly" },
  { code: "PPI_CN", label: "PPI 同比", tone: "violet", frequency: "Monthly" },
  { code: "GDP_CN", label: "实际 GDP 当季同比", tone: "muted", frequency: "Quarterly" },
  { code: "INDUS_CN", label: "工业增加值同比", tone: "teal", frequency: "Monthly" },
  { code: "RETAIL_CN", label: "社零同比", tone: "pink", frequency: "Monthly" },
  { code: "M1_CN", label: "M1 同比", tone: "ok", frequency: "Monthly" },
  { code: "M2_CN", label: "M2 同比", tone: "sky", frequency: "Monthly" },
  { code: "EXPORT_CN", label: "出口同比", tone: "warn", frequency: "Monthly" },
]

/** 对比分析可选项：9 个同比口径指标 + 制造业 PMI（点）。同比、景气指数都不是水平值，不能指数化。 */
export const CHINA_COMPARE_SPECS: readonly CompareSpec[] = [
  ...CHINA_PLOT_SPECS.map((spec) => ({
    code: spec.code,
    label: spec.label,
    unit: "%",
    indexable: false,
    tone: spec.tone,
  })),
  { code: "PMI_CN", label: "制造业 PMI", unit: "点", indexable: false, tone: "cyan" },
]

export const COMPARE_DEFAULT: readonly string[] = ["CPI_CN", "PPI_CN", "PMI_CN"]

export const INFLATION_DEFAULT: readonly string[] = ["CPI_CN", "CORE_CPI_CN", "PPI_CN"]
export const GROWTH_DEFAULT: readonly string[] = ["GDP_CN", "INDUS_CN", "RETAIL_CN"]

/** 页面需要的全部序列：指标带 20 个在前，其余走势图序列在后（去重）。 */
export function chinaObservationCodes(): string[] {
  return [...new Set([...chinaTileCodes(), ...CHINA_PLOT_SPECS.map((spec) => spec.code)])]
}

export function chunkCodes(codes: readonly string[], size: number = BATCH_LIMIT): string[][] {
  const chunks: string[][] = []
  for (let index = 0; index < codes.length; index += size) {
    chunks.push(codes.slice(index, index + size))
  }
  return chunks
}

type Rows = Readonly<Record<string, readonly Observation[]>>

export interface TrendLine {
  spec: PlotSpec
  rows: Observation[]
}

/** 取出被选中、且至少有两期的序列，并按窗口（以最新一个观测日往前推 years 年）裁剪。 */
export function trendLines(rowsByCode: Rows, codes: readonly string[], years: number): TrendLine[] {
  const picked = CHINA_PLOT_SPECS.filter((spec) => codes.includes(spec.code))
    .map((spec) => ({ spec, rows: [...(rowsByCode[spec.code] ?? [])] }))
    .filter((entry) => entry.rows.length > 1)
  if (!picked.length) {
    return []
  }
  const newest = picked.reduce((high, entry) => {
    const last = entry.rows[entry.rows.length - 1]?.date ?? ""
    return last > high ? last : high
  }, "")
  const cutoff = shiftYears(newest, years)
  return picked.map((entry) => ({ spec: entry.spec, rows: entry.rows.filter((row) => row.date >= cutoff) }))
}

export function latestSummary(lines: readonly TrendLine[]): string[] {
  return lines.flatMap(({ spec, rows }) => {
    const last = rows[rows.length - 1]
    return last ? [`${spec.label} ${periodText(last.date, spec.frequency)} ${last.value.toFixed(1)}%`] : []
  })
}

export {
  MIN_QUADRANT_POINTS,
  QUADRANT_TEXT,
  classify,
  median,
  shiftYears,
  type QuadrantCode,
  type QuadrantModel,
  type QuadrantPoint,
} from "./macroStats"

/** 横轴：实际 GDP 当季同比；纵轴：核心 CPI 同比，取该季最后一个月。分界线 = 窗口内两轴各自的中位数。 */
export function buildQuadrant(rowsByCode: Rows, years: number): QuadrantModel | null {
  return buildQuadrantModel(rowsByCode.GDP_CN ?? [], rowsByCode.CORE_CPI_CN ?? [], years)
}
export type Direction = "up" | "down" | "flat"

export interface ReadoutIndicator {
  code: string
  label: string
  text: string
  direction: Direction
}

export interface ReadoutDimension {
  key: "growth" | "inflation" | "pmi" | "money"
  label: string
  /** 汇总结论；一条数据都没有时为「数据不足」 */
  verdict: string
  tone: "ok" | "warn" | "muted" | "accent"
  indicators: ReadoutIndicator[]
  missing: string[]
}

interface DimensionSpec {
  key: ReadoutDimension["key"]
  label: string
  items: { code: string; label: string; frequency: "Monthly" | "Quarterly" }[]
  words: { up: string; down: string; flat: string; mixed: string }
}

const DIMENSIONS: readonly DimensionSpec[] = [
  {
    key: "growth",
    label: "增长",
    items: [
      { code: "GDP_CN", label: "实际 GDP 当季同比", frequency: "Quarterly" },
      { code: "INDUS_CN", label: "工业增加值当月同比", frequency: "Monthly" },
      { code: "RETAIL_CN", label: "社零当月同比", frequency: "Monthly" },
    ],
    words: { up: "回升", down: "回落", flat: "持平", mixed: "分化" },
  },
  {
    key: "inflation",
    label: "通胀",
    items: [
      { code: "CPI_CN", label: "CPI 同比", frequency: "Monthly" },
      { code: "CORE_CPI_CN", label: "核心 CPI 同比", frequency: "Monthly" },
      { code: "PPI_CN", label: "PPI 同比", frequency: "Monthly" },
    ],
    words: { up: "上行", down: "下行", flat: "持平", mixed: "分化" },
  },
  {
    key: "money",
    label: "货币",
    items: [
      { code: "M1_CN", label: "M1 同比", frequency: "Monthly" },
      { code: "M2_CN", label: "M2 同比", frequency: "Monthly" },
    ],
    words: { up: "走宽", down: "走紧", flat: "持平", mixed: "分化" },
  },
]

/** 与指标带「较上期」同口径：先按 1 位小数取整再比较，避免 +0.0 被标成上升。 */
function directionOf(current: number, previous: number): { direction: Direction; delta: number } {
  const delta = Math.round((current - previous) * 10) / 10
  return { direction: delta > 0 ? "up" : delta < 0 ? "down" : "flat", delta: delta === 0 ? 0 : delta }
}

const DIRECTION_WORD: Record<Direction, string> = { up: "较上期上升", down: "较上期下降", flat: "与上期持平" }

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}`
}

function indicatorOf(
  rowsByCode: Rows,
  item: DimensionSpec["items"][number],
): ReadoutIndicator | "missing" | "single" {
  const rows = rowsByCode[item.code] ?? []
  const last = rows[rows.length - 1]
  if (!last) {
    return "missing"
  }
  const before = rows[rows.length - 2]
  if (!before) {
    return "single"
  }
  const { direction, delta } = directionOf(last.value, before.value)
  return {
    code: item.code,
    label: item.label,
    direction,
    text: `${periodText(last.date, item.frequency)} ${last.value.toFixed(1)}%，${DIRECTION_WORD[direction]} ${signed(delta)} pct`,
  }
}

function verdictOf(indicators: readonly ReadoutIndicator[], words: DimensionSpec["words"]) {
  const ups = indicators.filter((item) => item.direction === "up").length
  const downs = indicators.filter((item) => item.direction === "down").length
  if (ups > 0 && downs > 0) {
    return { verdict: words.mixed, tone: "warn" as const }
  }
  if (ups > 0) {
    return { verdict: words.up, tone: "accent" as const }
  }
  if (downs > 0) {
    return { verdict: words.down, tone: "accent" as const }
  }
  return { verdict: words.flat, tone: "muted" as const }
}

/** 景气：制造业 PMI 相对 50 荣枯线（国家统计局口径）。 */
function pmiDimension(rowsByCode: Rows): ReadoutDimension {
  const last = (rowsByCode.PMI_CN ?? []).at(-1)
  if (!last) {
    return {
      key: "pmi",
      label: "景气",
      verdict: "数据不足",
      tone: "muted",
      indicators: [],
      missing: ["制造业 PMI"],
    }
  }
  const above = last.value >= 50
  return {
    key: "pmi",
    label: "景气",
    verdict: above ? "扩张区间" : "收缩区间",
    tone: above ? "ok" : "warn",
    indicators: [
      {
        code: "PMI_CN",
        label: "制造业 PMI",
        direction: "flat",
        text: `${periodText(last.date, "Monthly")} ${last.value.toFixed(1)}，${above ? "高于" : "低于"} 50 荣枯线`,
      },
    ],
    missing: [],
  }
}

/**
 * 规则解读：增长、通胀、货币三个维度各自比较最近两期已公布值的方向，景气按 PMI 荣枯线。
 * 只有一期或没有数据的指标不参与方向判断，并在 missing 里列出。
 */
export function buildReadout(rowsByCode: Rows): ReadoutDimension[] {
  const dimensions = DIMENSIONS.map((spec): ReadoutDimension => {
    const indicators: ReadoutIndicator[] = []
    const missing: string[] = []
    for (const item of spec.items) {
      const result = indicatorOf(rowsByCode, item)
      if (typeof result === "string") {
        missing.push(item.label)
      } else {
        indicators.push(result)
      }
    }
    if (!indicators.length) {
      return { key: spec.key, label: spec.label, verdict: "数据不足", tone: "muted", indicators, missing }
    }
    return { key: spec.key, label: spec.label, ...verdictOf(indicators, spec.words), indicators, missing }
  })
  const [growth, inflation, money] = dimensions as [ReadoutDimension, ReadoutDimension, ReadoutDimension]
  return [growth, inflation, pmiDimension(rowsByCode), money]
}
