/**
 * 宏观象限与情景推演的通用统计函数（美国页、中国页共用）。纯函数，不访问网络。
 *
 * 不编造数据（AGENTS.md）：
 *  - 象限分界线 = 所选窗口内两条轴各自的历史中位数，不是人为设定的目标值。
 *  - 情景推演里的「概率」只来自历史上相邻两个季度的象限转移次数（k / n），样本太少时不给百分比；
 *    区间只来自历史季度变化量的 10% / 50% / 90% 分位数。这是描述性统计，不是预测。
 */
import { periodText, type Observation } from "./macroTiles"

type Rows = readonly Observation[]

export function shiftYears(date: string, years: number): string {
  return `${Number(date.slice(0, 4)) - years}${date.slice(4)}`
}

export function median(values: readonly number[]): number | null {
  if (!values.length) {
    return null
  }
  const sorted = [...values].sort((left, right) => left - right)
  const middle = Math.floor(sorted.length / 2)
  const upper = sorted[middle] as number
  return sorted.length % 2 ? upper : ((sorted[middle - 1] as number) + upper) / 2
}

/** 线性插值分位数（与 numpy 默认一致）。q 取 0–1。 */
export function quantile(values: readonly number[], q: number): number | null {
  if (!values.length || q < 0 || q > 1) {
    return null
  }
  const sorted = [...values].sort((left, right) => left - right)
  const position = (sorted.length - 1) * q
  const lower = Math.floor(position)
  const upper = Math.ceil(position)
  const low = sorted[lower] as number
  const high = sorted[upper] as number
  return low + (high - low) * (position - lower)
}

/** 季度观测日（取季首日）→ 该季最后一个月，如 2026-04-01 → 2026-06。 */
export function quarterLastMonth(date: string): string {
  const year = Number(date.slice(0, 4))
  const month = Number(date.slice(5, 7)) + 2
  return `${year}-${String(month).padStart(2, "0")}`
}

function monthIndex(date: string): number {
  return Number(date.slice(0, 4)) * 12 + Number(date.slice(5, 7))
}

export type QuadrantCode = "overheat" | "goldilocks" | "stagflation" | "weak"

export const QUADRANT_CODES: readonly QuadrantCode[] = ["overheat", "goldilocks", "stagflation", "weak"]

export const QUADRANT_TEXT: Record<QuadrantCode, { label: string; sub: string }> = {
  overheat: { label: "过热风险", sub: "增长高 · 通胀高" },
  goldilocks: { label: "良性扩张", sub: "增长高 · 通胀低" },
  stagflation: { label: "滞胀风险", sub: "增长低 · 通胀高" },
  weak: { label: "低迷 / 通缩风险", sub: "增长低 · 通胀低" },
}

export function classify(growth: number, inflation: number, growthMid: number, inflationMid: number): QuadrantCode {
  const strong = growth >= growthMid
  const hot = inflation >= inflationMid
  if (strong) {
    return hot ? "overheat" : "goldilocks"
  }
  return hot ? "stagflation" : "weak"
}

export interface QuadrantPoint {
  /** 季度观测日（季首日） */
  date: string
  period: string
  growth: number
  inflation: number
}

export interface QuadrantModel {
  points: QuadrantPoint[]
  growthMid: number
  inflationMid: number
  latest: QuadrantPoint
  code: QuadrantCode
}

/** 至少需要这么多个配对点，中位数分界才有意义。 */
export const MIN_QUADRANT_POINTS = 4

/** 增长（季度，季首日）与通胀（月度）配对：通胀取该季最后一个月，缺该月的季度跳过，不用邻月替代。 */
export function pairQuarterly(growth: Rows, inflation: Rows): QuadrantPoint[] {
  const byMonth = new Map(inflation.map((row) => [row.date.slice(0, 7), row.value]))
  return growth.flatMap((row) => {
    const value = byMonth.get(quarterLastMonth(row.date))
    return value === undefined
      ? []
      : [{ date: row.date, period: periodText(row.date, "Quarterly"), growth: row.value, inflation: value }]
  })
}

/** 窗口 = 最新配对季度往前推 years 年；分界 = 窗口内两轴各自的中位数。 */
export function buildQuadrantModel(growth: Rows, inflation: Rows, years: number): QuadrantModel | null {
  const paired = pairQuarterly(growth, inflation)
  const newest = paired[paired.length - 1]
  if (!newest) {
    return null
  }
  const cutoff = shiftYears(newest.date, years)
  const points = paired.filter((point) => point.date >= cutoff)
  const latest = points[points.length - 1]
  if (!latest || points.length < MIN_QUADRANT_POINTS) {
    return null
  }
  const growthMid = median(points.map((point) => point.growth))
  const inflationMid = median(points.map((point) => point.inflation))
  if (growthMid === null || inflationMid === null) {
    return null
  }
  return {
    points,
    growthMid,
    inflationMid,
    latest,
    code: classify(latest.growth, latest.inflation, growthMid, inflationMid),
  }
}

/** 某个当前象限下，历史上下一个季度落在各象限的次数少于这个数时，不给百分比。 */
export const MIN_TRANSITIONS = 5
/** 季度变化量样本少于这个数时，不给区间。 */
export const MIN_CHANGES = 8

export interface ScenarioRow {
  code: QuadrantCode
  count: number
  /** 样本足够时才有；否则为 null，页面只显示次数 */
  share: number | null
}

export interface ChangeRange {
  latest: number
  /** 历史季度变化量的 10% / 50% / 90% 分位数叠加到最新值上 */
  low: number
  mid: number
  high: number
  samples: number
}

export interface ScenarioModel {
  from: QuadrantCode
  /** 历史上处于当前象限、且下一季度已有数据的季度数 */
  transitions: number
  sufficient: boolean
  rows: ScenarioRow[]
  growthRange: ChangeRange | null
  inflationRange: ChangeRange | null
  quarters: number
  firstPeriod: string
  lastPeriod: string
  growthMid: number
  inflationMid: number
}

function changeRange(values: readonly number[], latest: number): ChangeRange | null {
  if (values.length < MIN_CHANGES) {
    return null
  }
  const low = quantile(values, 0.1)
  const mid = quantile(values, 0.5)
  const high = quantile(values, 0.9)
  if (low === null || mid === null || high === null) {
    return null
  }
  return { latest, low: latest + low, mid: latest + mid, high: latest + high, samples: values.length }
}

/**
 * 情景推演（统计口径）：
 *  - 象限转移：窗口内只统计「相邻两个季度」（观测日恰好相差 3 个月）的前后象限；
 *  - 区间：相邻季度的变化量分布，叠加到最新值上，给出下一季度的历史分位区间。
 */
export function buildScenario(model: QuadrantModel): ScenarioModel {
  const { points, growthMid, inflationMid } = model
  const states = points.map((point) => classify(point.growth, point.inflation, growthMid, inflationMid))
  const counts: Record<QuadrantCode, number> = { overheat: 0, goldilocks: 0, stagflation: 0, weak: 0 }
  const growthChanges: number[] = []
  const inflationChanges: number[] = []
  let transitions = 0
  for (let index = 0; index + 1 < points.length; index += 1) {
    const current = points[index] as QuadrantPoint
    const next = points[index + 1] as QuadrantPoint
    if (monthIndex(next.date) - monthIndex(current.date) !== 3) {
      continue
    }
    growthChanges.push(next.growth - current.growth)
    inflationChanges.push(next.inflation - current.inflation)
    if (states[index] === model.code) {
      transitions += 1
      counts[states[index + 1] as QuadrantCode] += 1
    }
  }
  const sufficient = transitions >= MIN_TRANSITIONS
  return {
    from: model.code,
    transitions,
    sufficient,
    rows: QUADRANT_CODES.map((code) => ({
      code,
      count: counts[code],
      share: sufficient ? counts[code] / transitions : null,
    })),
    growthRange: changeRange(growthChanges, model.latest.growth),
    inflationRange: changeRange(inflationChanges, model.latest.inflation),
    quarters: points.length,
    firstPeriod: (points[0] as QuadrantPoint).period,
    lastPeriod: model.latest.period,
    growthMid,
    inflationMid,
  }
}
