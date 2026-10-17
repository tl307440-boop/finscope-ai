/**
 * 驾驶舱图表的 ECharts 配置。纯函数：输入后端已算好的序列，只负责摆放与配色，不产生新的财务数字。
 */
import type { EChartsOption } from "echarts"
import type { AmountPoint, Bar, Bucket, IndexPeriod, MinutePoint, SeriesPoint } from "../../../api/dashboard"
import { cssVar } from "../industry/chartKit"

/** 首屏展示的 K 线根数；后端多给的部分只用于均线预热。 */
export const VISIBLE_BARS: Record<Exclude<IndexPeriod, "min">, number> = { day: 120, week: 100, month: 80 }

const MA_COLORS = ["#fbbf24", "#60a5fa", "#c084fc"]
const SPLIT = { lineStyle: { color: "rgba(96,140,220,0.08)" } }

/** 坐标轴上的成交额：元 → 亿 / 万亿。 */
export function axisAmount(value: number): string {
  const size = Math.abs(value)
  if (size >= 1e12) {
    return `${(value / 1e12).toFixed(1)}万亿`
  }
  return `${Math.round(value / 1e8)}亿`
}

export function klineOption(bars: readonly Bar[], period: Exclude<IndexPeriod, "min">): EChartsOption {
  const rows = bars.slice(-VISIBLE_BARS[period])
  const rise = cssVar("--rise", "#f6465d")
  const fall = cssVar("--fall", "#1fc37d")
  const days = rows.map((row) => row.day)
  const extremeLabel = { color: cssVar("--text", "#e6edf8"), fontSize: 11 }
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: { top: 0, left: 50, icon: "circle", itemWidth: 8, data: ["MA5", "MA10", "MA20"] },
    grid: [
      { left: 54, right: 16, top: 28, height: "60%" },
      { left: 54, right: 16, top: "77%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: days, boundaryGap: true, axisLabel: { hideOverlap: true } },
      { type: "category", gridIndex: 1, data: days, axisLabel: { show: false } },
    ],
    yAxis: [
      { type: "value", scale: true, splitLine: SPLIT },
      { type: "value", gridIndex: 1, splitNumber: 2, splitLine: SPLIT, axisLabel: { formatter: axisAmount } },
    ],
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1] }],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: rows.map((row) => [row.open, row.close, row.low, row.high]),
        itemStyle: { color: rise, color0: fall, borderColor: rise, borderColor0: fall },
        markPoint: {
          symbol: "pin",
          symbolSize: 1,
          label: { ...extremeLabel, formatter: (item) => `${item.name} ${Number(item.value).toFixed(2)}` },
          data: [
            { name: "高", type: "max", valueDim: "highest", label: { position: "top" } },
            { name: "低", type: "min", valueDim: "lowest", label: { position: "bottom" } },
          ],
        },
      },
      ...(["ma5", "ma10", "ma20"] as const).map((key, index) => ({
        name: `MA${key.slice(2)}`,
        type: "line" as const,
        data: rows.map((row) => row[key]),
        showSymbol: false,
        lineStyle: { width: 1.1, color: MA_COLORS[index] },
        itemStyle: { color: MA_COLORS[index] },
      })),
      {
        name: "成交额",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: rows.map((row) => ({
          value: row.amount,
          itemStyle: { color: row.close >= row.open ? "rgba(246,70,93,0.7)" : "rgba(31,195,125,0.7)" },
        })),
        tooltip: { valueFormatter: (value) => (typeof value === "number" ? axisAmount(value) : "—") },
      },
    ],
  }
}

export function minuteOption(points: readonly MinutePoint[], prevClose: number | null): EChartsOption {
  const rise = cssVar("--rise", "#f6465d")
  const fall = cssVar("--fall", "#1fc37d")
  const clocks = points.map((point) => point.clock)
  const prices = points.map((point) => point.price)
  const bound = (pick: "min" | "max") => (extent: { min: number; max: number }) => {
    if (prevClose === null) {
      return extent[pick]
    }
    return pick === "min" ? Math.min(extent.min, prevClose * 0.995) : Math.max(extent.max, prevClose * 1.005)
  }
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 54, right: 58, top: 16, height: "62%" },
      { left: 54, right: 58, top: "79%", height: "15%" },
    ],
    xAxis: [
      { type: "category", data: clocks, boundaryGap: false, axisLabel: { interval: 29 } },
      { type: "category", gridIndex: 1, data: clocks, axisLabel: { show: false } },
    ],
    yAxis: [
      { type: "value", scale: true, min: bound("min"), max: bound("max"), splitLine: SPLIT },
      {
        type: "value",
        position: "right",
        min: bound("min"),
        max: bound("max"),
        splitLine: { show: false },
        axisLabel: {
          formatter: (value: number) => (prevClose ? `${((value / prevClose - 1) * 100).toFixed(2)}%` : ""),
        },
      },
      { type: "value", gridIndex: 1, splitNumber: 2, splitLine: SPLIT, axisLabel: { formatter: axisAmount } },
    ],
    series: [
      {
        name: "价格",
        type: "line",
        data: prices,
        showSymbol: false,
        lineStyle: { width: 1.6, color: "#60a5fa" },
        itemStyle: { color: "#60a5fa" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(96,165,250,0.25)" },
              { offset: 1, color: "rgba(96,165,250,0)" },
            ],
          },
        },
        markLine:
          prevClose === null
            ? undefined
            : {
                symbol: "none",
                silent: true,
                data: [{ yAxis: prevClose }],
                lineStyle: { color: "#94a3b8", type: "dashed" },
                label: { formatter: `昨收 ${prevClose.toFixed(2)}`, color: "#94a3b8", position: "insideStartTop" },
              },
      },
      { type: "line", yAxisIndex: 1, data: prices, showSymbol: false, lineStyle: { opacity: 0 }, tooltip: { show: false } },
      {
        name: "成交额",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: points.map((point, index) => ({
          value: point.amount,
          itemStyle: { color: index > 0 && point.price < (points[index - 1]?.price ?? point.price) ? fall : rise },
        })),
        tooltip: { valueFormatter: (value) => (typeof value === "number" ? axisAmount(value) : "—") },
      },
    ],
  }
}

export function distributionOption(buckets: readonly Bucket[]): EChartsOption {
  const rise = cssVar("--rise", "#f6465d")
  const fall = cssVar("--fall", "#1fc37d")
  return {
    animation: false,
    title: { text: "全市场涨跌分布（家数）", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 44, right: 12, top: 40, bottom: 52 },
    xAxis: { type: "category", data: buckets.map((item) => item.label), axisLabel: { rotate: 45, fontSize: 10, interval: 0 } },
    yAxis: { type: "value", splitLine: SPLIT, minInterval: 1 },
    series: [
      {
        type: "bar",
        data: buckets.map((item) => ({
          value: item.count,
          itemStyle: { color: item.bucket > 0 ? rise : item.bucket < 0 ? fall : "#64748b" },
        })),
        label: { show: true, position: "top", fontSize: 10, color: cssVar("--muted", "#94a4be") },
      },
    ],
  }
}

export function amountOption(points: readonly AmountPoint[]): EChartsOption {
  const rows = points.slice(-40)
  return {
    animation: false,
    title: { text: "两市成交额与前 20 日均额", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis", valueFormatter: (value) => (typeof value === "number" ? axisAmount(value) : "—") },
    legend: { right: 8, top: 24 },
    grid: { left: 60, right: 12, top: 52, bottom: 28 },
    xAxis: { type: "category", data: rows.map((row) => row.day.slice(5)) },
    yAxis: { type: "value", scale: true, splitLine: SPLIT, axisLabel: { formatter: axisAmount } },
    series: [
      { name: "成交额", type: "bar", data: rows.map((row) => row.amount), itemStyle: { color: "#3b82f6" }, barMaxWidth: 14 },
      { name: "前20日均额", type: "line", data: rows.map((row) => row.ma20), showSymbol: false, lineStyle: { color: "#fbbf24" } },
    ],
  }
}

function pairs(points: readonly SeriesPoint[]): [string, number][] {
  return points.map((point) => [point.day, point.value])
}

export function netLiquidityOption(points: readonly SeriesPoint[]): EChartsOption {
  const cyan = cssVar("--cyan", "#22d3ee")
  return {
    animation: false,
    title: { text: "美联储净流动性（十亿美元，周）", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" },
    grid: { left: 56, right: 14, top: 40, bottom: 28 },
    xAxis: { type: "time" },
    yAxis: { type: "value", scale: true, splitLine: SPLIT },
    series: [
      {
        name: "净流动性",
        type: "line",
        data: pairs(points),
        showSymbol: false,
        lineStyle: { color: cyan },
        itemStyle: { color: cyan },
        areaStyle: { opacity: 0.12, color: cyan },
      },
    ],
  }
}

export function dollarYieldOption(dollar: readonly SeriesPoint[], realYield: readonly SeriesPoint[]): EChartsOption {
  return {
    animation: false,
    title: { text: "美元指数（广义）与 10Y 实际利率", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" },
    legend: { right: 8, top: 24 },
    grid: { left: 50, right: 50, top: 52, bottom: 28 },
    xAxis: { type: "time" },
    yAxis: [
      { type: "value", scale: true, splitLine: SPLIT },
      { type: "value", scale: true, splitLine: { show: false }, axisLabel: { formatter: "{value}%" } },
    ],
    series: [
      { name: "美元指数", type: "line", data: pairs(dollar), showSymbol: false, lineStyle: { color: "#a78bfa" }, itemStyle: { color: "#a78bfa" } },
      {
        name: "10Y实际利率",
        type: "line",
        yAxisIndex: 1,
        data: pairs(realYield),
        showSymbol: false,
        lineStyle: { color: "#fb923c" },
        itemStyle: { color: "#fb923c" },
      },
    ],
  }
}

export function chinaRatesOption(shibor: readonly SeriesPoint[], spread: readonly SeriesPoint[]): EChartsOption {
  return {
    animation: false,
    title: { text: "SHIBOR 隔夜与中美 10Y 利差", textStyle: { fontSize: 13 } },
    tooltip: { trigger: "axis" },
    legend: { right: 8, top: 24 },
    grid: { left: 50, right: 56, top: 52, bottom: 28 },
    xAxis: { type: "time" },
    yAxis: [
      { type: "value", scale: true, splitLine: SPLIT, axisLabel: { formatter: "{value}%" } },
      { type: "value", scale: true, splitLine: { show: false }, axisLabel: { formatter: "{value}bp" } },
    ],
    series: [
      { name: "SHIBOR隔夜", type: "line", data: pairs(shibor), showSymbol: false, lineStyle: { color: "#34d399" }, itemStyle: { color: "#34d399" } },
      {
        name: "中美10Y利差",
        type: "line",
        yAxisIndex: 1,
        data: pairs(spread),
        showSymbol: false,
        lineStyle: { color: "#f472b6" },
        itemStyle: { color: "#f472b6" },
      },
    ],
  }
}

/** 热度指数徽标底色：越热越红。 */
export function heatBadge(index: number): string {
  return index >= 80 ? "#dc2626" : index >= 65 ? "#ea580c" : index >= 50 ? "#d97706" : "#475569"
}
