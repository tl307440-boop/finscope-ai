import { describe, expect, it } from "vitest"
import {
  BAR_RANGE_PRESETS,
  FRED_RANGE_PRESETS,
  MAX_BAR_WINDOW_DAYS,
  MAX_SYNC_WINDOW_DAYS,
  presetRange,
  rangeEnding,
} from "../../src/api/syncRange"

function spanDays(start: string, end: string): number {
  const from = new Date(`${start}T00:00:00`)
  const to = new Date(`${end}T00:00:00`)
  return Math.round((to.getTime() - from.getTime()) / 86_400_000)
}

describe("同步区间快捷按钮", () => {
  const today = new Date(2026, 9, 18)

  it("用今天作为结束日，起始日往前推整年", () => {
    expect(rangeEnding(5, today)).toEqual({ start: "2021-10-18", end: "2026-10-18" })
    expect(rangeEnding(10, today)).toEqual({ start: "2016-10-18", end: "2026-10-18" })
    expect(rangeEnding(20, today)).toEqual({ start: "2006-10-18", end: "2026-10-18" })
    expect(rangeEnding(50, today)).toEqual({ start: "1976-10-18", end: "2026-10-18" })
  })

  it("闰日减到闰年仍是 2 月 29 日，减到平年落到 3 月 1 日", () => {
    expect(rangeEnding(4, new Date(2024, 1, 29))).toEqual({
      start: "2020-02-29",
      end: "2024-02-29",
    })
    expect(rangeEnding(1, new Date(2024, 1, 29))).toEqual({
      start: "2023-03-01",
      end: "2024-02-29",
    })
  })

  it("全部取各自接口刚好接受的最长天数", () => {
    const fredAll = presetRange(FRED_RANGE_PRESETS[4], today)
    const barAll = presetRange(BAR_RANGE_PRESETS[2], today)
    expect(spanDays(fredAll.start, fredAll.end)).toBe(MAX_SYNC_WINDOW_DAYS)
    expect(spanDays(barAll.start, barAll.end)).toBe(MAX_BAR_WINDOW_DAYS)
    const fifty = presetRange({ label: "50年", years: 50 }, today)
    expect(spanDays(fifty.start, fifty.end)).toBeLessThan(MAX_SYNC_WINDOW_DAYS)
  })

  it("宏观给到 100 年，日线全部停在 3660 天", () => {
    expect(FRED_RANGE_PRESETS.map((item) => item.label)).toEqual(["5年", "10年", "20年", "50年", "全部"])
    expect(BAR_RANGE_PRESETS.map((item) => item.label)).toEqual(["5年", "10年", "全部"])
  })
})
