// 规格书 §技术选型：图表统一使用 Apache ECharts。
// 这里按需注册用到的图表与组件，避免整包引入拖慢首屏。
import { BarChart, CandlestickChart, HeatmapChart, LineChart, PieChart, ScatterChart } from "echarts/charts"
import {
  DataZoomComponent,
  DatasetComponent,
  GraphicComponent,
  GridComponent,
  LegendComponent,
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
import { use } from "echarts/core"
import type { App } from "vue"
import VChart from "vue-echarts"

use([
  CanvasRenderer,
  LineChart,
  BarChart,
  CandlestickChart,
  HeatmapChart,
  PieChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  TitleComponent,
  DataZoomComponent,
  DatasetComponent,
  TransformComponent,
  VisualMapComponent,
  MarkLineComponent,
  MarkPointComponent,
  GraphicComponent,
  ToolboxComponent,
  LabelLayout,
  UniversalTransition,
])

export const CHART_COMPONENT_NAME = "VChart"

export function installCharts(app: App): void {
  app.component(CHART_COMPONENT_NAME, VChart)
}

export { VChart }
