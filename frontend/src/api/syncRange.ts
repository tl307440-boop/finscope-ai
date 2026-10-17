/** 数据管理里「同步」表单的快捷观察区间。 */

export interface SyncRangePreset {
  label: string
  /** 按整年回推。与 days 二选一。 */
  years?: number
  /** 按自然日回推。用于「全部」，对齐同步接口的默认上限。 */
  days?: number
}

/** 与 MACRO_SYNC_MAX_WINDOW_DAYS 的默认值一致。比较用的是「大于」，正好等于上限可以通过。 */
export const MAX_SYNC_WINDOW_DAYS = 36500

/** 与 A 股日线 valid_window 的 3660 天一致。比较用的是「不超过」，正好等于上限可以通过。 */
export const MAX_BAR_WINDOW_DAYS = 3660

export const FRED_RANGE_PRESETS: readonly SyncRangePreset[] = [
  { label: "5年", years: 5 },
  { label: "10年", years: 10 },
  { label: "20年", years: 20 },
  { label: "50年", years: 50 },
  { label: "全部", days: MAX_SYNC_WINDOW_DAYS },
]

/** A 股日线超过 3660 天会被拒绝，全部取这个上限。 */
export const BAR_RANGE_PRESETS: readonly SyncRangePreset[] = [
  { label: "5年", years: 5 },
  { label: "10年", years: 10 },
  { label: "全部", days: MAX_BAR_WINDOW_DAYS },
]

export function isoDate(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0")
  const day = String(value.getDate()).padStart(2, "0")
  return `${value.getFullYear()}-${month}-${day}`
}

/** 截至 today 的整年区间。2 月 29 日减到非闰年时落到 3 月 1 日。 */
export function rangeEnding(years: number, today: Date): { start: string; end: string } {
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const start = new Date(end)
  start.setFullYear(end.getFullYear() - years)
  return { start: isoDate(start), end: isoDate(end) }
}

export function rangeEndingDays(days: number, today: Date): { start: string; end: string } {
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const start = new Date(end)
  start.setDate(end.getDate() - days)
  return { start: isoDate(start), end: isoDate(end) }
}

export function presetRange(preset: SyncRangePreset, today: Date): { start: string; end: string } {
  if (preset.days !== undefined) {
    return rangeEndingDays(preset.days, today)
  }
  return rangeEnding(preset.years ?? 0, today)
}
