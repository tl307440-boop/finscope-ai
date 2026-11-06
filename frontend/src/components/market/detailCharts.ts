/**
 * 右侧详情面板（个股 / 指数）共用的分时、日K 图表配置。
 *
 * 个股的均价、量柱一定有；指数 / 商品上游可能没给均价或成交量（腾讯没有均价、黄金原油日K没有成交量），
 * 这时不画对应的线 / 柱，而不是画一条 0：没有量柱就只留一个绘图区，时间轴标签挪到主图下面。
 */
import type { EChartsOption } from "echarts"

const RISE = "#f6465d"
const FALL = "#1fc37d"
const AXIS_LINE = { lineStyle: { color: "rgba(148,163,184,0.25)" } }
const AXIS_LABEL = { fontSize: 10, color: "#64748b" }
const SPLIT_LINE = { lineStyle: { color: "rgba(148,163,184,0.14)", type: "dashed" as const } }

/** 数组里有没有至少一个真实数值（null / NaN 不算；0 算）。 */
export function hasValues(list: ReadonlyArray<number | null | undefined>): boolean {
  return list.some((v) => typeof v === "number" && Number.isFinite(v))
}

export interface IntradayChartInput {
  times: string[]
  prices: number[]
  avgs: Array<number | null>
  vols: Array<number | null>
  /** 涨跌方向，决定现价线 / 面积的颜色 */
  isUp: boolean
}

export function buildIntradayOption(input: IntradayChartInput): EChartsOption {
  const { times, prices, avgs, vols, isUp } = input
  const showAvg = hasValues(avgs)
  const showVol = hasValues(vols)
  const mainColor = isUp ? RISE : FALL

  const grid = showVol
    ? [
        { left: 42, right: 12, top: 10, height: "58%" },
        { left: 42, right: 12, top: "74%", height: "20%" },
      ]
    : [{ left: 42, right: 12, top: 10, bottom: 24 }]

  const xAxis = showVol
    ? [
        { type: "category" as const, data: times, boundaryGap: false, axisLabel: { show: false }, axisLine: AXIS_LINE },
        { type: "category" as const, gridIndex: 1, data: times, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
      ]
    : [{ type: "category" as const, data: times, boundaryGap: false, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE }]

  const yAxis = showVol
    ? [
        { scale: true, splitNumber: 3, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
        { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      ]
    : [{ scale: true, splitNumber: 3, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE }]

  const series: NonNullable<EChartsOption["series"]> = [
    {
      name: "分时现价",
      type: "line",
      data: prices,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.8, color: mainColor },
      areaStyle: { color: isUp ? "rgba(246, 70, 93, 0.14)" : "rgba(31, 195, 125, 0.14)" },
    },
  ]
  if (showAvg) {
    series.push({
      name: "分时均价",
      type: "line",
      data: avgs,
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 1.3, color: "#f59e0b", type: "dashed" },
    })
  }
  if (showVol) {
    series.push({
      name: "成交量",
      type: "bar",
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: vols,
      itemStyle: {
        color: (params) => {
          const idx = params.dataIndex
          if (idx === 0) return mainColor
          return (prices[idx] ?? 0) >= (prices[idx - 1] ?? 0) ? RISE : FALL
        },
      },
    })
  }

  return { animation: false, tooltip: { trigger: "axis" }, grid, xAxis, yAxis, series }
}

export interface KlineBarInput {
  date: string
  open: number
  close: number
  low: number
  high: number
  volume: number | null
}

export function calcMA(dayCount: number, closes: number[]): Array<number | null> {
  return closes.map((_, idx) => {
    if (idx < dayCount - 1) return null
    let sum = 0
    for (let i = 0; i < dayCount; i++) {
      sum += closes[idx - i] ?? 0
    }
    return Number((sum / dayCount).toFixed(2))
  })
}

export function buildKlineOption(bars: KlineBarInput[]): EChartsOption {
  const dates = bars.map((b) => b.date.slice(5))
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high])
  const closes = bars.map((b) => b.close)
  const showVol = hasValues(bars.map((b) => b.volume))

  const grid = showVol
    ? [
        { left: 42, right: 12, top: 10, height: "58%" },
        { left: 42, right: 12, top: "74%", height: "20%" },
      ]
    : [{ left: 42, right: 12, top: 10, bottom: 24 }]

  const xAxis = showVol
    ? [
        { type: "category" as const, data: dates, axisLabel: { show: false }, axisLine: AXIS_LINE },
        { type: "category" as const, gridIndex: 1, data: dates, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE },
      ]
    : [{ type: "category" as const, data: dates, axisLabel: AXIS_LABEL, axisLine: AXIS_LINE }]

  const yAxis = showVol
    ? [
        { scale: true, splitNumber: 3, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE },
        { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
      ]
    : [{ scale: true, splitNumber: 3, axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE }]

  const series: NonNullable<EChartsOption["series"]> = [
    {
      name: "日K",
      type: "candlestick",
      data: ohlc,
      itemStyle: { color: RISE, color0: FALL, borderColor: RISE, borderColor0: FALL },
    },
    { name: "MA5", type: "line", data: calcMA(5, closes), smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: "#f59e0b" } },
    { name: "MA10", type: "line", data: calcMA(10, closes), smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: "#3b82f6" } },
    { name: "MA20", type: "line", data: calcMA(20, closes), smooth: true, showSymbol: false, lineStyle: { width: 1.2, color: "#a855f7" } },
  ]
  if (showVol) {
    series.push({
      name: "成交量",
      type: "bar",
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: bars.map((b) => b.volume),
      itemStyle: {
        color: (params) => {
          const b = bars[params.dataIndex]
          return b && b.close >= b.open ? RISE : FALL
        },
      },
    })
  }

  return { animation: false, tooltip: { trigger: "axis" }, grid, xAxis, yAxis, series }
}
