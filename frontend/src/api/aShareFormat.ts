/**
 * 看板通用格式化：null/NaN 一律显示「—」，不把缺失当成 0。
 * 涨跌颜色遵循 A 股惯例：红涨绿跌（对应 CSS 类 is-rise / is-fall）。
 */

export const EMPTY_TEXT = "—"

function isNum(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

export function fmtNum(value: number | null | undefined, digits = 2): string {
  return isNum(value) ? value.toFixed(digits) : EMPTY_TEXT
}

export function fmtInt(value: number | null | undefined): string {
  return isNum(value) ? String(Math.round(value)) : EMPTY_TEXT
}

export function fmtPct(value: number | null | undefined, digits = 2, signed = true): string {
  if (!isNum(value)) return EMPTY_TEXT
  const sign = signed && value > 0 ? "+" : ""
  return `${sign}${value.toFixed(digits)}%`
}

export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return EMPTY_TEXT
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`
}

/** 亿元；`signed` 用于净流入/日变动。 */
export function fmtYi(value: number | null | undefined, digits = 2, signed = false): string {
  if (!isNum(value)) return EMPTY_TEXT
  const sign = signed && value > 0 ? "+" : ""
  return `${sign}${value.toFixed(digits)}亿`
}

/** 万元自动折算：≥1 亿显示亿。 */
export function fmtWan(value: number | null | undefined, signed = false): string {
  if (!isNum(value)) return EMPTY_TEXT
  const sign = signed && value > 0 ? "+" : ""
  const abs = Math.abs(value)
  if (abs >= 10000) return `${sign}${(value / 10000).toFixed(2)}亿`
  return `${sign}${value.toFixed(0)}万`
}

export function toneClass(value: number | null | undefined): "is-rise" | "is-fall" | "" {
  if (!isNum(value) || value === 0) return ""
  return value > 0 ? "is-rise" : "is-fall"
}

/** 手数（期货持仓）：千分位。 */
export function fmtLots(value: number | null | undefined, signed = false): string {
  if (!isNum(value)) return EMPTY_TEXT
  const sign = signed && value > 0 ? "+" : ""
  return `${sign}${Math.round(value).toLocaleString("zh-CN")}`
}
