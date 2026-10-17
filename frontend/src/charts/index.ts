// 规格书 §技术选型：图表统一使用 Apache ECharts。
// 这里按需注册用到的图表与组件，避免整包引入拖慢首屏。
import {
  BarChart,
  CandlestickChart,
  HeatmapChart,
  LineChart,
  PieChart,
  ScatterChart,
  TreemapChart,
} from "echarts/charts"
import {
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
  TransformComponent,
  VisualMapComponent,
} from "echarts/components"
import { LabelLayout, UniversalTransition } from "echarts/features"
import { CanvasRenderer } from "echarts/renderers"
import { registerTheme, use } from "echarts/core"
import type { App } from "vue"
import VChart, { THEME_KEY } from "vue-echarts"

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  CandlestickChart,
  HeatmapChart,
  PieChart,
  ScatterChart,
  TreemapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  DatasetComponent,
  TransformComponent,
  VisualMapComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  GraphicComponent,
  ToolboxComponent,
  LabelLayout,
  UniversalTransition,
])

export const CHART_COMPONENT_NAME = "VChart"
export const CHART_THEME = "finscope"

const AXIS_TEXT = "#8a9bb8"
const AXIS_LINE = "rgba(96,140,220,0.28)"
const SPLIT_LINE = "rgba(96,140,220,0.10)"

const axisDefaults = {
  axisLine: { lineStyle: { color: AXIS_LINE } },
  axisTick: { show: false },
  axisLabel: { color: AXIS_TEXT, fontSize: 11 },
  splitLine: { lineStyle: { color: SPLIT_LINE } },
  nameTextStyle: { color: AXIS_TEXT, fontSize: 11 },
}

// 视图里显式写的样式优先；主题只兜底未指定的部分（尤其是默认白底的 tooltip）。
registerTheme(CHART_THEME, {
  color: ["#3b82f6", "#22d3ee", "#a78bfa", "#fbbf24", "#34d399", "#fb923c", "#f472b6", "#94a3b8"],
  backgroundColor: "transparent",
  textStyle: { fontFamily: "Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif" },
  title: { textStyle: { color: "#dbe6f7" }, subtextStyle: { color: AXIS_TEXT } },
  legend: { textStyle: { color: AXIS_TEXT }, icon: "roundRect", itemWidth: 10, itemHeight: 4 },
  tooltip: {
    backgroundColor: "rgba(10,20,38,0.94)",
    borderColor: "rgba(96,165,250,0.35)",
    borderWidth: 1,
    padding: [8, 12],
    textStyle: { color: "#dbe6f7", fontSize: 12 },
    extraCssText: "box-shadow:0 12px 32px -12px rgba(0,0,0,.7);border-radius:8px;backdrop-filter:blur(6px);",
    axisPointer: {
      lineStyle: { color: "rgba(96,165,250,0.55)", type: "dashed" },
      crossStyle: { color: "rgba(96,165,250,0.55)" },
      label: { backgroundColor: "#1e3a6b" },
    },
  },
  categoryAxis: axisDefaults,
  valueAxis: { ...axisDefaults, axisLine: { show: false } },
  timeAxis: axisDefaults,
  logAxis: axisDefaults,
  line: { symbol: "none", lineStyle: { width: 1.8 } },
  candlestick: {
    itemStyle: { color: "#f6465d", color0: "#1fc37d", borderColor: "#f6465d", borderColor0: "#1fc37d" },
  },
  dataZoom: {
    borderColor: "rgba(96,140,220,0.2)",
    fillerColor: "rgba(59,130,246,0.14)",
    handleStyle: { color: "#3b82f6", borderColor: "#60a5fa" },
    textStyle: { color: AXIS_TEXT },
    dataBackground: { lineStyle: { color: AXIS_LINE }, areaStyle: { color: "rgba(59,130,246,0.08)" } },
  },
  visualMap: { textStyle: { color: AXIS_TEXT } },
})

export function installCharts(app: App): void {
  app.component(CHART_COMPONENT_NAME, VChart)
  app.provide(THEME_KEY, CHART_THEME)
}

export { VChart }
