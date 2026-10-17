/** 产业研究两张页面共用的取色与展示格式。只做显示，不产生新的财务数字。 */
import { formatAmount } from "../../../api/json"

export function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") {
    return fallback
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

function hexToRgb(hex: string): [number, number, number] | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match?.[1]) {
    return null
  }
  const value = Number.parseInt(match[1], 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

function mix(from: [number, number, number], to: [number, number, number], ratio: number): string {
  const channel = (index: number): number => Math.round(from[index]! + (to[index]! - from[index]!) * ratio)
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`
}

/** A 股红涨绿跌；|pct| 达到 range 时取满色，0 附近是中性底色。 */
export function heatColor(pct: number | null, range: number): string {
  const neutral = hexToRgb(cssVar("--panel-raise", "#1a2b49")) ?? [26, 43, 73]
  if (pct === null || !Number.isFinite(pct)) {
    return mix(neutral, neutral, 0)
  }
  const rise = hexToRgb(cssVar("--rise", "#f6465d")) ?? [246, 70, 93]
  const fall = hexToRgb(cssVar("--fall", "#1fc37d")) ?? [31, 195, 125]
  const ratio = Math.min(1, Math.abs(pct) / range) * 0.92 + (pct === 0 ? 0 : 0.08)
  return mix(neutral, pct > 0 ? rise : fall, ratio)
}

export function pctText(value: number | null, digits = 2): string {
  if (value === null || !Number.isFinite(value)) {
    return "—"
  }
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`
}

export function yuan(value: number | null, digits = 2): string {
  return formatAmount(value, "", digits)
}

/** 金额换成「亿」的数值，图表坐标轴统一用亿元。 */
export function toYi(value: number | null): number | null {
  return value === null ? null : Number((value / 1e8).toFixed(2))
}

export function shortTime(iso: string): string {
  if (!iso) {
    return ""
  }
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return ""
  }
  const pad = (part: number): string => String(part).padStart(2, "0")
  return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`
}

export function sectionTone(status: string): "ok" | "warn" | "danger" {
  return status === "ok" ? "ok" : status === "stale" ? "warn" : "danger"
}

export function sectionLabel(status: string): string {
  return status === "ok" ? "实时" : status === "stale" ? "旧数据" : "不可用"
}
