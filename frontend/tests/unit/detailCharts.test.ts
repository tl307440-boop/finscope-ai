import { describe, expect, it } from "vitest"

import { buildIntradayOption, buildKlineOption, calcMA, hasValues } from "../../src/components/market/detailCharts"

function seriesNames(option: { series?: unknown }): string[] {
  const series = (option.series ?? []) as Array<{ name?: string }>
  return series.map((s) => String(s.name))
}

function gridCount(option: { grid?: unknown }): number {
  return Array.isArray(option.grid) ? option.grid.length : 0
}

describe("hasValues", () => {
  it("只要有一个真实数值就算有（0 算）；全 null / NaN / 空数组都算没有", () => {
    expect(hasValues([null, 0, null])).toBe(true)
    expect(hasValues([null, null])).toBe(false)
    expect(hasValues([Number.NaN, undefined])).toBe(false)
    expect(hasValues([])).toBe(false)
  })
})

describe("buildIntradayOption", () => {
  const times = ["09:30", "09:31", "09:32"]
  const prices = [3600, 3610, 3605]

  it("均价和成交量都有：现价 + 均价 + 量柱，两个绘图区", () => {
    const option = buildIntradayOption({ times, prices, avgs: [3600, 3605, 3606], vols: [10, 20, 30], isUp: true })
    expect(seriesNames(option)).toEqual(["分时现价", "分时均价", "成交量"])
    expect(gridCount(option)).toBe(2)
  })

  it("均价全是 null：不画均价线（不画成一条 0）", () => {
    const option = buildIntradayOption({ times, prices, avgs: [null, null, null], vols: [10, 20, 30], isUp: true })
    expect(seriesNames(option)).toEqual(["分时现价", "成交量"])
  })

  it("成交量全是 null：不画量柱，只剩一个绘图区，时间轴标签在主图上", () => {
    const option = buildIntradayOption({ times, prices, avgs: [3600, 3605, 3606], vols: [null, null, null], isUp: false })
    expect(seriesNames(option)).toEqual(["分时现价", "分时均价"])
    expect(gridCount(option)).toBe(1)
    const xAxis = option.xAxis as Array<{ axisLabel?: { show?: boolean } }>
    expect(xAxis).toHaveLength(1)
    expect(xAxis[0]?.axisLabel?.show).not.toBe(false)
  })

  it("时间轴按接口返回的顺序，不按 HH:MM 排序（美股分时跨北京时间午夜）", () => {
    const usTimes = ["23:30", "23:59", "00:00", "04:59"]
    const option = buildIntradayOption({ times: usTimes, prices: [1, 2, 3, 4], avgs: [null, null, null, null], vols: [null, null, null, null], isUp: true })
    const xAxis = option.xAxis as Array<{ data?: string[] }>
    expect(xAxis[0]?.data).toEqual(usTimes)
  })
})

describe("buildKlineOption", () => {
  const bar = (date: string, close: number, volume: number | null) => ({ date, open: close - 1, close, low: close - 2, high: close + 2, volume })

  it("有成交量：K 线 + MA + 量柱", () => {
    const option = buildKlineOption([bar("2026-11-04", 10, 100), bar("2026-11-05", 11, 120)])
    expect(seriesNames(option)).toEqual(["日K", "MA5", "MA10", "MA20", "成交量"])
    expect(gridCount(option)).toBe(2)
  })

  it("黄金 / 原油日K没有成交量：不画量柱", () => {
    const option = buildKlineOption([bar("2026-11-04", 10, null), bar("2026-11-05", 11, null)])
    expect(seriesNames(option)).toEqual(["日K", "MA5", "MA10", "MA20"])
    expect(gridCount(option)).toBe(1)
  })

  it("日期去掉年份，保持升序输入顺序", () => {
    const option = buildKlineOption([bar("2026-11-04", 10, null), bar("2026-11-05", 11, null)])
    const xAxis = option.xAxis as Array<{ data?: string[] }>
    expect(xAxis[0]?.data).toEqual(["11-04", "11-05"])
  })
})

describe("calcMA", () => {
  it("不足周期的位置是 null，够了取均值并保留两位小数", () => {
    expect(calcMA(3, [1, 2, 3, 4])).toEqual([null, null, 2, 3])
  })
})
