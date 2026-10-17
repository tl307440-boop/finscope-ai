/** 板块轮动图（RRG）的 ECharts 配置。坐标值全部来自后端，这里只决定画法。 */
import type { RotationSeries } from "../../../api/marketPulse"
import { cssVar, pctText } from "../industry/chartKit"

export const QUADRANT_COLORS: Record<string, string> = {
  领先: "#f6465d",
  转弱: "#fbbf24",
  落后: "#1fc37d",
  改善: "#60a5fa",
}

export const QUADRANT_HINTS: Record<string, string> = {
  领先: "相对强度高于基准且还在走强",
  转弱: "相对强度仍高，但动量已经回落",
  落后: "相对强度低于基准且仍在走弱",
  改善: "相对强度仍低，但动量开始回升",
}

/** 以 100 为中心的对称坐标范围，留一点边距，保证四个象限面积一致。 */
export function rotationBounds(series: RotationSeries[], tails: boolean): { x: number; y: number } {
  let x = 1
  let y = 1
  for (const item of series) {
    const points = tails ? item.tail : item.tail.slice(-1)
    for (const point of points) {
      x = Math.max(x, Math.abs(point.ratio - 100))
      y = Math.max(y, Math.abs(point.momentum - 100))
    }
  }
  return { x: Math.ceil(x * 1.15 * 10) / 10, y: Math.ceil(y * 1.15 * 10) / 10 }
}

export function quadrantColor(quadrant: string): string {
  return QUADRANT_COLORS[quadrant] ?? cssVar("--muted", "#8a9bb8")
}

export function buildRotationOption(series: RotationSeries[], tails: boolean, focus: string): Record<string, unknown> {
  const bounds = rotationBounds(series, tails)
  const text = cssVar("--text-strong", "#f1f5ff")
  const area = (quadrant: string, x: [number, number], y: [number, number]) => [
    {
      name: quadrant,
      xAxis: x[0],
      yAxis: y[0],
      itemStyle: { color: `${QUADRANT_COLORS[quadrant]}12` },
      label: {
        color: `${QUADRANT_COLORS[quadrant]}cc`,
        fontSize: 13,
        fontWeight: 600,
        position: x[0] > 99 ? (y[0] > 99 ? "insideTopRight" : "insideBottomRight") : y[0] > 99 ? "insideTopLeft" : "insideBottomLeft",
      },
    },
    { xAxis: x[1], yAxis: y[1] },
  ]
  const lowX = 100 - bounds.x
  const highX = 100 + bounds.x
  const lowY = 100 - bounds.y
  const highY = 100 + bounds.y
  const heads = series.map((item) => {
    const last = item.tail[item.tail.length - 1]!
    const dim = Boolean(focus) && focus !== item.code
    return {
      value: [last.ratio, last.momentum],
      code: item.code,
      name: item.name,
      quadrant: item.quadrant,
      changePct: item.changePct,
      date: last.date,
      itemStyle: { color: quadrantColor(item.quadrant), opacity: dim ? 0.25 : 1 },
      label: { show: !dim, color: text },
    }
  })
  const tailSeries = series
    .filter((item) => tails || focus === item.code)
    .map((item) => ({
      type: "line",
      name: item.name,
      data: item.tail.map((point) => [point.ratio, point.momentum]),
      smooth: 0.35,
      symbol: "circle",
      symbolSize: 4,
      silent: true,
      z: focus === item.code ? 4 : 2,
      lineStyle: {
        width: focus === item.code ? 2.4 : 1.2,
        color: quadrantColor(item.quadrant),
        opacity: focus && focus !== item.code ? 0.12 : 0.55,
      },
      itemStyle: { color: quadrantColor(item.quadrant), opacity: focus && focus !== item.code ? 0.12 : 0.6 },
    }))
  return {
    grid: { left: 56, right: 24, top: 24, bottom: 48 },
    tooltip: {
      trigger: "item",
      formatter: (info: { data?: { name?: string; code?: string; quadrant?: string; value?: number[]; changePct?: number | null; date?: string } }) => {
        const data = info.data
        if (!data?.code || !data.value) {
          return ""
        }
        return [
          `<b>${data.name}</b> <span style="opacity:.6">${data.code}</span>`,
          `象限：${data.quadrant}（${QUADRANT_HINTS[data.quadrant ?? ""] ?? ""}）`,
          `RS-Ratio ${data.value[0]!.toFixed(2)} · RS-Momentum ${data.value[1]!.toFixed(2)}`,
          `当日涨跌 ${pctText(data.changePct ?? null)} · 截至 ${data.date}`,
        ].join("<br/>")
      },
    },
    xAxis: {
      type: "value",
      name: "RS-Ratio 相对强度 →",
      nameLocation: "middle",
      nameGap: 28,
      min: lowX,
      max: highX,
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "RS-Momentum 相对动量 →",
      nameLocation: "middle",
      nameGap: 40,
      min: lowY,
      max: highY,
      splitLine: { show: false },
    },
    series: [
      ...tailSeries,
      {
        type: "scatter",
        name: "板块",
        z: 5,
        symbolSize: 11,
        data: heads,
        label: { position: "right", fontSize: 11, formatter: (info: { data?: { name?: string } }) => info.data?.name ?? "" },
        labelLayout: { hideOverlap: true },
        emphasis: { scale: 1.6, label: { show: true } },
        markArea: {
          silent: true,
          data: [
            area("领先", [100, highX], [100, highY]),
            area("转弱", [100, highX], [lowY, 100]),
            area("落后", [lowX, 100], [lowY, 100]),
            area("改善", [lowX, 100], [100, highY]),
          ],
        },
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: "rgba(148,163,184,0.45)", type: "dashed" },
          data: [{ xAxis: 100 }, { yAxis: 100 }],
        },
      },
    ],
  }
}
