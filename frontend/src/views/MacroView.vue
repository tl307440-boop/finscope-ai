<script setup lang="ts">
/**
 * 全球宏观分析（/macro/us，效果图 2）。
 *
 * 真实接口（与 backend/app/api/v1/macro.py、schemas/macro.py、catalog/macro_series.py 一致；
 * 原有取数逻辑保持不变，本页只重塑版式）：
 *  - GET /api/v1/macro/series
 *      data = { provider_code, availability, notice, series[] }
 *      series[] = { series_code, name_cn, name_en, category, frequency, base_unit, seasonal_adjustment }
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value, reason, evidence[] },
 *               notice, version }
 *
 * 不编造数据（AGENTS.md 硬约束 + docs/UI-设计规范.md §八）：
 *  - 指标带只显示这 8 条序列**真实存在的观测值**与相邻两期差；序列目录里没有的指标
 *    （效果图里的 ISM 制造业 PMI）不凭空补位，而是在「数据说明」里如实列出缺失原因。
 *  - 效果图上的「GDP 实际增速 / CPI 同比」是同比口径，后端没有提供该计算，前端也**不做**同比
 *    换算（前端不算财务/宏观数字）。这里显示序列的原始口径数值，并在数据说明里写明口径差异。
 *  - 宏观象限的「当前位置」只用后端规则分数（macro-rules-v1）定位；分数取不到时画网格与
 *    四象限文字，但不落点、不编造坐标。
 *  - AI 宏观解读的正文来自 assessments 的 regime_code / reason / evidence 原样转发，不是模型生成文案。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"

interface CatalogItem {
  code: string
  name: string
  nameEn: string
  category: string
  frequency: string
  unit: string
  adjustment: string
}

interface Observation {
  date: string
  value: number
  raw: string
  realtimeStart: string
}

interface DimensionScore {
  code: string
  value: number | null
  reason: string | null
  evidence: string[]
}

interface Assessment {
  pitDate: string
  regimeCode: string
  growth: DimensionScore | null
  inflation: DimensionScore | null
  liquidity: DimensionScore | null
  notice: string
  version: string
}

/** 指标带顺序与效果图一致；name 用后端序列名，label 用后端 name_cn，不自行改名。 */
const TILE_CODES = [
  "GDP",
  "CPIAUCSL",
  "PCEPILFE",
  "UNRATE",
  "FEDFUNDS",
  "DGS10",
  "T10Y2Y",
  "DGS2",
] as const

/** 收益率曲线横轴口径：效果图 3M…30Y，这里只按目录里真实存在的序列落点。 */
const CURVE_TENORS: readonly { tenor: string; code: string }[] = [
  { tenor: "3M", code: "DTB3" },
  { tenor: "6M", code: "DTB6" },
  { tenor: "1Y", code: "DGS1" },
  { tenor: "2Y", code: "DGS2" },
  { tenor: "3Y", code: "DGS3" },
  { tenor: "5Y", code: "DGS5" },
  { tenor: "7Y", code: "DGS7" },
  { tenor: "10Y", code: "DGS10" },
  { tenor: "20Y", code: "DGS20" },
  { tenor: "30Y", code: "DGS30" },
]

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

/** 数据说明弹窗里的缺失清单：这些指标**不在**本项目序列目录内，不画空壳卡片。 */
const MISSING_NOTES: readonly string[] = [
  "ISM 制造业 PMI：后端 FRED 序列目录中不存在该序列代码，需先在 catalog/macro_series.py 登记并同步后才能展示。",
  "GDP 实际增速 / CPI 同比 / 核心 PCE 同比：目录提供的是 GDP 现价水平（Billions of Dollars）与两个价格指数（Index 2017=100），同比口径由后端规则负责，前端不做换算。",
]

/** 情景框架：只是效果图里的三条框架位，概率与方向结论都需要后端输入，故不填。 */
const SCENARIOS = [
  {
    key: "base",
    label: "基准",
    tone: "accent" as const,
    title: "沿当前 regime_code 延续",
    basis: "以后端规则分数在当前观察日的取值作为基准情形，不做外推。",
  },
  {
    key: "optimistic",
    label: "乐观",
    tone: "ok" as const,
    title: "增长维度上行 + 流动性转松",
    basis: "需要后续观测值把增长分数推到 ≥50、流动性分数推到 ≥70 才会出现，届时以新分数为准。",
  },
  {
    key: "pessimistic",
    label: "悲观",
    tone: "danger" as const,
    title: "增长维度下行 + 流动性转紧",
    basis: "需要后续观测值把增长分数压到 <50、流动性分数压到 ≤30 才会出现，届时以新分数为准。",
  },
]

const phase = ref<Phase>("loading")
const failure = ref("")
const providerCode = ref("")
const availability = ref("")
const catalogNotice = ref("")
const catalog = ref<CatalogItem[]>([])
const observationsByCode = ref<Record<string, Observation[]>>({})
const emptyMessages = ref<Record<string, string>>({})
const assessmentFailed = ref(false)
const assessment = ref<Assessment | null>(null)
const helpOpen = ref(false)
const observationsBusy = ref(false)

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`

/** 观察日：一次请求同时约束指标带、近端序列与规则判断，避免两块数据对不上时点。 */
const queryAsOf = ref(browserToday)
const appliedAsOf = ref(browserToday)

const tileRows = computed(() =>
  TILE_CODES.map((code) => {
    const item = catalog.value.find((entry) => entry.code === code) ?? null
    const rows = observationsByCode.value[code] ?? []
    const latest = rows.length ? rows[rows.length - 1] ?? null : null
    const previous = rows.length > 1 ? rows[rows.length - 2] ?? null : null
    const values = rows.map((row) => row.value)
    return {
      code,
      item,
      rows,
      latest,
      delta: latest && previous ? latest.value - previous.value : null,
      spark: values.length > 1 ? values : [],
    }
  }),
)

const missingCodes = computed<string[]>(() =>
  TILE_CODES.filter((code) => !catalog.value.some((item) => item.code === code)),
)

const availabilityLabel = computed<string>(() => {
  if (availability.value === "configured") {
    return "外部数据源已配置"
  }
  if (availability.value === "unavailable") {
    return "外部数据源未配置（序列定义仍在，观测值需同步或导入）"
  }
  return availability.value || "—"
})

const dataNotices = computed<string[]>(() => {
  const rows: string[] = []
  if (catalogNotice.value) {
    rows.push(`目录说明：${catalogNotice.value}`)
  }
  if (availability.value === "unavailable") {
    rows.push("FRED_API_KEY 未配置：序列定义可见，但不返回观测值；下方指标一律显示真实空态而不是替代数字。")
  }
  for (const code of missingCodes.value) {
    rows.push(`序列目录中没有 ${code}，该指标本次不展示。`)
  }
  for (const [code, message] of Object.entries(emptyMessages.value)) {
    if (message) {
      rows.push(`${code}：${message}`)
    }
  }
  return rows
})

const latestObservationDate = computed<string>(() => {
  const dates = Object.values(observationsByCode.value)
    .flat()
    .map((row) => row.date)
  return dates.length ? dates.reduce((left, right) => (left > right ? left : right)) : ""
})

const stale = computed<boolean>(() => phase.value === "ready" && !latestObservationDate.value)

/** 效果图右上角「数据说明」：只解释真实取数情况，不做假按钮。 */
const sourceNote = computed<string>(() =>
  [
    "指标带取序列原始口径的观测值，较上期为相邻两个已公布观测值之差（频率随序列，日频即日变动、月频即月变动）。",
    ...MISSING_NOTES,
  ].join(" "),
)

function formatValue(value: number | null, unit: string): string {
  if (value === null) {
    return "—"
  }
  if (unit === "Percent") {
    return value.toFixed(2)
  }
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString("zh-CN", { maximumFractionDigits: 1 })
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
}

function tilePeriod(row: { latest: Observation | null; item: CatalogItem | null }): string {
  if (!row.latest) {
    return row.item ? `${row.item.frequency} · 暂无可见观测值` : "序列目录中不存在"
  }
  return `${row.latest.date} · ${row.item?.frequency ?? "—"}`
}

/** 联邦基金利率：用 step 反映政策利率的阶梯特性。 */
const fedOption = computed<EChartsCoreOption | null>(() => {
  const rows = observationsByCode.value["FEDFUNDS"] ?? []
  if (rows.length < 2) {
    return null
  }
  const points = rows.slice(-48)
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
      valueFormatter: (value: unknown): string => `${String(value)} %`,
    },
    xAxis: {
      type: "category",
      data: points.map((row) => row.date),
      axisLine: { lineStyle: { color: "var(--line)" } },
      axisLabel: { color: "var(--muted)", fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: "var(--muted)", fontSize: 11 },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        name: "FEDFUNDS（%）",
        type: "line",
        step: "end",
        symbol: "none",
        data: points.map((row) => row.value),
        lineStyle: { color: "var(--accent)", width: 2 },
        itemStyle: { color: "var(--accent)" },
        areaStyle: { color: "var(--accent)", opacity: 0.12 },
      },
    ],
  }
})

/** 收益率曲线：只取目录里真实存在的期限，缺的期限不补点。 */
const curvePoints = computed<{ tenor: string; code: string; value: number; date: string }[]>(() => {
  const rows: { tenor: string; code: string; value: number; date: string }[] = []
  for (const item of CURVE_TENORS) {
    if (!catalog.value.some((entry) => entry.code === item.code)) {
      continue
    }
    const points = observationsByCode.value[item.code] ?? []
    const latest = points.length ? points[points.length - 1] : null
    if (!latest) {
      continue
    }
    rows.push({ tenor: item.tenor, code: item.code, value: latest.value, date: latest.date })
  }
  return rows
})

const curveMissing = computed<string[]>(() =>
  CURVE_TENORS.filter((item) => !catalog.value.some((entry) => entry.code === item.code)).map(
    (item) => item.tenor,
  ),
)

const curveOption = computed<EChartsCoreOption | null>(() => {
  const rows = curvePoints.value
  if (rows.length < 2) {
    return null
  }
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
      valueFormatter: (value: unknown): string => `${String(value)} %`,
    },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.tenor),
      axisLine: { lineStyle: { color: "var(--line)" } },
      axisLabel: { color: "var(--muted)", fontSize: 11 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: "var(--muted)", fontSize: 11 },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        name: "国债收益率（%）",
        type: "line",
        smooth: true,
        symbolSize: 7,
        data: rows.map((row) => row.value),
        lineStyle: { color: "var(--accent)", width: 2 },
        itemStyle: { color: "var(--accent)" },
        label: {
          show: true,
          position: "top",
          color: "var(--muted)",
          fontSize: 11,
          formatter: (params: { value?: unknown }): string =>
            typeof params.value === "number" ? params.value.toFixed(2) : "",
        },
      },
    ],
  }
})

/** 通胀走势：两条指数序列 + 2% 目标虚线（口径差异见卡片下方说明）。 */
const inflationOption = computed<EChartsCoreOption | null>(() => {
  const cpi = (observationsByCode.value["CPIAUCSL"] ?? []).slice(-120)
  const pce = (observationsByCode.value["PCEPILFE"] ?? []).slice(-120)
  if (cpi.length < 2 && pce.length < 2) {
    return null
  }
  return {
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: {
      top: 0,
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: "var(--muted)", fontSize: 11 },
      data: ["CPI 指数（2017=100）", "核心 PCE 指数（2017=100）", "目标水平 2%"],
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
    },
    xAxis: {
      type: "category",
      data: (cpi.length >= pce.length ? cpi : pce).map((row) => row.date),
      axisLine: { lineStyle: { color: "var(--line)" } },
      axisLabel: { color: "var(--muted)", fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: "var(--muted)", fontSize: 11 },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        name: "CPI 指数（2017=100）",
        type: "line",
        symbol: "none",
        data: cpi.map((row) => row.value),
        lineStyle: { color: "var(--accent)", width: 2 },
        itemStyle: { color: "var(--accent)" },
      },
      {
        name: "核心 PCE 指数（2017=100）",
        type: "line",
        symbol: "none",
        data: pce.map((row) => row.value),
        lineStyle: { color: "var(--warn)", width: 2 },
        itemStyle: { color: "var(--warn)" },
      },
      {
        name: "目标水平 2%",
        type: "line",
        symbol: "none",
        silent: true,
        data: [],
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "var(--muted)", type: "dashed" },
          label: { color: "var(--muted)", fontSize: 10, formatter: "目标水平 2%" },
          data: [{ yAxis: 2 }],
        },
      },
    ],
  }
})

/** 象限图的 0–100 网格：四角文字与轴线用 graphic，不依赖未注册的 markArea。 */
const QUADRANT_GRAPHIC: readonly Record<string, unknown>[] = [
  {
    type: "text",
    left: 24,
    top: 12,
    style: { text: "滞胀风险\n（高通胀·低增长）", fill: "var(--muted)", fontSize: 11, lineHeight: 16 },
  },
  {
    type: "text",
    right: 24,
    top: 12,
    style: { text: "过热风险\n（高增长·高通胀）", fill: "var(--muted)", fontSize: 11, lineHeight: 16, align: "right" },
  },
  {
    type: "text",
    left: 24,
    bottom: 40,
    style: { text: "衰退风险\n（低增长·低通胀）", fill: "var(--muted)", fontSize: 11, lineHeight: 16 },
  },
  {
    type: "text",
    right: 24,
    bottom: 40,
    style: { text: "复苏阶段\n（低通胀·高增长）", fill: "var(--muted)", fontSize: 11, lineHeight: 16, align: "right" },
  },
  {
    type: "text",
    left: "center",
    top: 8,
    style: { text: "经济较热（增长动能强）", fill: "var(--muted)", fontSize: 11, align: "center" },
  },
  {
    type: "text",
    left: "center",
    bottom: 20,
    style: { text: "经济较冷（增长动能弱）", fill: "var(--muted)", fontSize: 11, align: "center" },
  },
]

const quadrantPoint = computed<{ x: number; y: number } | null>(() => {
  const growth = assessment.value?.growth?.value
  const liquidity = assessment.value?.liquidity?.value
  if (growth === null || growth === undefined || liquidity === null || liquidity === undefined) {
    return null
  }
  return { x: 30 + (liquidity / 100) * 40, y: 30 + (growth / 100) * 40 }
})

const quadrantOption = computed<EChartsCoreOption>(() => {
  const point = quadrantPoint.value
  return {
    grid: { left: 32, right: 32, top: 36, bottom: 36 },
    graphic: QUADRANT_GRAPHIC,
    tooltip: {
      trigger: "item",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
      formatter: (params: unknown): string => {
        const record = asRecord(params)
        const value = asListValue(record?.value)
        if (value.length < 2) {
          return ""
        }
        return `横轴＝流动性分数 ${value[0].toFixed(1)} · 纵轴＝增长分数 ${value[1].toFixed(1)}`
      },
    },
    xAxis: {
      type: "value",
      min: 0,
      max: 100,
      name: "货币收紧  货币宽松",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: "var(--muted)", fontSize: 11 },
      axisLine: {
        onZero: false,
        lineStyle: { color: "var(--line)" },
        symbol: ["none", "arrow"],
        symbolSize: [0, 8],
      },
      axisLabel: { show: false },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      name: "经济冷热",
      nameLocation: "middle",
      nameGap: 28,
      nameTextStyle: { color: "var(--muted)", fontSize: 11 },
      axisLine: {
        onZero: false,
        lineStyle: { color: "var(--line)" },
        symbol: ["none", "arrow"],
        symbolSize: [0, 8],
      },
      axisLabel: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        type: "scatter",
        symbolSize: point ? 16 : 0,
        data: point
          ? [
              {
                name: "当前位置",
                value: [point.x, point.y],
                label: {
                  show: true,
                  position: "top",
                  distance: 8,
                  backgroundColor: "var(--panel-soft)",
                  borderColor: "var(--accent)",
                  borderWidth: 1,
                  borderRadius: 6,
                  padding: [4, 8],
                  color: "var(--text)",
                  fontSize: 11,
                  formatter: "当前位置",
                },
              },
            ]
          : [],
        itemStyle: {
          color: "var(--rise)",
          borderColor: "var(--panel)",
          borderWidth: 2,
        },
      },
    ],
  }
})

function asListValue(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const parsed = asNumber(item)
    return parsed === null ? [] : [parsed]
  })
}

const dimensionRows = computed<DimensionScore[]>(() => {
  const current = assessment.value
  if (!current) {
    return []
  }
  return [current.growth, current.inflation, current.liquidity].flatMap((item) => (item ? [item] : []))
})

const allInsufficient = computed<boolean>(
  () => dimensionRows.value.length > 0 && dimensionRows.value.every((item) => item.value === null),
)

const regimeLabel = computed<string>(() => {
  const code = assessment.value?.regimeCode ?? ""
  if (!code) {
    return "—"
  }
  if (code === "insufficient_data") {
    return "数据不足，规则无法给出组合状态"
  }
  const words: Record<string, string> = { up: "上行", down: "下行", easy: "偏松", tight: "偏紧" }
  const [growth, inflation, liquidity] = code.split("-")
  const parts: string[] = []
  if (growth) {
    parts.push(`增长${words[growth] ?? growth}`)
  }
  if (inflation) {
    parts.push(`通胀${words[inflation] ?? inflation}`)
  }
  if (liquidity) {
    parts.push(`流动性${words[liquidity] ?? liquidity}`)
  }
  return parts.join(" · ")
})

const regimePill = computed<{ text: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" }>(() => {
  const current = assessment.value
  if (!current) {
    return { text: "未取到评估结果", tone: "muted" }
  }
  if (current.regimeCode === "insufficient_data" || allInsufficient.value) {
    return { text: "数据不足", tone: "muted" }
  }
  if (!current.regimeCode) {
    return { text: "—", tone: "muted" }
  }
  return { text: current.regimeCode, tone: "accent" }
})

/** 维度分数：70=上行/偏松、30=下行/偏紧、50=持平，value 缺省＝数据不足。 */
function scoreView(score: DimensionScore | null): { label: string; tone: string } {
  if (!score || score.value === null) {
    return { label: "数据不足", tone: "var(--muted)" }
  }
  const liquidity = score.code === "liquidity"
  if (score.value === 70) {
    return { label: liquidity ? "偏松（70，利率下行）" : "上行（70）", tone: "var(--rise)" }
  }
  if (score.value === 30) {
    return { label: liquidity ? "偏紧（30，利率上行）" : "下行（30）", tone: "var(--fall)" }
  }
  if (score.value === 50) {
    return { label: "持平（50）", tone: "var(--text)" }
  }
  return { label: `分数 ${score.value}`, tone: "var(--text)" }
}

function dimensionLabel(code: string): string {
  if (code === "growth") {
    return "增长"
  }
  if (code === "inflation") {
    return "通胀"
  }
  if (code === "liquidity") {
    return "流动性"
  }
  return code
}

function reasonView(reason: string | null): string {
  if (!reason) {
    return ""
  }
  if (reason === "insufficient_data") {
    return "不足两个已公布观测值，规则无法给出方向"
  }
  return reason
}

/** 依据行来自后端 _evidence()："<序列代码> <观测日> <数值> 公布日 <公布日>"。 */
function evidenceParts(line: string): { headline: string; published: string } {
  const marker = " 公布日 "
  const index = line.indexOf(marker)
  if (index < 0) {
    return { headline: line, published: "" }
  }
  return { headline: line.slice(0, index), published: line.slice(index + marker.length) }
}

function parseCatalog(value: unknown): CatalogItem[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.series_code)
    const name = asString(item.name_cn)
    if (!code || !name) {
      return []
    }
    return [
      {
        code,
        name,
        nameEn: asString(item.name_en) ?? "",
        category: asString(item.category) ?? "—",
        frequency: asString(item.frequency) ?? "—",
        unit: asString(item.base_unit) ?? "—",
        adjustment: asString(item.seasonal_adjustment) ?? "—",
      },
    ]
  })
}

function parseObservation(value: unknown): Observation[] {
  const record = asRecord(value)
  const date = asString(record?.observation_date)
  const amount = asNumber(record?.value)
  const raw = asString(record?.value)
  if (!date || amount === null) {
    return []
  }
  return [
    {
      date,
      value: amount,
      raw: raw ?? String(amount),
      realtimeStart: asString(record?.realtime_start) ?? "",
    },
  ]
}

function parseScore(value: unknown): DimensionScore | null {
  const record = asRecord(value)
  const code = asString(record?.code)
  if (!code) {
    return null
  }
  const evidence = Array.isArray(record?.evidence)
    ? record.evidence.flatMap((entry) => {
        const text = asString(entry)
        return text ? [text] : []
      })
    : []
  return {
    code,
    value: asNumber(record?.value),
    reason: asString(record?.reason),
    evidence,
  }
}

async function loadObservations(): Promise<void> {
  observationsBusy.value = true
  const codes = TILE_CODES.filter((code) => catalog.value.some((item) => item.code === code))
  try {
    const results = await Promise.all(
      codes.map(async (code) => {
        const params = new URLSearchParams({ limit: "60" })
        if (appliedAsOf.value) {
          params.set("as_of", appliedAsOf.value)
        }
        const response = await fetch(
          apiUrl(
            `/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`,
          ),
        )
        const body = await readJson(response)
        if (!response.ok) {
          throw errorFrom(body, response.headers.get("X-Request-ID"))
        }
        const data = asRecord(asRecord(body)?.data)
        return {
          code,
          rows: Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : [],
          emptyMessage: asString(data?.empty_message) ?? "",
        }
      }),
    )
    const nextObs: Record<string, Observation[]> = {}
    const nextEmpty: Record<string, string> = {}
    for (const item of results) {
      nextObs[item.code] = item.rows
      if (item.emptyMessage) {
        nextEmpty[item.code] = item.emptyMessage
      }
    }
    observationsByCode.value = nextObs
    emptyMessages.value = nextEmpty
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  } finally {
    observationsBusy.value = false
  }
}

async function loadAssessment(): Promise<void> {
  assessmentFailed.value = false
  assessment.value = null
  try {
    const params = new URLSearchParams()
    if (appliedAsOf.value) {
      params.set("as_of", appliedAsOf.value)
    }
    const query = params.toString()
    const response = await fetch(apiUrl(`/api/v1/macro/assessments${query ? `?${query}` : ""}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    assessment.value = {
      pitDate: asString(data?.pit_date) ?? "",
      regimeCode: asString(data?.regime_code) ?? "",
      growth: parseScore(data?.growth),
      inflation: parseScore(data?.inflation),
      liquidity: parseScore(data?.liquidity),
      notice: asString(data?.notice) ?? "",
      version: asString(data?.version) ?? "",
    }
  } catch {
    // 评估接口失败不让整页失败，但如实标注「未取到」，不编造结论。
    assessmentFailed.value = true
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const seriesResponse = await fetch(apiUrl("/api/v1/macro/series"))
    const seriesBody = await readJson(seriesResponse)
    if (!seriesResponse.ok) {
      throw errorFrom(seriesBody, seriesResponse.headers.get("X-Request-ID"))
    }
    const seriesData = asRecord(asRecord(seriesBody)?.data)
    providerCode.value = asString(seriesData?.provider_code) ?? ""
    availability.value = asString(seriesData?.availability) ?? ""
    catalogNotice.value = asString(seriesData?.notice) ?? ""
    catalog.value = parseCatalog(seriesData?.series)
    phase.value = "ready"
    await Promise.all([loadObservations(), loadAssessment()])
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

/** 应用观察日：指标带、近端序列与规则判断一起刷新，保持同一时点。 */
async function applyAsOf(): Promise<void> {
  // 必须先把观察日落到 appliedAsOf 再发请求：
  // loadObservations() 是用 appliedAsOf 拼 as_of 的，否则改观察日只会重发同一个时点。
  appliedAsOf.value = queryAsOf.value || browserToday
  await Promise.all([loadObservations(), loadAssessment()])
}

watch(queryAsOf, (value) => {
  if (!value) {
    queryAsOf.value = browserToday
  }
})

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="全球宏观分析"
      subtitle="把握全球经济周期，洞察宏观变量，发现大类资产配置机会"
    >
      <template #actions>
        <el-date-picker
          v-model="queryAsOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日"
          class="asof-select"
          @change="applyAsOf"
        />
        <el-button @click="helpOpen = true">数据说明</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="us" />
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        message="正在读取 FRED 序列目录与观测值"
      />

      <StateBlock
        v-else-if="phase === 'error'"
        state="error"
        :message="failure"
        @retry="load"
      />

      <template v-else>
        <StateBlock
          v-if="dataNotices.length"
          state="stale"
          :as-of="appliedAsOf"
          :message="dataNotices[0]"
          @retry="load"
        />

        <div class="grid-8">
          <MetricTile
            v-for="row in tileRows"
            :key="row.code"
            :label="row.item?.name ?? row.code"
            :value="row.latest ? formatValue(row.latest.value, row.item?.unit ?? '') : '—'"
            :period="tilePeriod(row)"
            :info="
              row.item
                ? `${row.item.nameEn || row.item.name} · 单位 ${row.item.unit} · ${row.item.adjustment} · 分类 ${row.item.category}`
                : '该序列代码不在后端序列目录中'
            "
            delta-label="较上期"
          >
            <template #chart>
              <MiniSpark
                v-if="row.spark.length > 1"
                :points="row.spark"
                kind="line"
                tone="muted"
                :width="104"
                :height="34"
              />
              <span v-else class="tile-empty">无可见观测值</span>
            </template>
            <template #delta>
              <DeltaValue
                v-if="row.delta !== null"
                :value="row.delta"
                :suffix="row.item?.unit === 'Percent' ? ' pct' : ''"
              />
              <DeltaValue v-else :value="null" />
            </template>
          </MetricTile>
        </div>

        <div class="grid-2-1">
          <PanelCard
            title="宏观象限：经济冷热 × 货币松紧"
            :info="`后端规则分数定位（macro-rules-v1）：横轴流动性、纵轴增长，各 0–100。最新时点 ${assessment?.pitDate || '—'}`"
          >
            <template #icon><span class="panel-icon">◎</span></template>
            <div class="chart-box">
              <VChart class="chart chart-quadrant" :option="quadrantOption" autoresize />
              <p v-if="!quadrantPoint" class="chart-note">
                <template v-if="assessmentFailed">
                  评估接口本次未返回可用结果，象限只画网格与风险区名称，不落点、不编造坐标。
                </template>
                <template v-else>
                  增长或流动性维度取不到分数（reason：{{ assessment?.growth?.reason ?? '—' }} /
                  {{ assessment?.liquidity?.reason ?? '—' }}），象限暂不落点。
                </template>
              </p>
            </div>
            <h3 class="doc-section">当前判断</h3>
            <template v-if="assessment && !allInsufficient">
              <p class="doc-text">
                组合状态 <code>{{ assessment.regimeCode || '—' }}</code>（{{ regimeLabel }}），
                规则版本 {{ assessment.version || '—' }}，后端时点 {{ assessment.pitDate || '—' }}。
              </p>
              <ul class="doc-list">
                <li v-for="item in dimensionRows" :key="item.code">
                  {{ dimensionLabel(item.code) }}：<span :style="{ color: scoreView(item).tone }">
                    {{ scoreView(item).label }}
                  </span>
                  <template v-if="reasonView(item.reason)"> · {{ reasonView(item.reason) }}</template>
                </li>
              </ul>
              <p class="muted">
                分数与理由全部来自后端规则，本页只做文案翻译，不做经济预测，也不构成投资建议。
              </p>
            </template>
            <p v-else class="doc-text muted">
              当前时点取不到可用的维度分数，本页不编造结论。可换一个观察日后重试。
            </p>
          </PanelCard>

          <div class="stack">
            <PanelCard title="通胀走势" info="CPI 与核心 PCE 的指数水平（2017=100），2% 目标为参考虚线">
              <template #icon><span class="panel-icon">▲</span></template>
              <VChart v-if="inflationOption" class="chart chart-inflation" :option="inflationOption" autoresize />
              <StateBlock
                v-else
                state="empty"
                message="CPI 与核心 PCE 都少于两个可解析的观测值，暂不画走势图"
              />
              <p class="chart-note">
                纵轴是「指数水平」而不是同比百分比，所以 2% 虚线只作目标位置对照，两者口径不同、不可直接比较；
                同比口径由后端规则负责，前端不做换算。
              </p>
            </PanelCard>

            <PanelCard
              title="规则解读（宏观状态 / 情景框架 / 关注点）"
              info="全部文案来自 assessments 的 regime_code、reason 与 evidence，不是模型生成内容"
            >
              <template #icon><span class="panel-icon">✦</span></template>
              <div class="readout-head">
                <StatusPill :text="regimePill.text" :tone="regimePill.tone" dot />
                <span class="muted">
                  后端时点 {{ assessment?.pitDate || '—' }} · 规则版本 {{ assessment?.version || '—' }}
                </span>
              </div>
              <p v-if="assessmentFailed" class="doc-text muted" role="alert">
                评估接口本次未返回可用结果，本页不编造结论。
                <button class="text-button" type="button" @click="loadAssessment">重试</button>
              </p>
              <p v-else-if="!assessment" class="doc-text muted">未取到评估结果，本页不编造结论。</p>
              <template v-else>
                <p class="doc-text">
                  当前宏观状态：{{ regimeLabel }}。各维度分数、理由与判定依据如下，均可在
                  <router-link to="/macro/scenarios">宏观综合判断</router-link> 页按同一观察日复核。
                </p>
                <ul class="doc-list">
                  <li v-for="item in dimensionRows" :key="item.code">
                    {{ dimensionLabel(item.code) }}：
                    <span :style="{ color: scoreView(item).tone }">{{ scoreView(item).label }}</span>
                    <template v-if="reasonView(item.reason)"> · {{ reasonView(item.reason) }}</template>
                    <ul v-if="item.evidence.length" class="evidence">
                      <li v-for="line in item.evidence" :key="line">
                        {{ evidenceParts(line).headline }}
                        <span v-if="evidenceParts(line).published" class="muted">
                          （公布日 {{ evidenceParts(line).published }}）
                        </span>
                      </li>
                    </ul>
                  </li>
                </ul>
              </template>

              <h3 class="doc-section">未来情景推演</h3>
              <ul class="scenario-list">
                <li v-for="item in SCENARIOS" :key="item.key" class="scenario-row">
                  <StatusPill :text="item.label" :tone="item.tone" />
                  <div class="scenario-body">
                    <p class="scenario-title">
                      {{ item.title }}
                      <span class="muted">· 概率：后端未提供概率字段</span>
                    </p>
                    <p class="muted">{{ item.basis }}</p>
                  </div>
                </li>
              </ul>
              <p class="chart-note">
                效果图里的「基准/乐观/悲观 概率 25%–50%」需要模型或人工情景输入，本项目后端没有该写入口
                （`/api/v1/macro/assessments` 只有 GET），所以这里只列情景框架、不填概率，也不生成方向结论。
              </p>

              <h3 class="doc-section">主要关注点</h3>
              <p class="chart-note">
                后端接口只返回 regime_code / reason / evidence，没有「主要关注点」字段；按不编造数据的要求，
                这里不填推测性条目。可在
                <router-link to="/macro/liquidity">货币与流动性</router-link> 与
                <router-link to="/macro/china">中国经济</router-link> 页查看各序列的真实观测值。
              </p>
            </PanelCard>
          </div>
        </div>

        <div class="grid-2">
          <PanelCard title="联邦基金利率" info="FEDFUNDS 有效联邦基金利率（%），阶梯式政策利率">
            <template #icon><span class="panel-icon">▤</span></template>
            <VChart v-if="fedOption" class="chart chart-third" :option="fedOption" autoresize />
            <StateBlock v-else state="empty" message="FEDFUNDS 少于两个可见观测值，暂不画走势图" />
          </PanelCard>

          <PanelCard
            title="美债收益率曲线"
            info="按序列目录里真实存在的期限落点，缺的期限不补点"
          >
            <template #icon><span class="panel-icon">◠</span></template>
            <VChart v-if="curveOption" class="chart chart-third" :option="curveOption" autoresize />
            <StateBlock
              v-else
              state="empty"
              :message="`目录中只有 ${curvePoints.length} 个国债期限点（${curvePoints.map((row) => row.tenor).join(' / ') || '无'}），不足以成曲线`"
            />
            <p class="chart-note">
              横轴固定为 3M/6M/1Y/2Y/3Y/5Y/7Y/10Y/20Y/30Y，仅上述期限有序列时落点。
              <template v-if="curveMissing.length">
                目录缺少期限：{{ curveMissing.join(' / ') }}，需先在 catalog/macro_series.py 登记并同步。
              </template>
              <template v-if="curvePoints.length"> 曲线时点：{{ curvePoints[0]?.date || '—' }}。</template>
            </p>
          </PanelCard>
        </div>

        <SourceFooter
          :as-of="latestObservationDate || appliedAsOf"
          :sources="['FRED（圣路易斯联储）']"
          :stale="stale"
          :note="sourceNote"
        />
      </template>
    </div>

    <el-dialog v-model="helpOpen" title="数据说明" width="620px">
      <p class="doc-text">
        数据源：FRED（圣路易斯联储）。接口：<code>GET /api/v1/macro/series</code>、
        <code>GET /api/v1/macro/series/{code}/observations</code>、
        <code>GET /api/v1/macro/assessments</code>。
        当前数据源状态：{{ availabilityLabel }}；观察日 {{ appliedAsOf || "今天" }}。
      </p>
      <p class="doc-text muted">
        指标带显示序列的原始口径观测值与相邻两期差；日频序列的「较上期」是日变动，月频是月变动。
        曲线与走势图的数据点全部来自接口，缺数据处显示真实空态与原因。
      </p>
      <ul class="doc-list">
        <li v-for="row in dataNotices" :key="row">{{ row }}</li>
      </ul>
    </el-dialog>
  </main>
</template>

<style scoped>
.asof-select {
  width: 210px;
}

.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.tile-empty {
  color: var(--muted);
  font-size: 11px;
}

.chart-box {
  position: relative;
}

.chart {
  width: 100%;
  background: var(--bg);
  border-radius: 12px;
}

.chart-quadrant {
  height: 380px;
}

.chart-inflation {
  height: 220px;
}

.chart-third {
  height: 240px;
}

.chart-note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.readout-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.scenario-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.scenario-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}

.scenario-row:last-child {
  border-bottom: 0;
}

.scenario-body {
  min-width: 0;
}

.scenario-title {
  margin: 0 0 2px;
  font-size: 13px;
}

.evidence {
  margin: 4px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}
</style>
