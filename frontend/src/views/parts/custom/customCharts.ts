/**
 * 自定义数据分析的 ECharts 配置。数值全部来自后端：缺失的点保持 null（断线），不补 0、不插值。
 * 颜色刻意避开 A 股「红涨绿跌」：PCR / 估值水位不是涨跌，用蓝、青、紫区分。
 */
import type { Indicator, IndicatorPoint, PcrPoint } from "../../../api/customAnalysis"
import { cssVar } from "../industry/chartKit"

/** 默认只展示最近 1 年（约 250 个交易日），其余可用下方缩放条拖出来。 */
export const VISIBLE_DAYS = 250

export const PCR_FEAR_LINE = 1.2
export const PCR_GREED_LINE = 0.6

const BLUE = "#3b82f6"
const CYAN = "#22d3ee"
const VIOLET = "#a78bfa"
const AMBER = "#fbbf24"

export function zoomStart(total: number, visible = VISIBLE_DAYS): number {
  return total > visible ? Math.round((1 - visible / total) * 100) : 0
}

function dataZoom(total: number): Record<string, unknown>[] {
  const start = zoomStart(total)
  return [
    { type: "inside", start, end: 100 },
    { type: "slider", start, end: 100, height: 18, bottom: 8 },
  ]
}

function numberText(value: unknown, digits: number): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—"
}

function axisTooltip(digits: number): Record<string, unknown> {
  return {
    trigger: "axis",
    axisPointer: { type: "cross" },
    valueFormatter: (value: unknown) => numberText(value, digits),
  }
}

function baseGrid(rightAxis: boolean): Record<string, unknown> {
  return { left: 52, right: rightAxis ? 56 : 24, top: 44, bottom: 56 }
}

function line(
  name: string,
  data: (number | null)[],
  color: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    name,
    type: "line",
    data,
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 1.8, color },
    itemStyle: { color },
    ...extra,
  }
}

function thresholdLines(entries: { value: number; label: string }[]): Record<string, unknown> {
  const muted = cssVar("--muted", "#8a9bb8")
  return {
    silent: true,
    symbol: "none",
    lineStyle: { type: "dashed", color: muted, width: 1 },
    label: { color: muted, fontSize: 11, formatter: "{b}", position: "insideEndTop" },
    data: entries.map((entry) => ({ yAxis: entry.value, name: entry.label })),
  }
}

/** PCR 主图：成交量 PCR、10 日均线、持仓量 PCR（左轴）+ 跟踪指数收盘（右轴，可选）。 */
export function buildPcrOption(points: PcrPoint[], showIndex: boolean): Record<string, unknown> {
  const dates = points.map((point) => point.tradeDate)
  const hasIndex = showIndex && points.some((point) => point.indexClose !== null)
  const series: Record<string, unknown>[] = [
    line(
      "成交量 PCR",
      points.map((point) => point.volumePcr),
      BLUE,
      {
        markLine: thresholdLines([
          { value: PCR_GREED_LINE, label: `${PCR_GREED_LINE} 极度贪婪线` },
          { value: PCR_FEAR_LINE, label: `${PCR_FEAR_LINE} 极度恐慌线` },
        ]),
      },
    ),
    line(
      "10 日均线",
      points.map((point) => point.volumePcrMa10),
      CYAN,
      { lineStyle: { width: 1.4, color: CYAN } },
    ),
    line(
      "持仓量 PCR",
      points.map((point) => point.oiPcr),
      VIOLET,
      { lineStyle: { width: 1.4, color: VIOLET, type: "dashed" } },
    ),
  ]
  if (hasIndex) {
    series.push(
      line(
        "跟踪指数收盘",
        points.map((point) => point.indexClose),
        cssVar("--faint", "#64748b"),
        { yAxisIndex: 1, lineStyle: { width: 1, color: cssVar("--faint", "#64748b"), opacity: 0.8 } },
      ),
    )
  }
  const yAxes: Record<string, unknown>[] = [{ type: "value", scale: true, name: "PCR" }]
  if (hasIndex) {
    yAxes.push({ type: "value", scale: true, name: "指数", splitLine: { show: false } })
  }
  return {
    animation: false,
    legend: { top: 4, data: series.map((item) => item.name) },
    tooltip: axisTooltip(3),
    grid: baseGrid(hasIndex),
    xAxis: { type: "category", data: dates, boundaryGap: false },
    yAxis: yAxes,
    dataZoom: dataZoom(points.length),
    series,
  }
}

/** 5 年分位小图：分位 ≥ 90 偏恐慌，≤ 10 偏贪婪。 */
export function buildPcrPercentileOption(points: PcrPoint[]): Record<string, unknown> {
  return {
    animation: false,
    tooltip: axisTooltip(1),
    grid: { left: 52, right: 24, top: 28, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.tradeDate), boundaryGap: false },
    yAxis: { type: "value", min: 0, max: 100, name: "5 年分位" },
    dataZoom: dataZoom(points.length),
    series: [
      line(
        "成交量 PCR 5 年分位",
        points.map((point) => point.percentile5y),
        AMBER,
        {
          areaStyle: { color: "rgba(251,191,36,0.08)" },
          markLine: thresholdLines([
            { value: 10, label: "10 偏贪婪" },
            { value: 90, label: "90 偏恐慌" },
          ]),
        },
      ),
    ],
  }
}

function pointExtra(point: IndicatorPoint, key: string): number | null {
  const value = point.extras[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

/** 估值主图：ERP 画 μ±2σ 通道，中位数 PB 叠加「不剔除 ST」对照线，巴菲特指标只画本身。 */
export function buildIndicatorOption(indicator: Indicator): Record<string, unknown> {
  const points = indicator.points
  const series: Record<string, unknown>[] = [
    line(
      indicator.name,
      points.map((point) => point.value),
      BLUE,
    ),
  ]
  // 样本不足时通道整列都是 null：不画、也不进图例，免得图例里列着一条不存在的线。
  if (indicator.code === "erp" && points.some((point) => point.bandMean !== null)) {
    series.push(
      line(
        "5 年均值",
        points.map((point) => point.bandMean),
        CYAN,
        { lineStyle: { width: 1.2, color: CYAN, type: "dashed" } },
      ),
      line(
        "μ+2σ（机会极值）",
        points.map((point) => point.bandUpper),
        AMBER,
        { lineStyle: { width: 1, color: AMBER, type: "dotted" } },
      ),
      line(
        "μ−2σ（风险警戒）",
        points.map((point) => point.bandLower),
        VIOLET,
        { lineStyle: { width: 1, color: VIOLET, type: "dotted" } },
      ),
    )
  }
  if (indicator.code === "median_pb") {
    series.push(
      line(
        "不剔除 ST（对照）",
        points.map((point) => pointExtra(point, "median_without_st_filter")),
        CYAN,
        { lineStyle: { width: 1.2, color: CYAN, type: "dashed" } },
      ),
    )
  }
  return {
    animation: false,
    legend: { top: 4, data: series.map((item) => item.name) },
    tooltip: axisTooltip(indicator.code === "median_pb" ? 3 : 2),
    grid: baseGrid(false),
    xAxis: { type: "category", data: points.map((point) => point.tradeDate), boundaryGap: false },
    yAxis: { type: "value", scale: true, name: indicator.unit },
    dataZoom: dataZoom(points.length),
    series,
  }
}

/** 估值分位小图（巴菲特指标、中位数 PB）：> 80 偏热，< 20 偏冷。 */
export function buildIndicatorPercentileOption(indicator: Indicator): Record<string, unknown> {
  const points = indicator.points
  return {
    animation: false,
    tooltip: axisTooltip(1),
    grid: { left: 52, right: 24, top: 28, bottom: 48 },
    xAxis: { type: "category", data: points.map((point) => point.tradeDate), boundaryGap: false },
    yAxis: { type: "value", min: 0, max: 100, name: "5 年分位" },
    dataZoom: dataZoom(points.length),
    series: [
      line(
        `${indicator.name} 5 年分位`,
        points.map((point) => point.percentile5y),
        AMBER,
        {
          areaStyle: { color: "rgba(251,191,36,0.08)" },
          markLine: thresholdLines([
            { value: 20, label: "20 低估" },
            { value: 80, label: "80 偏热" },
          ]),
        },
      ),
    ],
  }
}

/** 有任何一个分位值才值得画分位图；样本不足时整列都是 null，只显示后端给的说明。 */
export function hasPercentiles(points: { percentile5y: number | null }[]): boolean {
  return points.some((point) => point.percentile5y !== null)
}
