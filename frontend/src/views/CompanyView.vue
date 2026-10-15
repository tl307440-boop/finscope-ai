<script setup lang="ts">
/**
 * 公司研究 · 公司详情（/company/:symbol）。视觉目标：设计稿 4「公司详情」。
 *
 * 数据来源（全部只读；页面不做任何财务计算，缺数据一律走 StateBlock 空态）：
 *  - GET  /api/v1/instruments/search        路由 symbol → 证券元数据（id / CIK / 币种）
 *  - GET  /api/v1/companies/{id}/research   两个报告期的 XBRL 对比 + 规则结论（highlights/risks/unevaluated）
 *  - GET  /api/v1/market/bars/{id}          已入库日线：最新收盘、simple_return、迷你走势
 *  - GET  /api/v1/sec/facts?cik=&as_of=     已申报事实：财务趋势与财务数据对比
 *  - GET  /api/v1/analysis/model-status     模型是否已配置（AI 解读面板的来源说明）
 *  - POST /api/v1/analysis/filing           生成 AI 财报解读（需登录）
 *  - GET/POST/DELETE /api/v1/watchlists     加入自选（需登录）
 *
 * 效果图里有、但已接入接口没有的口径（总市值 / 流通市值 / 总股本 / 流通股本 / 行业 / 地区）
 * 一律显示「—」并在卡片脚注说明缺失原因，绝不填数字；也不在前端换算涨跌以外的东西。
 * 接口给的 change 是 year_over_year 比值（0.5 = +50%），展示时只做 ×100 的单位换算。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, formatMoment, listOf } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { modelNotice } from "../api/modelStatus"
import { restoreSession, useSessionStore } from "../stores/auth"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const props = defineProps<{ symbol?: string }>()
const session = useSessionStore()
const router = useRouter()

/** 效果图里的四个市值/股本口径：后端任何接口都没有这些字段，固定渲染为「—」。 */
const NO_FIELD = "已接入接口未提供该字段：行情只有 /market/bars，财务只有 SEC XBRL 事实。"

/** XBRL 概念的中文标题；不在表里的概念原样显示概念名，不做猜测。 */
const CONCEPT_LABELS: Record<string, string> = {
  "us-gaap:Revenues": "营业收入",
  "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": "营业收入",
  "us-gaap:OperatingIncomeLoss": "营业利润",
  "us-gaap:NetIncomeLoss": "归母净利润",
  "us-gaap:GrossProfit": "毛利",
  "us-gaap:NetCashProvidedByUsedInOperatingActivities": "经营活动现金流",
  "us-gaap:Assets": "总资产",
  "us-gaap:Liabilities": "总负债",
  "us-gaap:StockholdersEquity": "股东权益",
  "us-gaap:CashAndCashEquivalentsAtCarryingValue": "现金及现金等价物",
  "us-gaap:InventoryNet": "存货",
  "us-gaap:AccountsReceivableNetCurrent": "应收账款",
}

/** 规则行状态 → 标签语气。状态串原样展示，不臆造「高/中」风险等级。 */
const RULE_TONES: Record<string, "ok" | "warn" | "danger" | "muted" | "accent"> = {
  flagged: "danger",
  triggered: "danger",
  alert: "danger",
  ok: "ok",
  passed: "ok",
  not_evaluated: "muted",
  insufficient_data: "warn",
  insufficient: "warn",
}

const CHANGE_REASONS: Record<string, string> = {
  insufficient_data: "数据不足，接口未给同比",
}

/** 单季度判定：报告期跨度不超过 100 天。这是对接口区间的分类，不是重新计算财务数。 */
const QUARTER_MAX_DAYS = 100

interface ResearchSide {
  value: string
  unit: string
  form: string
  accessionNo: string
  sourceUrl: string
  filed: string
}

interface ResearchMetricRow {
  concept: string
  unit: string
  earlier: ResearchSide | null
  later: ResearchSide | null
  change: number | null
  changeReason: string
}

interface ResearchRule {
  ruleId: string
  status: string
  rationale: string
}

interface BarRow {
  tradeDate: string
  close: number | null
  closeText: string
  simpleReturn: number | null
}

interface FactRow {
  concept: string
  unit: string
  value: string
  periodStart: string
  periodEnd: string
  form: string
  filed: string
  accessionNo: string
  sourceUrl: string
}

interface SeriesPoint {
  periodEnd: string
  periodStart: string
  value: number
  closeText: string
}

interface MetricTileView {
  key: string
  label: string
  concept: string
  unit: string
  value: string | null
  period: string
  info: string
  delta: number | null
  changeReason: string
  earliest: string
  spark: number[]
  sparkKind: "bar" | "line"
}

interface ComparisonRow {
  key: string
  label: string
  concept: string
  unit: string
  change: number | null
  accessionNo: string
  sourceUrl: string
  cells: (string | null)[]
}

interface RunDraft {
  runId: string
  reportId: string
  status: string
  title: string
  message: string
  body: string
  notice: string
  asOf: string
}

type Phase = "loading" | "ready" | "error"
type LoadPhase = "idle" | "loading" | "ready" | "empty" | "error" | "unavailable"

const symbol = ref(propText(props.symbol))
const asOf = ref("2024-06-01")
const phase = ref<Phase>("loading")
const failure = ref("无法读取公司")
const instrument = ref<InstrumentHit | null>(null)

const researchNotice = ref("")
const periods = ref<string[]>([])
const periodA = ref("")
const periodB = ref("")
const metrics = ref<ResearchMetricRow[]>([])
const highlights = ref<ResearchRule[]>([])
const risks = ref<ResearchRule[]>([])
const unevaluated = ref<ResearchRule[]>([])
const researchAsOf = ref("")

const bars = ref<BarRow[]>([])
const barsPhase = ref<LoadPhase>("idle")
const barsMessage = ref("")
const barsSource = ref("")
const barsDelay = ref("")

const facts = ref<FactRow[]>([])
const factsPhase = ref<LoadPhase>("idle")
const factsFailure = ref("")
const factsNotice = ref("")

const trendMode = ref("cumulative")
const activeConcept = ref("")
const activeSection = ref("finance")

const modelLine = ref("正在确认模型状态")
const run = ref<RunDraft | null>(null)
const runMessage = ref("")
const generating = ref(false)
const copyState = ref("")

const watchBusy = ref(false)
const watchMessage = ref("")
const watchGroupId = ref("")
const inWatchlist = ref(false)

const heading = computed<string>(() => {
  const item = instrument.value
  const name = item?.nameCn || item?.nameEn || ""
  const code = item?.displaySymbol ?? symbol.value
  return name ? `${name} ${code}` : code
})

const metaLine = computed<string>(() => {
  const item = instrument.value
  if (!item) {
    return `公司研究 · 公司详情 · ${symbol.value}`
  }
  const parts = [item.exchange, item.market, `币种 ${item.currency}`]
  if (item.cik) {
    parts.push(`CIK ${item.cik}`)
  } else {
    parts.push("无 CIK（没有 SEC 申报）")
  }
  return `公司研究 · 公司详情 · ${parts.filter(Boolean).join(" · ")}`
})

const currency = computed<string>(() => instrument.value?.currency ?? "")

/** 后端枚举与 XBRL 单位是英文的，界面上要落成中文词（规格书 §1.4 中文界面）。 */
const TYPE_LABELS: Record<string, string> = {
  STOCK: "股票",
  INDEX: "指数",
  ETF: "ETF",
  FUND: "基金",
  BOND: "债券",
}

const MARKET_LABELS: Record<string, string> = {
  CN: "A股",
  US: "美股",
  HK: "港股",
}

const UNIT_LABELS: Record<string, string> = {
  SHARES: "股",
  USD: "美元",
  CNY: "元",
  HKD: "港元",
  PURE: "倍",
}

function labelOf(table: Record<string, string>, raw: string): string {
  const key = raw.trim().toUpperCase()
  return table[key] ?? raw
}

function unitLabel(unit: string): string {
  return labelOf(UNIT_LABELS, unit)
}

const tags = computed<string[]>(() => {
  const item = instrument.value
  if (!item) {
    return []
  }
  return [
    labelOf(TYPE_LABELS, item.instrumentType),
    item.exchange,
    labelOf(MARKET_LABELS, item.market),
  ].filter(Boolean)
})

const logoText = computed<string>(() => {
  const item = instrument.value
  const source = item?.nameEn || item?.nameCn || item?.displaySymbol || symbol.value
  return source.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "").slice(0, 4).toUpperCase() || "—"
})

const cik = computed<string>(() => instrument.value?.cik ?? "")

const latestBar = computed<BarRow | null>(() => bars.value[bars.value.length - 1] ?? null)

const pricePoints = computed<number[]>(() =>
  bars.value.flatMap((bar) => (bar.close === null ? [] : [bar.close])),
)

const headerStats = computed(() => {
  const item = instrument.value
  return [
    { label: "总市值", value: null as string | null, info: NO_FIELD },
    { label: "流通市值", value: null as string | null, info: NO_FIELD },
    { label: "总股本", value: null as string | null, info: NO_FIELD },
    { label: "流通股本", value: null as string | null, info: NO_FIELD },
    {
      label: "最新收盘",
      value: latestBar.value ? `${latestBar.value.closeText} ${currency.value}` : null,
      info: "来自 GET /api/v1/market/bars/{instrument_id}",
    },
    {
      label: "交易所 / 市场",
      value: item ? `${item.exchange} · ${item.market}` : null,
      info: "来自 GET /api/v1/instruments/search",
    },
    { label: "币种", value: item?.currency ?? null, info: "证券记账币种" },
    {
      label: "CIK",
      value: item?.cik ?? null,
      info: "没有 CIK 表示该证券本身没有 SEC 申报，不是接口故障",
    },
  ]
})

/** 效果图主打期货口径缺失时，如实说明接口给了几个概念。 */
const metricNote = computed<string>(() => {
  if (!metrics.value.length) {
    return ""
  }
  const names = metrics.value.map((metric) => `${conceptLabel(metric.concept)}（${metric.unit}）`)
  return `接口本次返回 ${metrics.value.length} 个概念：${names.join("、")}。效果图上的其余口径接口未返回，页面不补数字。`
})

const researchEmptyMessage = computed<string>(() => {
  if (!instrument.value) {
    return "还没有读到证券元数据"
  }
  if (!cik.value) {
    return "该证券没有 CIK，接口不会返回 SEC XBRL 事实，因此没有可对比的财务指标。"
  }
  if (!periods.value.length) {
    return `该观察日之前没有已申报事实。${researchNotice.value}`
  }
  return `接口没有返回可对比的两个报告期。${researchNotice.value}`
})

function spanDays(row: FactRow): number | null {
  if (!row.periodStart || !row.periodEnd) {
    return null
  }
  const start = new Date(`${row.periodStart}T00:00:00Z`).getTime()
  const end = new Date(`${row.periodEnd}T00:00:00Z`).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return null
  }
  return Math.round((end - start) / 86_400_000)
}

function inTrendMode(row: FactRow): boolean {
  const span = spanDays(row)
  if (span === null) {
    return trendMode.value === "cumulative"
  }
  const quarter = span <= QUARTER_MAX_DAYS
  return trendMode.value === "quarter" ? quarter : !quarter
}

const trendRows = computed<FactRow[]>(() => facts.value.filter(inTrendMode))

/** 概念 + 单位分组；只有 ≥2 期才能画趋势，1 期不画。 */
const trendGroups = computed<Map<string, SeriesPoint[]>>(() => {
  const grouped = new Map<string, SeriesPoint[]>()
  for (const row of trendRows.value) {
    const value = Number(row.value)
    if (!Number.isFinite(value)) {
      continue
    }
    const key = `${row.concept}|${row.unit}`
    const points = grouped.get(key) ?? []
    points.push({
      periodEnd: row.periodEnd,
      periodStart: row.periodStart,
      value,
      closeText: row.value,
    })
    grouped.set(key, points)
  }
  for (const points of grouped.values()) {
    points.sort((left, right) => left.periodEnd.localeCompare(right.periodEnd))
  }
  return grouped
})

const trendTabs = computed(() =>
  [...trendGroups.value.keys()].map((key) => {
    const [concept = "", unit = ""] = key.split("|")
    return { key, label: `${conceptLabel(concept)} · ${unitLabel(unit)}` }
  }),
)

const trendPoints = computed<SeriesPoint[]>(() => {
  const groups = trendGroups.value
  if (!groups.size) {
    return []
  }
  const wanted = groups.has(activeConcept.value) ? activeConcept.value : [...groups.keys()][0] ?? ""
  return groups.get(wanted) ?? []
})

const trendAxisUnit = computed<string>(() => {
  const groups = trendGroups.value
  if (!groups.size) {
    return ""
  }
  const wanted = groups.has(activeConcept.value) ? activeConcept.value : [...groups.keys()][0] ?? ""
  return unitLabel(wanted.split("|")[1] ?? "")
})

const trendNotice = computed<string>(() => {
  if (factsPhase.value === "loading") {
    return "正在读取已申报事实"
  }
  if (factsPhase.value === "error") {
    return factsFailure.value
  }
  if (factsPhase.value === "unavailable") {
    return "该证券没有 CIK，接口不会返回 SEC XBRL 事实。"
  }
  if (!facts.value.length) {
    return `该观察日之前没有已申报事实。${factsNotice.value}`
  }
  if (!trendGroups.value.size) {
    return `已申报事实里没有可成趋势的概念。${factsNotice.value}`
  }
  return ""
})

const trendOption = computed<EChartsOption | null>(() => {
  const points = trendPoints.value
  if (points.length < 2) {
    return null
  }
  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 72, right: 24, top: 28, bottom: 44 },
    xAxis: {
      type: "category",
      data: points.map((point) => point.periodEnd),
      axisLabel: { color: "#95a6be", fontSize: 11, rotate: 0 },
      axisLine: { lineStyle: { color: "rgba(231,237,248,0.12)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      name: trendAxisUnit.value,
      nameTextStyle: { color: "#95a6be", fontSize: 11 },
      axisLabel: { color: "#95a6be", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(231,237,248,0.06)" } },
    },
    series: [
      {
        name: `已申报数值（${trendAxisUnit.value}）`,
        type: "bar",
        barMaxWidth: 42,
        itemStyle: { color: "#3b82f6" },
        data: points.map((point) => point.value),
      },
    ],
  }
})

const comparisonColumns = computed<string[]>(() => {
  const seen = new Set<string>()
  for (const row of trendRows.value) {
    seen.add(row.periodEnd)
  }
  return [...seen].sort((left, right) => right.localeCompare(left)).slice(0, 6)
})

const comparisonRows = computed<ComparisonRow[]>(() => {
  const columns = comparisonColumns.value
  const grouped = new Map<string, ComparisonRow>()
  for (const row of trendRows.value) {
    const key = `${row.concept}|${row.unit}`
    const current =
      grouped.get(key) ??
      ({
        key,
        label: conceptLabel(row.concept),
        concept: row.concept,
        unit: unitLabel(row.unit),
        change: null,
        accessionNo: row.accessionNo,
        sourceUrl: row.sourceUrl,
        cells: columns.map(() => null),
      } satisfies ComparisonRow)
    const index = columns.indexOf(row.periodEnd)
    if (index >= 0) {
      current.cells[index] = row.value
    }
    grouped.set(key, current)
  }
  for (const row of grouped.values()) {
    const matched = metrics.value.find(
      (metric) => metric.concept === row.concept && metric.unit === row.unit,
    )
    if (matched) {
      row.change = matched.change
      if (matched.later) {
        row.accessionNo = matched.later.accessionNo
        row.sourceUrl = matched.later.sourceUrl
      }
    }
  }
  return [...grouped.values()]
})

const comparisonModeTabs = computed(() => [
  { key: "quarter", label: "单季度" },
  { key: "cumulative", label: "累计" },
])

const sectionTabs = computed(() => [
  { key: "overview", label: "公司概况" },
  { key: "finance", label: "财务总览" },
  { key: "trend", label: "财务趋势" },
  { key: "ai", label: "AI财报解读" },
  { key: "valuation", label: "估值分析" },
  { key: "filings", label: "原始财报", to: `/company/${encodeURIComponent(symbol.value)}/filings` },
])

const reportRows = computed(() => {
  const body = run.value?.body ?? ""
  return body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line, index) => {
      const match = line.match(/https?:\/\/\S+/)
      const url = match ? match[0] : ""
      return {
        index: index + 1,
        text: url ? line.replace(url, "").trim() : line,
        url,
      }
    })
})

const reportLink = computed<string>(() => {
  if (!run.value) {
    return ""
  }
  return `/company/${encodeURIComponent(symbol.value)}/analysis/${encodeURIComponent(run.value.runId)}`
})

/** 运行状态 → 标签语气；状态串原样展示，不改写后端给的措辞。 */
const runTone = computed<"ok" | "warn" | "danger" | "muted">(() => {
  const status = run.value?.status ?? ""
  if (status === "succeeded") {
    return "ok"
  }
  if (status === "failed") {
    return "danger"
  }
  return "muted"
})

/**
 * 底部来源条写的是「数据从哪个平台来的」，不是接口路径。
 * 只列真的用到的：主数据一定有；有 CIK 才可能有 SEC 事实；有入库日线才谈行情源。
 */
const sourceList = computed<string[]>(() => {
  const list = ["本站证券主数据"]
  if (bars.value.length) {
    list.push(barsSource.value ? `${barsSource.value} 日线` : "已入库日线")
  }
  if (instrument.value?.cik) {
    list.push("SEC EDGAR")
  }
  return list
})

const watchLabel = computed<string>(() => (inWatchlist.value ? "已自选" : "加入自选"))

function propText(value: string | undefined): string {
  return typeof value === "string" && value ? value : ""
}

function conceptLabel(concept: string): string {
  return CONCEPT_LABELS[concept] ?? concept
}

function ruleTone(status: string): "ok" | "warn" | "danger" | "muted" | "accent" {
  return RULE_TONES[status] ?? "muted"
}

function changeReasonLabel(reason: string): string {
  return CHANGE_REASONS[reason] ?? reason
}

function round2(value: number): number {
  return Math.round(value * 100) / 100
}

/** 接口里的变化是比值（0.5 → +50%），这里只做单位换算。 */
function toPercent(value: unknown): number | null {
  const ratio = typeof value === "number" ? value : asNumber(value)
  return ratio === null ? null : round2(ratio * 100)
}

function sparkKind(unit: string): "bar" | "line" {
  const flowUnits = ["USD", "CNY", "EUR", "HKD", "shares"]
  return flowUnits.includes(unit.toUpperCase()) ? "bar" : "line"
}

function parseSide(value: unknown): ResearchSide | null {
  const record = asRecord(value)
  const amount = asString(record?.value)
  if (!amount) {
    return null
  }
  return {
    value: amount,
    unit: asString(record?.unit) ?? "",
    form: asString(record?.form) ?? "",
    accessionNo: asString(record?.accession_no) ?? "",
    sourceUrl: asString(record?.source_url) ?? "",
    filed: asString(record?.filed) ?? "",
  }
}

function parseMetric(value: unknown): ResearchMetricRow[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  if (!concept) {
    return []
  }
  return [
    {
      concept,
      unit: asString(record?.unit) ?? "",
      earlier: parseSide(record?.earlier),
      later: parseSide(record?.later),
      change: toPercent(record?.change),
      changeReason: asString(record?.change_reason) ?? "",
    },
  ]
}

function parseRule(value: unknown): ResearchRule[] {
  const record = asRecord(value)
  const ruleId = asString(record?.rule_id)
  if (!ruleId) {
    return []
  }
  return [
    {
      ruleId,
      status: asString(record?.status) ?? "",
      rationale: asString(record?.rationale) ?? "",
    },
  ]
}

function parseBar(value: unknown): BarRow[] {
  const record = asRecord(value)
  const tradeDate = asString(record?.trade_date)
  const close = asString(record?.close)
  if (!tradeDate || !close) {
    return []
  }
  return [
    {
      tradeDate,
      close: asNumber(record?.close),
      closeText: close,
      simpleReturn: asNumber(record?.simple_return),
    },
  ]
}

function parseFact(value: unknown): FactRow[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  const amount = asString(record?.value)
  const periodEnd = asString(record?.period_end)
  if (!concept || !amount || !periodEnd) {
    return []
  }
  return [
    {
      concept,
      unit: asString(record?.unit) ?? "",
      value: amount,
      periodStart: asString(record?.period_start) ?? "",
      periodEnd,
      form: asString(record?.form) ?? "",
      filed: asString(record?.filed) ?? "",
      accessionNo: asString(record?.accession_no) ?? "",
      sourceUrl: asString(record?.source_url) ?? "",
    },
  ]
}

/** 概念 → 迷你走势：用同一概念、同一单位的已申报数值，少于 2 期不画。 */
function sparkPoints(concept: string, unit: string): number[] {
  return facts.value
    .filter((row) => row.concept === concept && row.unit === unit)
    .map((row) => Number(row.value))
    .filter((value) => Number.isFinite(value))
}

const metricTiles = computed<MetricTileView[]>(() =>
  metrics.value.map((metric) => {
    const points = sparkPoints(metric.concept, metric.unit)
    const side = metric.later ?? metric.earlier
    return {
      key: `${metric.concept}|${metric.unit}`,
      label: conceptLabel(metric.concept),
      concept: metric.concept,
      unit: unitLabel(metric.unit),
      value: side?.value ?? null,
      period: side?.filed ? `申报 ${side.filed}` : "",
      info: `${metric.concept} · 单位 ${unitLabel(metric.unit)}${side?.accessionNo ? ` · 申报号 ${side.accessionNo}` : ""}`,
      delta: metric.change,
      changeReason: metric.changeReason ? changeReasonLabel(metric.changeReason) : "",
      earliest: points.length >= 2 ? `${points.length} 期已申报` : "不足 2 期，不画走势",
      spark: points,
      sparkKind: sparkKind(metric.unit),
    }
  }),
)

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadCompany(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  metrics.value = []
  highlights.value = []
  risks.value = []
  unevaluated.value = []
  facts.value = []
  factsPhase.value = "idle"
  bars.value = []
  barsPhase.value = "idle"
  run.value = null
  runMessage.value = ""
  const wanted = symbol.value.trim()
  if (!wanted) {
    phase.value = "error"
    failure.value = "路由里没有证券代码"
    return
  }
  try {
    const found = await searchInstruments(wanted, undefined, 10)
    const hit =
      found.results.find((row) => row.displaySymbol.toUpperCase() === wanted.toUpperCase()) ??
      found.results.find((row) => row.canonicalCode.toUpperCase() === wanted.toUpperCase()) ??
      found.results[0] ??
      null
    if (!hit) {
      phase.value = "error"
      failure.value = found.emptyMessage ?? "暂无该证券"
      return
    }
    instrument.value = hit
    await loadResearch()
    await loadBars(hit.id)
    if (hit.cik) {
      await loadFacts(hit.cik)
    } else {
      factsPhase.value = "unavailable"
    }
    void loadWatchState(hit.id)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadResearch(): Promise<void> {
  const item = instrument.value
  if (!item) {
    return
  }
  phase.value = "loading"
  const params = new URLSearchParams({ as_of: asOf.value })
  if (periodA.value && periodB.value && periodA.value !== periodB.value) {
    params.set("period_a", periodA.value)
    params.set("period_b", periodB.value)
  }
  try {
    const response = await fetch(apiUrl(`/api/v1/companies/${item.id}/research?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const metaAsOf = asString(asRecord(asRecord(body)?.meta)?.as_of)
    researchAsOf.value = metaAsOf ? formatMoment(metaAsOf) : ""
    const data = asRecord(asRecord(body)?.data)
    const available = Array.isArray(data?.available_periods)
      ? data.available_periods.filter((entry): entry is string => typeof entry === "string")
      : []
    periods.value = available
    researchNotice.value = asString(data?.notice) ?? ""
    const hadSelection = Boolean(periodA.value && periodB.value && periodA.value !== periodB.value)
    if (!periodA.value && available[0]) {
      periodA.value = available[0]
    }
    if (!periodB.value && available.length > 1) {
      periodB.value = available[available.length - 1]
    }
    if (!hadSelection && periodA.value && periodB.value && periodA.value !== periodB.value) {
      await loadResearch()
      return
    }
    metrics.value = Array.isArray(data?.metrics) ? data.metrics.flatMap(parseMetric) : []
    highlights.value = Array.isArray(data?.highlights) ? data.highlights.flatMap(parseRule) : []
    risks.value = Array.isArray(data?.risks) ? data.risks.flatMap(parseRule) : []
    unevaluated.value = Array.isArray(data?.unevaluated) ? data.unevaluated.flatMap(parseRule) : []
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadBars(instrumentId: string): Promise<void> {
  barsPhase.value = "loading"
  barsMessage.value = ""
  try {
    const response = await fetch(apiUrl(`/api/v1/market/bars/${instrumentId}?limit=60`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    const rows = listOf(data?.bars).flatMap(parseBar)
    barsSource.value = asString(data?.source) ?? ""
    barsDelay.value = asString(data?.delay) ?? ""
    // 接口在有日线时把 empty_message 置为 null，这时候不该再套默认文案，
    // 否则底部会写着「暂无已同步的日线」而上面正显示着收盘价。
    const pageMessage = asString(data?.empty_message) ?? ""
    barsMessage.value = pageMessage || (rows.length ? "" : "暂无已同步的日线")
    bars.value = rows
    barsPhase.value = rows.length ? "ready" : "empty"
  } catch (caught) {
    bars.value = []
    barsMessage.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    barsPhase.value = "error"
  }
}

async function loadFacts(targetCik: string): Promise<void> {
  factsPhase.value = "loading"
  factsFailure.value = ""
  const params = new URLSearchParams({ cik: targetCik, as_of: asOf.value })
  try {
    const response = await fetch(apiUrl(`/api/v1/sec/facts?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    const rows = listOf(data?.facts).flatMap(parseFact)
    factsNotice.value = asString(data?.notice) ?? ""
    facts.value = rows
    factsPhase.value = rows.length ? "ready" : "empty"
    activeConcept.value = ""
  } catch (caught) {
    facts.value = []
    factsFailure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    factsPhase.value = "error"
  }
}

/** 加入自选：先保证有一个分组，再按当前状态 POST / DELETE。 */
async function ensureWatchGroup(): Promise<string> {
  if (watchGroupId.value) {
    return watchGroupId.value
  }
  const listResponse = await authorizedFetch(
    "/api/v1/watchlists",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const listBody = await readJson(listResponse)
  if (listResponse.ok) {
    const group = listOf(dataOf(listBody)?.groups)[0]
    const id = asString(group?.id) ?? ""
    if (id) {
      watchGroupId.value = id
      return id
    }
  }
  const createResponse = await authorizedFetch(
    "/api/v1/watchlists",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "我的自选" }),
    },
    session.accessToken,
    refreshToken,
  )
  const createBody = await readJson(createResponse)
  if (createResponse.ok) {
    const id = asString(dataOf(createBody)?.id) ?? ""
    if (id) {
      watchGroupId.value = id
      return id
    }
  }
  const error = asRecord(asRecord(createBody)?.error)
  watchMessage.value = asString(error?.message) ?? "无法准备自选分组"
  return ""
}

async function loadWatchState(instrumentId: string): Promise<void> {
  if (!session.accessToken) {
    return
  }
  try {
    const response = await authorizedFetch(
      "/api/v1/watchlists",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    if (!response.ok) {
      return
    }
    const body = await readJson(response)
    const group = listOf(dataOf(body)?.groups)[0]
    watchGroupId.value = asString(group?.id) ?? ""
    inWatchlist.value = listOf(group?.items).some(
      (item) => asString(item.instrument_id) === instrumentId,
    )
  } catch {
    // 自选状态只是按钮文案，读不到就不改按钮。
  }
}

async function toggleWatch(): Promise<void> {
  const item = instrument.value
  if (!item) {
    return
  }
  watchMessage.value = ""
  if (!session.accessToken) {
    watchMessage.value = "登录后可以加入自选"
    return
  }
  watchBusy.value = true
  try {
    const groupId = await ensureWatchGroup()
    if (!groupId) {
      return
    }
    if (inWatchlist.value) {
      const response = await authorizedFetch(
        `/api/v1/watchlists/${groupId}/items/${encodeURIComponent(item.id)}`,
        { method: "DELETE" },
        session.accessToken,
        refreshToken,
      )
      const payload = await readJson(response)
      if (!response.ok) {
        const error = asRecord(asRecord(payload)?.error)
        watchMessage.value = asString(error?.message) ?? "无法移出自选"
        return
      }
      inWatchlist.value = false
      watchMessage.value = "已移出自选"
      return
    }
    const response = await authorizedFetch(
      `/api/v1/watchlists/${groupId}/items`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instrument_id: item.id }),
      },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    if (!response.ok) {
      const error = asRecord(asRecord(payload)?.error)
      watchMessage.value = asString(error?.message) ?? "无法加入自选"
      return
    }
    inWatchlist.value = true
    watchMessage.value = "已加入自选"
  } catch {
    watchMessage.value = "无法连接接口"
  } finally {
    watchBusy.value = false
  }
}

/** 生成 AI 财报解读：POST /api/v1/analysis/filing，响应就地渲染，并留到分析页的入口。 */
async function generateReport(): Promise<void> {
  runMessage.value = ""
  const item = instrument.value
  if (!session.accessToken) {
    runMessage.value = "请先登录后再生成"
    return
  }
  if (!item || !periodA.value || !periodB.value || periodA.value === periodB.value) {
    runMessage.value = "请选择两个不同的报告期"
    return
  }
  generating.value = true
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/filing",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instrument_id: item.id,
          as_of: asOf.value,
          period_a: periodA.value,
          period_b: periodB.value,
        }),
      },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const data = dataOf(payload)
    const runId = asString(data?.run_id)
    if (!response.ok || !runId) {
      const error = asRecord(asRecord(payload)?.error)
      runMessage.value = asString(error?.message) ?? "无法生成研究记录"
      return
    }
    run.value = {
      runId,
      reportId: asString(data?.report_id) ?? "",
      status: asString(data?.status) ?? "",
      title: asString(data?.title) ?? "研究记录",
      message: asString(data?.message) ?? "",
      body: typeof data?.body === "string" ? data.body : "",
      notice: asString(data?.notice) ?? "",
      asOf: asString(asRecord(asRecord(payload)?.meta)?.as_of) ?? "",
    }
    runMessage.value = "已生成，可查看完整报告页"
  } catch {
    runMessage.value = "无法连接接口"
  } finally {
    generating.value = false
  }
}

async function copyReport(): Promise<void> {
  const text = run.value?.body ?? ""
  if (!text) {
    copyState.value = "没有可复制的正文"
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    copyState.value = "已复制正文"
  } catch {
    copyState.value = "浏览器拒绝访问剪贴板，请手动选中正文复制"
  }
}

async function loadModel(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/analysis/model-status"))
    const body = await readJson(response)
    const data = asRecord(asRecord(body)?.data)
    modelLine.value = modelNotice(asString(data?.message), data?.available === true)
  } catch {
    modelLine.value = modelNotice(null, false)
  }
}

function selectSection(key: string): void {
  activeSection.value = key
  const target = document.getElementById(`sec-${key}`)
  if (target) {
    target.scrollIntoView({ behavior: "smooth", block: "start" })
  }
}

function openAnalysis(): void {
  if (!run.value) {
    return
  }
  void router.push(reportLink.value)
}

function openFilings(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}/filings`)
}

onMounted(() => {
  void loadCompany()
  void loadModel()
})

watch(
  () => props.symbol,
  (next) => {
    const text = propText(next)
    if (text && text !== symbol.value) {
      symbol.value = text
      periodA.value = ""
      periodB.value = ""
      void loadCompany()
    }
  },
)
</script>

<template>
  <main class="page stack">
    <nav class="crumb" aria-label="面包屑">
      <span>公司研究</span>
      <i aria-hidden="true">›</i>
      <strong>公司详情</strong>
    </nav>

    <PageHeader :title="heading" :subtitle="metaLine">
      <template #actions>
        <span class="chip">{{ instrument?.displaySymbol ?? symbol }}</span>
        <button class="ghost-btn" type="button" :disabled="watchBusy" @click="toggleWatch">
          {{ watchLabel }}
        </button>
        <button class="ghost-btn" type="button" @click="openFilings">原始财报</button>
      </template>
      <template #filters>
        <el-input v-model="asOf" type="date" size="small" class="filter-date" />
        <el-select v-model="periodA" size="small" placeholder="报告期甲" class="filter-period">
          <el-option v-for="period in periods" :key="`a-${period}`" :label="period" :value="period" />
        </el-select>
        <el-select v-model="periodB" size="small" placeholder="报告期乙" class="filter-period">
          <el-option v-for="period in periods" :key="`b-${period}`" :label="period" :value="period" />
        </el-select>
        <el-button size="small" @click="loadResearch">对比</el-button>
      </template>
    </PageHeader>

    <p v-if="watchMessage" class="muted" role="status">{{ watchMessage }}</p>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取公司研究"
      :message="failure"
      @retry="loadCompany"
    />

    <template v-else>
      <PanelCard>
        <div class="company-head">
          <div class="logo" aria-hidden="true">{{ logoText }}</div>
          <div class="ident">
            <div class="name-row">
              <h2>{{ instrument?.nameCn || instrument?.nameEn || symbol }}</h2>
              <span class="code">{{ instrument?.displaySymbol }}</span>
              <StatusPill v-for="tag in tags" :key="tag" :text="tag" tone="muted" />
            </div>
            <p class="full-name">
              {{ instrument?.nameEn || instrument?.nameCn || "—" }}
              <span class="muted">（接口只提供证券元数据，不含行业归类与注册地）</span>
            </p>
            <p class="meta-line">
              <span>{{ instrument?.exchange || "—" }}</span>
              <i aria-hidden="true">›</i>
              <span>{{ instrument?.market || "—" }}</span>
              <span class="muted">· 币种 {{ currency || "—" }} · CIK {{ cik || "—" }}</span>
            </p>
          </div>

          <div class="quote">
            <StateBlock v-if="barsPhase === 'loading'" state="loading" :rows="2" />
            <StateBlock
              v-else-if="barsPhase === 'error'"
              state="error"
              title="无法读取行情"
              :message="barsMessage"
              @retry="loadCompany"
            />
            <StateBlock
              v-else-if="barsPhase !== 'ready'"
              state="empty"
              title="最新收盘"
              :message="barsMessage"
            />
            <template v-else-if="latestBar">
              <p class="price">
                {{ latestBar.closeText }}<small> {{ currency }}</small>
              </p>
              <p class="quote-delta">
                <DeltaValue :value="toPercent(latestBar.simpleReturn)" suffix="%" />
                <span class="muted">
                  {{ latestBar.tradeDate }} · {{ barsSource }} · {{ barsDelay }}
                </span>
              </p>
              <MiniSpark :points="pricePoints" kind="line" tone="accent" :width="180" :height="38" />
            </template>
          </div>

          <dl class="key-values">
            <div v-for="row in headerStats" :key="row.label" class="kv">
              <dt>
                {{ row.label }}
                <span class="info" :title="row.info">?</span>
              </dt>
              <dd :class="{ dim: !row.value }">{{ row.value ?? "—" }}</dd>
            </div>
          </dl>
        </div>
        <p class="footnote">
          总市值 / 流通市值 / 总股本 / 流通股本 / 行业归类 / 地区：{{ NO_FIELD }}
        </p>
      </PanelCard>

      <PanelCard compact>
        <SectionTabs :tabs="sectionTabs" :model-value="activeSection" @update:model-value="selectSection" />
      </PanelCard>

      <div class="grid-2-1 pair">
      <PanelCard id="sec-finance" title="核心财务指标" :info="`GET /api/v1/companies/{id}/research · ${researchNotice || '接口未提供来源说明'}`">
        <template #icon>▤</template>
        <template #actions>
          <span class="muted">
            最新财报：{{ periodB || "—" }}
            <template v-if="periodA && periodB">（对比 {{ periodA }} → {{ periodB }}）</template>
          </span>
        </template>

        <StateBlock
          v-if="!metricTiles.length"
          state="empty"
          title="暂无核心财务指标"
          :message="researchEmptyMessage"
        />
        <template v-else>
          <div :class="metricTiles.length >= 4 ? 'grid-6' : 'grid-3'">
            <MetricTile
              v-for="tile in metricTiles"
              :key="tile.key"
              :label="tile.label"
              :value="tile.value"
              :unit="tile.unit"
              :period="tile.earliest"
              :info="tile.info"
              delta-label="同比"
            >
              <template #delta>
                <DeltaValue :value="tile.delta" suffix="%" size="sm" />
                <StatusPill v-if="tile.changeReason" :text="tile.changeReason" tone="warn" />
              </template>
              <template v-if="tile.spark.length >= 2" #chart>
                <MiniSpark
                  :points="tile.spark"
                  :kind="tile.sparkKind"
                  :tone="(tile.delta ?? 0) >= 0 ? 'up' : 'down'"
                  :width="96"
                  :height="30"
                />
              </template>
            </MetricTile>
          </div>
          <p class="footnote">{{ metricNote }}</p>
        </template>
      </PanelCard>

      <PanelCard
        id="sec-risks"
        title="风险提示"
        more-text="原始财报"
        :more-to="`/company/${encodeURIComponent(symbol)}/filings`"
      >
        <template #icon>⚠</template>
        <StateBlock
          v-if="!risks.length"
          state="empty"
          title="接口未返回风险标记"
          :message="`${researchNotice || '接口未提供来源说明'}规则引擎只有在拿到对应科目时才会标记风险，未标记不等于没有风险。`"
        />
        <ul v-else class="rule-list">
          <li v-for="rule in risks" :key="rule.ruleId">
            <div class="rule-head">
              <strong>{{ rule.ruleId }}</strong>
              <StatusPill :text="rule.status || '—'" :tone="ruleTone(rule.status)" />
            </div>
            <p class="muted">{{ rule.rationale }}</p>
          </li>
        </ul>

        <div v-if="unevaluated.length" class="unevaluated">
          <p class="sub-title">未评估的规则（接口给出的原因）</p>
          <ul class="rule-list">
            <li v-for="rule in unevaluated" :key="`u-${rule.ruleId}`">
              <div class="rule-head">
                <strong>{{ rule.ruleId }}</strong>
                <StatusPill :text="rule.status || '—'" :tone="ruleTone(rule.status)" />
              </div>
              <p class="muted">{{ rule.rationale }}</p>
            </li>
          </ul>
        </div>
      </PanelCard>
      </div>

      <div class="grid-2-1 pair">
        <div class="stack">
          <PanelCard
            id="sec-trend"
            title="财务趋势"
        info="GET /api/v1/sec/facts?cik=&as_of= 已申报数值。接口没有提供逐期变化序列，因此只画一条数值轴，右轴线留空而不是自算同比。"
      >
        <template #icon>◔</template>
        <template #actions>
          <div class="trend-actions">
            <SectionTabs v-if="trendTabs.length" :tabs="trendTabs" :model-value="activeConcept" @update:model-value="activeConcept = $event" />
            <SectionTabs :tabs="comparisonModeTabs" :model-value="trendMode" @update:model-value="trendMode = $event" />
          </div>
        </template>

        <StateBlock v-if="factsPhase === 'loading'" state="loading" :rows="3" />
        <StateBlock
          v-else-if="!trendOption"
          state="empty"
          title="暂无可画趋势的已申报数值"
          :message="trendNotice || '该口径少于 2 期已申报数值，接口也未提供逐期变化序列，因此不画曲线。'"
        />
        <VChart v-else class="trend-chart" :option="trendOption" autoresize aria-label="财务趋势" />
        <p v-if="trendOption" class="footnote">
          单位 {{ trendAxisUnit }} · 单季度/累计按接口给出的报告期跨度分类 · 数据来源 SEC EDGAR
        </p>
      </PanelCard>

      <PanelCard id="sec-compare" title="财务数据对比" info="指标行与各期数值都来自 GET /api/v1/sec/facts；同比列来自 /research 的 year_over_year，缺失即「—」，前端不自算。">
        <template #icon>▦</template>
        <template #actions>
          <SectionTabs :tabs="comparisonModeTabs" :model-value="trendMode" @update:model-value="trendMode = $event" />
        </template>

        <StateBlock v-if="factsPhase === 'loading'" state="loading" :rows="3" />
        <StateBlock
          v-else-if="!comparisonRows.length"
          state="empty"
          title="暂无可对比的已申报事实"
          :message="trendNotice || '该口径没有已申报数值。'"
        />
        <el-table v-else :data="comparisonRows" stripe class="tabular">
          <el-table-column prop="label" label="指标" min-width="150" />
          <el-table-column prop="unit" label="单位" width="90" />
          <el-table-column
            v-for="(column, index) in comparisonColumns"
            :key="column"
            :label="column"
            min-width="120"
          >
            <template #default="scope">{{ scope.row.cells[index] ?? "—" }}</template>
          </el-table-column>
          <el-table-column label="同比" width="110">
            <template #default="scope">
              <DeltaValue :value="scope.row.change" suffix="%" size="sm" />
            </template>
          </el-table-column>
          <el-table-column label="来源" min-width="120">
            <template #default="scope">
              <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" target="_blank" rel="noreferrer noopener">
                {{ scope.row.accessionNo || "原始文件" }}
              </a>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>
        </div>

        <PanelCard id="sec-ai" title="AI 财报解读" :info="modelLine">
        <template #icon>✦</template>
        <template #actions>
          <el-button
            size="small"
            type="primary"
            :loading="generating"
            :disabled="!session.accessToken"
            @click="generateReport"
          >
            {{ run ? "重新生成" : "生成 AI 财报解读" }}
          </el-button>
        </template>

        <p class="muted">{{ modelLine }}</p>

        <StateBlock
          v-if="!session.accessToken"
          state="denied"
          title="需要登录"
          message="生成 AI 财报解读会写入你自己的研究记录，请先登录。未登录时本页不展示任何未生成的内容。"
        >
          <router-link :to="`/login?redirect=${encodeURIComponent(`/company/${symbol}`)}`">
            去登录 ›
          </router-link>
        </StateBlock>

        <StateBlock
          v-else-if="!run"
          state="empty"
          title="尚未生成"
          message="选择两个报告期后点右上角「生成 AI 财报解读」。后端未配置大模型时，报告由规则整理已入库数字，内容会如实标注。"
        />

        <template v-else>
          <div class="report-head">
            <h3>{{ run.title }}</h3>
            <StatusPill :text="run.status || '—'" :tone="runTone" dot />
            <span class="muted">运行 {{ run.runId }} · 服务端截止 {{ run.asOf || "—" }}</span>
          </div>
          <ol class="report-lines">
            <li v-for="row in reportRows" :key="row.index">
              <span class="line-index">{{ row.index }}</span>
              <span class="line-text">{{ row.text }}</span>
              <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer noopener">来源 ›</a>
            </li>
          </ol>
          <p v-if="run.message" class="muted">{{ run.message }}</p>
          <p v-if="run.notice" class="muted">{{ run.notice }}</p>
          <div class="action-row">
            <button class="ghost-btn" type="button" @click="copyReport">复制正文</button>
            <button class="ghost-btn" type="button" @click="openAnalysis">打开完整报告页</button>
          </div>
          <p v-if="copyState" class="muted" role="status">{{ copyState }}</p>
        </template>
        <p v-if="runMessage" class="muted" role="status">{{ runMessage }}</p>
      </PanelCard>
      </div>

      <PanelCard id="sec-valuation" title="估值分析" compact>
        <template #icon>◎</template>
        <StateBlock
          state="empty"
          title="接口没有市值与股本，无法给出估值倍数"
          message="PE / PB / PS 需要总市值或股价×股本，而 /market/bars 只有价格、/sec/facts 只有财报科目，两者都不含股本与市值字段。按项目硬规矩，这里不补数字、不画示意图。"
        >
          <router-link :to="`/company/${encodeURIComponent(symbol)}/metrics`">
            样例计算器（后端标注为样例，不是公司真实财报）›
          </router-link>
        </StateBlock>
      </PanelCard>

      <SourceFooter
        :as-of="researchAsOf"
        :sources="sourceList"
        :stale="barsPhase !== 'ready'"
        :note="`行情：${barsMessage || '已入库日线'}；财务：${researchNotice || 'SEC XBRL 事实'}`"
      />
    </template>
  </main>
</template>

<style scoped>
/* 两栏并排时按内容高度对齐，避免短卡片被长卡片拉出大片空白（列宽仍由 global.css 的 .grid-2-1 决定）。 */
.pair {
  align-items: start;
}

.crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

.crumb strong {
  color: var(--text);
  font-weight: 600;
}

.chip {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.ghost-btn {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 13px;
  padding: 5px 12px;
  cursor: pointer;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.filter-date {
  width: 148px;
}

.filter-period {
  width: 138px;
}

.company-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1.4fr) minmax(0, 1fr) minmax(0, 1.1fr);
  gap: 18px;
  align-items: start;
}

.logo {
  width: 64px;
  height: 64px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--accent);
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 0.4px;
}

.name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.name-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}

.code {
  color: var(--muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.full-name {
  margin: 6px 0 4px;
  font-size: 13px;
}

.meta-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.quote {
  display: grid;
  gap: 6px;
  align-content: start;
}

.price {
  margin: 0;
  font-size: 30px;
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.price small {
  margin-left: 4px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.quote-delta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 13px;
}

.key-values {
  display: grid;
  gap: 6px;
  margin: 0;
}

.kv {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 4px;
}

.kv:last-child {
  border-bottom: 0;
}

.kv dt {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font-size: 13px;
}

.kv dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.kv dd.dim {
  color: var(--muted);
  font-weight: 500;
}

.info {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1px solid var(--line);
  font-size: 9px;
  cursor: help;
}

.footnote {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.rule-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.rule-list li {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  padding: 10px 12px;
}

.rule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.rule-list p {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.6;
}

.unevaluated {
  margin-top: 14px;
  border-top: 1px solid var(--line-soft);
  padding-top: 12px;
}

.sub-title {
  margin: 0 0 8px;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
}

.trend-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.trend-chart {
  width: 100%;
  height: 320px;
}

.report-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.report-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.report-lines {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: grid;
  gap: 8px;
}

.report-lines li {
  display: flex;
  align-items: baseline;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 8px;
}

.line-index {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
}

.line-text {
  flex: 1;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.report-lines a {
  flex: none;
  color: var(--accent);
  font-size: 12px;
  text-decoration: none;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

@media (max-width: 1280px) {
  .company-head {
    grid-template-columns: auto minmax(0, 1fr);
  }
}
</style>
