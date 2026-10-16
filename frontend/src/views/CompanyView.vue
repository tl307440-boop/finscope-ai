<script setup lang="ts">
/**
 * 公司研究 · 公司详情（/company/:symbol）。视觉目标：设计稿「公司详情」。
 *
 * 数据来源（全部只读）：
 *  - GET  /api/v1/instruments/search        路由 symbol → 证券元数据（id / CIK / 币种）
 *  - GET  /api/v1/companies/{id}/research   可选报告期 + 规则引擎结论
 *  - GET  /api/v1/sec/filings?cik=          最新一份申报（公司卡）
 *  - GET  /api/v1/market/bars/{id}          已入库日线：收盘、涨跌、K 线、区间收益率（window_returns）
 *  - GET  /api/v1/market/hotspots           合规公告流，按公司简称筛出本公司公告
 *  - GET  /api/v1/analysis/model-status     模型是否已配置
 *  - POST /api/v1/analysis/filing           生成财报解读（需登录）
 *  - GET  /api/v1/analysis/reports          已有研究记录（需登录）
 *  - GET/POST/DELETE /api/v1/watchlists     加入自选（需登录）
 *
 *  - GET  /api/v1/companies/{id}/financials 指标卡、趋势、对比表与规则读数（同比与比率由后端配对计算）
 *
 * 本页不计算任何财务数字，只做「亿」等单位缩放与配色；后端配不上可比口径时显示「—」。
 * 市值 / 股本类字段任何接口都没有，只用一句话说明，不填数字。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, formatAmount, formatMoment, listOf } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { fetchDataStatus, requestDataSync, type CompanyDataStatus } from "../api/companyData"
import { modelNotice } from "../api/modelStatus"
import {
  fetchFinancials,
  type CompanyFinancials,
  type FinanceTile,
  type TrendSeries,
} from "../api/companyFinancials"
import {
  parseReportBody,
  ruleChecks,
  ruleName,
  ruleStatusLabel,
  ruleTone,
  runStatusLabel,
  runTone,
} from "../api/research"
import {
  conceptLabel,
  defaultPeriods,
  factDisplay,
  formLabel,
  formatFact,
  isPerShare,
  shiftDays,
  todayText,
  unitLabel,
} from "../api/secFacts"
import { restoreSession, useSessionStore } from "../stores/auth"
import DataReadiness from "../components/ui/DataReadiness.vue"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MetricSnapshotPanel from "../components/ui/MetricSnapshotPanel.vue"
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

const EXCHANGE_LABELS: Record<string, string> = {
  SSE: "上交所",
  SZSE: "深交所",
  BSE: "北交所",
  HKEX: "港交所",
  NASDAQ: "纳斯达克",
  NYSE: "纽交所",
  AMEX: "美交所",
}

const MARKET_LABELS: Record<string, string> = { CN: "A股", US: "美股", HK: "港股" }
const TYPE_LABELS: Record<string, string> = { STOCK: "股票", INDEX: "指数", ETF: "ETF" }
const CURRENCY_LABELS: Record<string, string> = { CNY: "人民币", USD: "美元", HKD: "港元", TWD: "新台币", EUR: "欧元", JPY: "日元", GBP: "英镑" }
const BAR_SOURCES: Record<string, string> = { akshare: "AKShare", tushare: "Tushare" }

const SPARK_WIDTH = 72
const SPARK_HEIGHT = 28

const KLINE_RANGES = [
  { key: "22", label: "1月" },
  { key: "66", label: "3月" },
  { key: "130", label: "6月" },
  { key: "250", label: "1年" },
]

interface ResearchRule {
  ruleId: string
  status: string
  rationale: string
}

interface BarRow {
  tradeDate: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  closeText: string
  volume: number | null
  amount: number | null
  ma5: number | null
  ma20: number | null
  simpleReturn: number | null
}

interface FilingBrief {
  filingType: string
  fiscalYear: number | null
  fiscalPeriod: string
  filed: string
  status: string
}

interface Announcement {
  id: string
  title: string
  url: string
  typeLabel: string
  sourceBadge: string
  day: string
}

interface TileView {
  key: string
  label: string
  value: string | null
  unit: string
  period: string
  info: string
  delta: number | null
  deltaSuffix: string
  spark: number[]
  sparkKind: "bar" | "line"
}

interface TableView {
  key: string
  label: string
  unit: string
  cells: (string | null)[]
  yoy: number | null
  qoq: number | null
  deltaSuffix: string
}

interface RunDraft {
  runId: string
  status: string
  title: string
  message: string
  body: string
}

interface RecentReport {
  runId: string
  title: string
  asOf: string
}

type Phase = "loading" | "ready" | "error"
type LoadPhase = "idle" | "loading" | "ready" | "empty" | "error"

const symbol = ref(propText(props.symbol))
const asOf = ref(todayText())
const phase = ref<Phase>("loading")
const failure = ref("")
const instrument = ref<InstrumentHit | null>(null)

const periods = ref<string[]>([])
const periodA = ref("")
const periodB = ref("")
const periodMessage = ref("")
const rules = ref<ResearchRule[]>([])
const researchAsOf = ref("")

const bars = ref<BarRow[]>([])
/** 接口按最近 N 根日线算好的区间收益率（比例），键与 KLINE_RANGES 一致 */
const windowReturns = ref<Record<string, number>>({})
const barsPhase = ref<LoadPhase>("idle")
const barsSource = ref("")
const klineRange = ref("130")

const financials = ref<CompanyFinancials | null>(null)
const factsPhase = ref<LoadPhase>("idle")
const factsFailure = ref("")
const latestFiling = ref<FilingBrief | null>(null)

const announcements = ref<Announcement[]>([])
const announcementsPhase = ref<LoadPhase>("idle")

const trendMode = ref<"quarter" | "annual">("quarter")
const trendMetric = ref("revenue")
const tableMode = ref<"quarter" | "annual">("quarter")
const activeSection = ref("overview")

const modelLine = ref("正在确认模型状态")
const run = ref<RunDraft | null>(null)
const runMessage = ref("")
const generating = ref(false)
const copyState = ref("")
const recentReport = ref<RecentReport | null>(null)

const watchBusy = ref(false)
const watchMessage = ref("")
const watchGroupId = ref("")
const inWatchlist = ref(false)

const isCn = computed<boolean>(() => instrument.value?.market === "CN")
const hasSec = computed<boolean>(() => Boolean(instrument.value?.cik))
/** 有财务数据可看：美股走 SEC（有 CIK），A 股走东方财富 F10 报表。 */
const hasFinancials = computed<boolean>(() => hasSec.value || isCn.value)
const dataStatus = ref<CompanyDataStatus | null>(null)
const trackTimedOut = ref(false)
const companyName = computed<string>(() => instrument.value?.nameCn || instrument.value?.nameEn || symbol.value)

const heading = computed<string>(() => {
  const item = instrument.value
  const name = item?.nameCn || item?.nameEn || ""
  const code = item?.displaySymbol ?? symbol.value
  return name ? `${name} ${code}` : code
})

const exchangeText = computed<string>(() => labelOf(EXCHANGE_LABELS, instrument.value?.exchange ?? ""))
const marketText = computed<string>(() => labelOf(MARKET_LABELS, instrument.value?.market ?? ""))
const currencyText = computed<string>(() => labelOf(CURRENCY_LABELS, instrument.value?.currency ?? ""))
const priceUnit = computed<string>(() => (instrument.value?.currency === "CNY" ? "元" : currencyText.value))

const metaLine = computed<string>(() => {
  const item = instrument.value
  if (!item) {
    return "公司研究 · 公司详情"
  }
  return ["公司研究", exchangeText.value, marketText.value, item.nameEn].filter(Boolean).join(" · ")
})

const tags = computed<string[]>(() => {
  const item = instrument.value
  if (!item) {
    return []
  }
  return [labelOf(TYPE_LABELS, item.instrumentType), exchangeText.value, marketText.value].filter(Boolean)
})

const logoText = computed<string>(() => {
  const item = instrument.value
  const source = item?.nameEn || item?.nameCn || item?.displaySymbol || symbol.value
  return source.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "").slice(0, 4).toUpperCase() || "—"
})

/* ---------------- 行情 ---------------- */

const latestBar = computed<BarRow | null>(() => bars.value[bars.value.length - 1] ?? null)
/** 近 N 个交易日：含区间起点那根收盘，共 N+1 根，与 window_returns 同一口径 */
const rangeBars = computed<BarRow[]>(() => bars.value.slice(-(Number(klineRange.value) + 1)))
const dayChange = computed<number | null>(() => toPercent(latestBar.value?.simpleReturn ?? null))
const pricePoints = computed<number[]>(() =>
  bars.value.slice(-60).flatMap((bar) => (bar.close === null ? [] : [bar.close])),
)

const rangeStats = computed(() => {
  const rows = rangeBars.value
  const first = rows[0]
  const last = rows[rows.length - 1]
  const highs = rows.flatMap((row) => (row.high === null ? [] : [row.high]))
  const lows = rows.flatMap((row) => (row.low === null ? [] : [row.low]))
  return {
    from: first?.tradeDate ?? "",
    to: last?.tradeDate ?? "",
    change: toPercent(windowReturns.value[klineRange.value] ?? null),
    high: highs.length ? Math.max(...highs) : null,
    low: lows.length ? Math.min(...lows) : null,
  }
})

const quoteEmptyText = computed<string>(() => {
  if (barsPhase.value === "error") {
    return "行情读取失败，稍后可重试"
  }
  return isCn.value ? "暂无已同步的日线" : "暂未同步美股日线"
})

const klineOption = computed<EChartsOption | null>(() => {
  const rows = rangeBars.value
  if (rows.length < 2) {
    return null
  }
  const dates = rows.map((row) => row.tradeDate)
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["日K", "MA5", "MA20"], top: 0, right: 8 },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 58, right: 16, top: 30, height: "58%" },
      { left: 58, right: 16, top: "76%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLabel: { show: false } },
      { type: "category", gridIndex: 1, data: dates, boundaryGap: true },
    ],
    yAxis: [
      { type: "value", scale: true, splitNumber: 4 },
      { type: "value", gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: "日K",
        type: "candlestick",
        data: rows.map((row) => [row.open, row.close, row.low, row.high]),
      },
      { name: "MA5", type: "line", data: rows.map((row) => row.ma5), color: "#fbbf24", showSymbol: false },
      { name: "MA20", type: "line", data: rows.map((row) => row.ma20), color: "#a78bfa", showSymbol: false },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: rows.map((row) => ({
          value: row.volume,
          itemStyle: {
            color: (row.close ?? 0) >= (row.open ?? 0) ? "rgba(246,70,93,0.65)" : "rgba(31,195,125,0.65)",
          },
        })),
      },
    ],
  }
})

/* ---------------- 财务事实（数值与同比均来自 /financials） ---------------- */

/** 报告期：两个选择里较晚的是「本期」，较早的是「对比期」；后端返回实际采用的期末。 */
const referenceEnd = computed<string>(() => financials.value?.referenceEnd ?? "")
const compareEnd = computed<string>(() => financials.value?.compareEnd ?? "")
const deltaLabel = computed<string>(() => financials.value?.deltaLabel ?? "同比")

const periodOptions = computed<string[]>(() => [...periods.value].sort().reverse())

function tileInfo(tile: FinanceTile): string {
  const source = tile.source
  if (!source) {
    return tile.formula
  }
  const filing = `${formLabel(source.form)} · 申报于 ${source.filed}${source.accessionNo ? ` · 申报号 ${source.accessionNo}` : ""}`
  return tile.formula ? `${tile.formula}；${filing}` : filing
}

const metricTiles = computed<TileView[]>(() =>
  (financials.value?.tiles ?? []).map((tile) => {
    const shown =
      tile.kind === "ratio"
        ? { text: tile.value === null ? "—" : tile.value.toFixed(2), unit: "%" }
        : factDisplay(tile.value, tile.unit)
    return {
      key: tile.key,
      label: tile.label,
      value: tile.value === null ? null : shown.text,
      unit: shown.unit,
      period: tile.source ? `${tile.source.periodLabel} · 截至 ${tile.source.periodEnd}` : "所选报告期无数据",
      info: tileInfo(tile),
      delta: tile.delta,
      deltaSuffix: tile.deltaUnit,
      spark: tile.spark,
      sparkKind: tile.sparkKind,
    }
  }),
)

function sparkTone(delta: number | null): "up" | "down" | "accent" {
  if (delta === null) {
    return "accent"
  }
  return delta >= 0 ? "up" : "down"
}

/** MiniSpark 的柱子会画出 SVG 两端（首柱偏左、末柱偏右），这里按柱宽给外框留边再裁掉溢出。 */
function sparkFrame(tile: TileView): Record<string, string> {
  const count = Math.max(tile.spark.length, 1)
  const slot = SPARK_WIDTH / count
  if (tile.sparkKind === "bar") {
    return { paddingLeft: `${(slot * 0.15).toFixed(1)}px`, paddingRight: `${(slot * 0.45).toFixed(1)}px` }
  }
  return { paddingLeft: "2px", paddingRight: "5px" }
}

/** 金额按亿显示、每股数保持原值；只是单位缩放。 */
function displayUnit(unit: string): string {
  return isPerShare(unit) ? unitLabel(unit) : `亿${unitLabel(unit)}`
}

function scaled(value: number, unit: string): number {
  return Number((isPerShare(unit) ? value : value / 1e8).toFixed(2))
}

/* ---------------- 财务趋势 ---------------- */

const trendTabs = computed(() =>
  (financials.value?.trend[trendMode.value] ?? []).map((series) => ({ key: series.key, label: series.label })),
)

const activeTrendKey = computed<string>(() => {
  const keys = trendTabs.value.map((tab) => tab.key)
  return keys.includes(trendMetric.value) ? trendMetric.value : keys[0] ?? ""
})

const trendSeries = computed<TrendSeries | null>(
  () => financials.value?.trend[trendMode.value].find((series) => series.key === activeTrendKey.value) ?? null,
)

const trendOption = computed<EChartsOption | null>(() => {
  const series = trendSeries.value
  if (!series || series.points.length < 2) {
    return null
  }
  const unit = displayUnit(series.unit)
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: [`${series.label}（${unit}）`, "同比增速（右轴）"], top: 0, left: 0 },
    grid: { left: 64, right: 56, top: 40, bottom: 32 },
    xAxis: {
      type: "category",
      data: series.points.map((point) =>
        trendMode.value === "quarter" ? point.periodEnd.slice(0, 7) : `截至 ${point.periodEnd.slice(0, 7)}`,
      ),
    },
    yAxis: [
      { type: "value", name: unit },
      {
        type: "value",
        name: "%",
        splitLine: { show: false },
        axisLabel: { formatter: "{value}%" },
      },
    ],
    series: [
      {
        name: `${series.label}（${unit}）`,
        type: "bar",
        barMaxWidth: 28,
        itemStyle: { color: "#3b82f6", borderRadius: [3, 3, 0, 0] },
        data: series.points.map((point) => scaled(point.value, series.unit)),
      },
      {
        name: "同比增速（右轴）",
        type: "line",
        yAxisIndex: 1,
        color: "#fbbf24",
        symbol: "circle",
        symbolSize: 5,
        connectNulls: false,
        data: series.points.map((point) => point.yoy),
      },
    ],
  }
})

const trendEmptyMessage = computed<string>(() => {
  if (factsPhase.value === "error") {
    return factsFailure.value || "财务数据读取失败，稍后可重试"
  }
  if (!financials.value?.tiles.length) {
    return financials.value?.notice || "观察日之前还没有已申报的财务数据"
  }
  return trendMode.value === "quarter" ? "单季口径不足 2 期，可切换到年度查看" : "年度口径不足 2 期"
})

/* ---------------- 财务数据对比表 ---------------- */

const tableColumns = computed<string[]>(() => financials.value?.table[tableMode.value].columns ?? [])

const tableRows = computed<TableView[]>(() =>
  (financials.value?.table[tableMode.value].rows ?? []).map((row) => ({
    key: row.key,
    label: row.label,
    unit: row.kind === "ratio" ? "%" : displayUnit(row.unit),
    cells: row.cells.map((cell) =>
      cell === null ? null : row.kind === "ratio" ? cell.toFixed(2) : scaled(cell, row.unit).toFixed(2),
    ),
    yoy: row.yoy,
    qoq: row.qoq,
    deltaSuffix: row.deltaUnit,
  })),
)

const comparisonModeTabs = [
  { key: "quarter", label: "单季度" },
  { key: "annual", label: "年度" },
]

/* ---------------- 风险提示 ---------------- */

const ruleRows = computed(() =>
  rules.value.map((rule) => ({
    ...rule,
    name: ruleName(rule.ruleId),
    checks: ruleChecks(rule.ruleId),
    statusText: ruleStatusLabel(rule.status),
    tone: ruleTone(rule.status),
    observation: financials.value?.ruleObservations[rule.ruleId] ?? null,
  })),
)

const allUnevaluated = computed<boolean>(
  () => ruleRows.value.length > 0 && ruleRows.value.every((rule) => rule.status === "not_evaluated"),
)

/* ---------------- 页签 / 来源 ---------------- */

const sectionTabs = computed(() => {
  const filings = { key: "filings", label: "原始财报", to: `/company/${encodeURIComponent(symbol.value)}/filings` }
  if (!hasFinancials.value) {
    return [
      { key: "overview", label: "公司概况" },
      { key: "quote", label: "行情走势" },
      { key: "news", label: "最新公告" },
      { key: "finance", label: "财务总览" },
      { key: "valuation", label: "估值分析" },
      filings,
    ]
  }
  if (isCn.value) {
    return [
      { key: "overview", label: "公司概况" },
      { key: "quote", label: "行情走势" },
      { key: "finance", label: "财务总览" },
      { key: "trend", label: "财务趋势" },
      { key: "ai", label: "AI财报解读" },
      { key: "news", label: "最新公告" },
      { key: "valuation", label: "估值分析" },
    ]
  }
  return [
    { key: "overview", label: "公司概况" },
    { key: "finance", label: "财务总览" },
    { key: "trend", label: "财务趋势" },
    { key: "ai", label: "AI财报解读" },
    { key: "news", label: "最新公告" },
    { key: "valuation", label: "估值分析" },
    filings,
  ]
})

const keyValues = computed(() => {
  const item = instrument.value
  const rows: { label: string; value: string | null }[] = [
    { label: "交易所", value: exchangeText.value || null },
    { label: "市场", value: marketText.value || null },
    { label: "记账币种", value: currencyText.value || null },
  ]
  if (item?.cik) {
    rows.push({ label: "CIK 编号", value: item.cik })
  }
  const filing = latestFiling.value
  if (filing) {
    const period = [filing.fiscalYear ? `FY${filing.fiscalYear}` : "", filing.fiscalPeriod].filter(Boolean).join(" ")
    rows.push({ label: "最新财报", value: `${period}${filing.filingType ? `（${filing.filingType}）` : ""}` || null })
    rows.push({ label: "申报日期", value: filing.filed })
  }
  const bar = latestBar.value
  if (bar) {
    rows.push({ label: "最新交易日", value: bar.tradeDate })
    rows.push({ label: "成交额", value: bar.amount === null ? null : formatAmount(bar.amount, priceUnit.value) })
  }
  return rows
})

const recentReportLink = computed<string>(() =>
  recentReport.value
    ? `/company/${encodeURIComponent(symbol.value)}/analysis/${encodeURIComponent(recentReport.value.runId)}`
    : "",
)

const runLink = computed<string>(() =>
  run.value ? `/company/${encodeURIComponent(symbol.value)}/analysis/${encodeURIComponent(run.value.runId)}` : "",
)

const runMetrics = computed(() => {
  const parsed = parseReportBody(run.value?.body ?? "")
  return parsed.metrics.slice(0, 8).map((line) => ({
    key: `${line.concept}|${line.unit}`,
    label: conceptLabel(line.concept),
    earlier: formatFact(asNumber(line.earlier), line.unit),
    later: formatFact(asNumber(line.later), line.unit),
  }))
})

const runPeriods = computed(() => parseReportBody(run.value?.body ?? "").periods)

const sourceList = computed<string[]>(() => {
  const list = ["本站证券主数据"]
  if (bars.value.length) {
    list.push(`${labelOf(BAR_SOURCES, barsSource.value) || "已入库"} 日线`)
  }
  if (hasSec.value) {
    list.push("SEC EDGAR")
  }
  if (isCn.value && financials.value && financials.value.dataStatus === "eastmoney") {
    list.push("东方财富 F10 报表")
  }
  if (announcements.value.some((item) => item.sourceBadge === "巨潮资讯")) {
    list.push("巨潮资讯")
  }
  return list
})

const footerNote = computed<string>(() => {
  if (isCn.value) {
    return "A 股财务为东方财富 F10 披露报表（累计口径，同比按上年同期）；行情为收盘后日线，不复权"
  }
  if (!bars.value.length) {
    return "美股日线暂未同步；财务数据为 SEC 已申报口径，同比按上一年同期计算"
  }
  return "财务数据为 SEC 已申报口径，同比按上一年同期计算"
})

const watchLabel = computed<string>(() => (inWatchlist.value ? "已自选" : "加入自选"))

/* ---------------- 工具函数 ---------------- */

function propText(value: string | undefined): string {
  return typeof value === "string" && value ? value : ""
}

function labelOf(table: Record<string, string>, raw: string): string {
  return table[raw] ?? table[raw.toUpperCase()] ?? raw
}

function toPercent(ratio: number | null): number | null {
  return ratio === null ? null : Math.round(ratio * 10000) / 100
}

function parseRule(value: unknown): ResearchRule[] {
  const record = asRecord(value)
  const ruleId = asString(record?.rule_id)
  if (!ruleId) {
    return []
  }
  return [{ ruleId, status: asString(record?.status) ?? "", rationale: asString(record?.rationale) ?? "" }]
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
      open: asNumber(record?.open),
      high: asNumber(record?.high),
      low: asNumber(record?.low),
      close: asNumber(record?.close),
      closeText: close,
      volume: asNumber(record?.volume),
      amount: asNumber(record?.amount),
      ma5: asNumber(record?.ma5),
      ma20: asNumber(record?.ma20),
      simpleReturn: asNumber(record?.simple_return),
    },
  ]
}

function parseFiling(value: unknown): FilingBrief[] {
  const record = asRecord(value)
  const filed = asString(record?.filed)
  if (!filed) {
    return []
  }
  return [
    {
      filingType: asString(record?.filing_type) ?? "",
      fiscalYear: asNumber(record?.fiscal_year),
      fiscalPeriod: asString(record?.fiscal_period) ?? "",
      filed,
      status: asString(record?.status) ?? "",
    },
  ]
}

/** 公告流不带证券 id，标题以公司简称开头（「贵州茅台2026年半年度报告」「苹果：10-Q」）。 */
function parseAnnouncement(value: unknown, name: string): Announcement[] {
  const record = asRecord(value)
  const title = asString(record?.title)
  const id = asString(record?.id)
  if (!title || !id || !name || !title.startsWith(name)) {
    return []
  }
  const rest = title.slice(name.length).replace(/^[：:\s]+/, "") || title
  return [
    {
      id,
      title: formLabel(rest),
      url: asString(record?.canonical_url) ?? "",
      typeLabel: asString(record?.event_type_label) ?? "公告",
      sourceBadge: asString(record?.source_badge) ?? "",
      day: formatMoment(record?.published_at).slice(0, 10),
    },
  ]
}

/* ---------------- 加载 ---------------- */

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadCompany(): Promise<void> {
  stopTracking()
  dataStatus.value = null
  trackTimedOut.value = false
  phase.value = "loading"
  failure.value = ""
  rules.value = []
  financials.value = null
  factsPhase.value = "idle"
  bars.value = []
  windowReturns.value = {}
  barsPhase.value = "idle"
  latestFiling.value = null
  announcements.value = []
  run.value = null
  runMessage.value = ""
  recentReport.value = null
  const wanted = symbol.value.trim()
  if (!wanted) {
    phase.value = "error"
    failure.value = "地址里缺少证券代码"
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
      failure.value = found.emptyMessage ?? "没有找到这只证券"
      return
    }
    instrument.value = hit
    const tasks: Promise<void>[] = [loadBars(hit.id), loadAnnouncements()]
    if (hit.cik || hit.market === "CN") {
      tasks.push(loadResearch().then(loadFinancials), loadRecentReports())
    }
    if (hit.cik) {
      tasks.push(loadFilings(hit.cik))
    }
    await Promise.all(tasks)
    void loadWatchState(hit.id)
    phase.value = "ready"
    void trackData(hit, true)
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    phase.value = "error"
  }
}

async function fetchResearch(item: InstrumentHit, earlier: string, later: string): Promise<Record<string, unknown> | null> {
  const params = new URLSearchParams({ as_of: asOf.value })
  if (earlier && later && earlier !== later) {
    params.set("period_a", earlier)
    params.set("period_b", later)
  }
  const response = await fetch(apiUrl(`/api/v1/companies/${item.id}/research?${params.toString()}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const metaAsOf = asString(asRecord(asRecord(body)?.meta)?.as_of)
  researchAsOf.value = metaAsOf ? formatMoment(metaAsOf) : ""
  return dataOf(body)
}

/** loadResearch 自己回填报告期时为 true，报告期监听据此跳过一次，避免重复请求。 */
let backfillingPeriods = false

/** 报告期选择不在可选列表里（首次进入、换了观察日）时，回到「最新一期 vs 上年同期」。 */
async function loadResearch(): Promise<void> {
  const item = instrument.value
  if (!item) {
    return
  }
  periodMessage.value = ""
  try {
    let data = await fetchResearch(item, periodA.value, periodB.value)
    const available = Array.isArray(data?.available_periods)
      ? data.available_periods.filter((entry): entry is string => typeof entry === "string")
      : []
    periods.value = available
    const valid =
      available.includes(periodA.value) && available.includes(periodB.value) && periodA.value !== periodB.value
    if (!valid) {
      const fallback = defaultPeriods(available)
      if (fallback.earlier !== periodA.value || fallback.later !== periodB.value) {
        backfillingPeriods = true
      }
      periodA.value = fallback.earlier
      periodB.value = fallback.later
      if (fallback.earlier && fallback.later) {
        data = await fetchResearch(item, fallback.earlier, fallback.later)
      }
    }
    rules.value = [
      ...(Array.isArray(data?.risks) ? data.risks.flatMap(parseRule) : []),
      ...(Array.isArray(data?.highlights) ? data.highlights.flatMap(parseRule) : []),
      ...(Array.isArray(data?.unevaluated) ? data.unevaluated.flatMap(parseRule) : []),
    ]
  } catch (caught) {
    rules.value = []
    periodMessage.value = caught instanceof ApiError ? caught.message : "无法读取报告期"
  }
}

async function loadBars(instrumentId: string): Promise<void> {
  barsPhase.value = "loading"
  try {
    const params = new URLSearchParams({ limit: "251", as_of: asOf.value })
    const response = await fetch(apiUrl(`/api/v1/market/bars/${instrumentId}?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    bars.value = listOf(data?.bars).flatMap(parseBar)
    windowReturns.value = Object.fromEntries(
      Object.entries(asRecord(data?.window_returns) ?? {}).flatMap(([key, raw]) => {
        const value = asNumber(raw)
        return value === null ? [] : [[key, value]]
      }),
    )
    barsSource.value = asString(data?.source) ?? ""
    barsPhase.value = bars.value.length ? "ready" : "empty"
  } catch {
    bars.value = []
    windowReturns.value = {}
    barsPhase.value = "error"
  }
}

let financialsTicket = 0

async function loadFinancials(): Promise<void> {
  const item = instrument.value
  if (!item || !hasFinancials.value) {
    return
  }
  const ticket = ++financialsTicket
  const picked = [periodA.value, periodB.value].filter(Boolean).sort()
  factsPhase.value = "loading"
  factsFailure.value = ""
  try {
    const loaded = await fetchFinancials(item.id, {
      asOf: asOf.value,
      reference: picked[picked.length - 1],
      compare: picked.length === 2 ? picked[0] : undefined,
    })
    if (ticket !== financialsTicket) {
      return
    }
    financials.value = loaded
    factsPhase.value = loaded?.tiles.length ? "ready" : "empty"
  } catch (caught) {
    if (ticket !== financialsTicket) {
      return
    }
    financials.value = null
    factsFailure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    factsPhase.value = "error"
  }
}

async function loadFilings(targetCik: string): Promise<void> {
  try {
    const response = await fetch(apiUrl(`/api/v1/sec/filings?cik=${encodeURIComponent(targetCik)}`))
    const body = await readJson(response)
    if (!response.ok) {
      return
    }
    const rows = listOf(dataOf(body)?.filings)
      .flatMap(parseFiling)
      .filter((row) => !asOf.value || row.filed <= asOf.value)
    latestFiling.value = rows.sort((left, right) => left.filed.localeCompare(right.filed)).pop() ?? null
  } catch {
    latestFiling.value = null
  }
}

async function loadAnnouncements(): Promise<void> {
  const name = instrument.value?.nameCn ?? ""
  if (!name) {
    announcementsPhase.value = "empty"
    return
  }
  announcementsPhase.value = "loading"
  const params = new URLSearchParams({ start: shiftDays(asOf.value, -365), end: asOf.value })
  try {
    const response = await fetch(apiUrl(`/api/v1/market/hotspots?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    announcements.value = listOf(dataOf(body)?.events)
      .flatMap((event) => parseAnnouncement(event, name))
      .sort((left, right) => right.day.localeCompare(left.day))
      .slice(0, 8)
    announcementsPhase.value = announcements.value.length ? "ready" : "empty"
  } catch {
    announcements.value = []
    announcementsPhase.value = "error"
  }
}

async function loadRecentReports(): Promise<void> {
  if (!session.accessToken) {
    return
  }
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/reports",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    if (!response.ok) {
      return
    }
    const body = await readJson(response)
    const name = instrument.value?.nameCn ?? ""
    const hit = listOf(dataOf(body)?.reports)
      .flatMap((row) => {
        const runId = asString(row.run_id)
        const title = asString(row.title) ?? ""
        return runId && name && title.startsWith(name)
          ? [{ runId, title, asOf: asString(row.as_of) ?? "" }]
          : []
      })
      .sort((left, right) => right.asOf.localeCompare(left.asOf))[0]
    recentReport.value = hit ?? null
  } catch {
    // 历史记录只是一个入口，读不到就不显示。
  }
}

async function applyFilters(): Promise<void> {
  const item = instrument.value
  if (!item) {
    return
  }
  if (!hasFinancials.value) {
    await Promise.all([loadBars(item.id), loadAnnouncements()])
    return
  }
  if (periodA.value && periodA.value === periodB.value) {
    periodMessage.value = "请选择两个不同的报告期"
    return
  }
  await Promise.all([
    loadBars(item.id),
    loadResearch().then(loadFinancials),
    item.cik ? loadFilings(item.cik) : Promise.resolve(),
    loadAnnouncements(),
  ])
}

/* ---------------- 数据就绪度 ---------------- */

const POLL_MS = 3000
const POLL_LIMIT = 40
let trackTicket = 0

function stopTracking(): void {
  trackTicket += 1
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 抓取完成后重读页面数据；报告期清空，让「最新一期 vs 上年同期」落到新数据上。 */
async function reloadAfterSync(item: InstrumentHit): Promise<void> {
  periodA.value = ""
  periodB.value = ""
  const tasks: Promise<void>[] = [loadBars(item.id), loadAnnouncements()]
  if (item.cik || item.market === "CN") {
    tasks.push(loadResearch().then(loadFinancials))
  }
  if (item.cik) {
    tasks.push(loadFilings(item.cik))
  }
  await Promise.all(tasks)
}

/**
 * 读数据状态；已登录且缺数据 / 已过期时登记后台抓取，抓取期间每 3 秒轮询，
 * 完成后重读页面。状态只是辅助信息，读不到就不打扰主流程。
 */
async function trackData(item: InstrumentHit, request: boolean): Promise<void> {
  stopTracking()
  const ticket = trackTicket
  trackTimedOut.value = false
  try {
    let status = await fetchDataStatus(item.id)
    if (ticket !== trackTicket) {
      return
    }
    if (request && session.accessToken && status?.datasets.some((entry) => entry.canRequest)) {
      status = (await requestDataSync(item.id, session.accessToken, refreshToken)) ?? status
    }
    dataStatus.value = status
    let sawSyncing = Boolean(status?.syncing)
    let rounds = 0
    while (status?.syncing) {
      if (rounds >= POLL_LIMIT) {
        trackTimedOut.value = true
        return
      }
      rounds += 1
      await pause(POLL_MS)
      if (ticket !== trackTicket) {
        return
      }
      status = await fetchDataStatus(item.id)
      if (ticket !== trackTicket) {
        return
      }
      dataStatus.value = status
      sawSyncing = true
    }
    if (sawSyncing && ticket === trackTicket) {
      await reloadAfterSync(item)
    }
  } catch {
    // 状态接口不可用时页面照常展示已入库的数据。
  }
}

function requestNow(): void {
  const item = instrument.value
  if (item) {
    void trackData(item, true)
  }
}

/* ---------------- 自选 ---------------- */

async function ensureWatchGroup(): Promise<string> {
  if (watchGroupId.value) {
    return watchGroupId.value
  }
  const listResponse = await authorizedFetch("/api/v1/watchlists", { method: "GET" }, session.accessToken, refreshToken)
  const listBody = await readJson(listResponse)
  if (listResponse.ok) {
    const id = asString(listOf(dataOf(listBody)?.groups)[0]?.id) ?? ""
    if (id) {
      watchGroupId.value = id
      return id
    }
  }
  const createResponse = await authorizedFetch(
    "/api/v1/watchlists",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "我的自选" }) },
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
  watchMessage.value = asString(asRecord(asRecord(createBody)?.error)?.message) ?? "无法准备自选分组"
  return ""
}

async function loadWatchState(instrumentId: string): Promise<void> {
  if (!session.accessToken) {
    return
  }
  try {
    const response = await authorizedFetch("/api/v1/watchlists", { method: "GET" }, session.accessToken, refreshToken)
    if (!response.ok) {
      return
    }
    const group = listOf(dataOf(await readJson(response))?.groups)[0]
    watchGroupId.value = asString(group?.id) ?? ""
    inWatchlist.value = listOf(group?.items).some((item) => asString(item.instrument_id) === instrumentId)
  } catch {
    // 自选状态只影响按钮文案，读不到就保持原样。
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
    const removing = inWatchlist.value
    const response = removing
      ? await authorizedFetch(
          `/api/v1/watchlists/${groupId}/items/${encodeURIComponent(item.id)}`,
          { method: "DELETE" },
          session.accessToken,
          refreshToken,
        )
      : await authorizedFetch(
          `/api/v1/watchlists/${groupId}/items`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ instrument_id: item.id }) },
          session.accessToken,
          refreshToken,
        )
    const payload = await readJson(response)
    if (!response.ok) {
      watchMessage.value =
        asString(asRecord(asRecord(payload)?.error)?.message) ?? (removing ? "无法移出自选" : "无法加入自选")
      return
    }
    inWatchlist.value = !removing
    watchMessage.value = removing ? "已移出自选" : "已加入自选"
  } catch {
    watchMessage.value = "无法连接服务器"
  } finally {
    watchBusy.value = false
  }
}

/* ---------------- 财报解读 ---------------- */

async function generateReport(): Promise<void> {
  runMessage.value = ""
  copyState.value = ""
  const item = instrument.value
  if (!session.accessToken) {
    runMessage.value = "请先登录后再生成"
    return
  }
  if (!item || !compareEnd.value) {
    runMessage.value = "请在页头选择两个不同的报告期"
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
          period_a: compareEnd.value,
          period_b: referenceEnd.value,
        }),
      },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const data = dataOf(payload)
    const runId = asString(data?.run_id)
    if (!response.ok || !runId) {
      runMessage.value = asString(asRecord(asRecord(payload)?.error)?.message) ?? "生成失败，请稍后重试"
      return
    }
    run.value = {
      runId,
      status: asString(data?.status) ?? "",
      title: asString(data?.title) ?? "研究记录",
      message: asString(data?.message) ?? "",
      body: typeof data?.body === "string" ? data.body : "",
    }
  } catch {
    runMessage.value = "无法连接服务器"
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
    copyState.value = "浏览器不允许访问剪贴板，请在完整报告页手动复制"
  }
}

async function loadModel(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/analysis/model-status"))
    const data = asRecord(asRecord(await readJson(response))?.data)
    modelLine.value = modelNotice(asString(data?.message), data?.available === true)
  } catch {
    modelLine.value = modelNotice(null, false)
  }
}

function selectSection(key: string): void {
  activeSection.value = key
  document.getElementById(`sec-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" })
}

function openFilings(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}/filings`)
}

onMounted(() => {
  void loadCompany()
  void loadModel()
})

onBeforeUnmount(stopTracking)

watch([periodA, periodB], ([earlier, later]) => {
  if (backfillingPeriods) {
    backfillingPeriods = false
    return
  }
  if (phase.value !== "ready" || !earlier || !later || earlier === later) {
    return
  }
  void loadResearch().then(loadFinancials)
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
        <button class="ghost-btn" type="button" :disabled="watchBusy" @click="toggleWatch">
          {{ watchLabel }}
        </button>
        <button class="ghost-btn" type="button" @click="openFilings">原始财报</button>
      </template>
      <template v-if="hasFinancials" #filters>
        <label class="filter-label" for="company-as-of">观察日</label>
        <el-input id="company-as-of" v-model="asOf" type="date" size="small" class="filter-date" />
        <span class="filter-label">对比</span>
        <el-select v-model="periodA" size="small" placeholder="对比期" class="filter-period" aria-label="对比期">
          <el-option v-for="period in periodOptions" :key="`a-${period}`" :label="period" :value="period" />
        </el-select>
        <span class="filter-label" aria-hidden="true">→</span>
        <el-select v-model="periodB" size="small" placeholder="报告期" class="filter-period" aria-label="报告期">
          <el-option v-for="period in periodOptions" :key="`b-${period}`" :label="period" :value="period" />
        </el-select>
        <el-button size="small" type="primary" @click="applyFilters">应用</el-button>
      </template>
    </PageHeader>

    <p v-if="watchMessage || periodMessage" class="muted" role="status">{{ watchMessage || periodMessage }}</p>

    <DataReadiness
      v-if="phase === 'ready'"
      :status="dataStatus"
      :signed-in="Boolean(session.accessToken)"
      :timed-out="trackTimedOut"
      @request="requestNow"
    />

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取公司信息"
      :message="failure"
      @retry="loadCompany"
    />

    <template v-else>
      <PanelCard id="sec-overview">
        <div class="company-head">
          <div class="logo" aria-hidden="true">{{ logoText }}</div>
          <div class="ident">
            <div class="name-row">
              <h2>{{ companyName }}</h2>
              <span class="code">{{ instrument?.displaySymbol }}</span>
              <StatusPill v-for="tag in tags" :key="tag" :text="tag" tone="muted" />
            </div>
            <p class="full-name">{{ instrument?.nameEn || instrument?.nameCn || "—" }}</p>
            <p class="meta-line">
              {{ exchangeText || "—" }} <i aria-hidden="true">›</i> {{ marketText || "—" }}
              <span class="muted">· {{ currencyText || "—" }}计价</span>
            </p>
          </div>

          <div class="quote">
            <template v-if="latestBar">
              <p class="price num">
                {{ latestBar.closeText }}<small> {{ priceUnit }}</small>
              </p>
              <p class="quote-delta">
                <DeltaValue :value="dayChange" suffix="%" />
                <span class="muted">{{ latestBar.tradeDate }} 收盘</span>
              </p>
              <MiniSpark :points="pricePoints" kind="area" tone="accent" :width="200" :height="36" />
            </template>
            <div v-else class="quote-empty">
              <p class="price dim">—</p>
              <p class="muted">{{ barsPhase === "loading" ? "正在读取行情" : quoteEmptyText }}</p>
            </div>
          </div>

          <dl class="key-values">
            <div v-for="row in keyValues" :key="row.label" class="kv">
              <dt>{{ row.label }}</dt>
              <dd :class="{ dim: !row.value }">{{ row.value ?? "—" }}</dd>
            </div>
          </dl>
        </div>
        <p class="footnote">市值、股本类指标需要行情与股本数据，暂未提供。</p>
      </PanelCard>

      <div class="tabs-bar">
        <SectionTabs :tabs="sectionTabs" :model-value="activeSection" @update:model-value="selectSection" />
      </div>

      <div v-if="bars.length" class="grid-2-1 pair">
        <PanelCard
          id="sec-quote"
          title="行情走势"
          more-text="完整行情"
          :more-to="`/market/stocks/${encodeURIComponent(instrument?.displaySymbol ?? symbol)}`"
        >
          <template #icon>◷</template>
          <template #actions>
            <SectionTabs :tabs="KLINE_RANGES" :model-value="klineRange" class="mini-tabs" @update:model-value="klineRange = $event" />
          </template>
          <div class="quote-stats">
            <div>
              <span>最新收盘</span>
              <strong class="num">{{ latestBar?.closeText ?? "—" }}</strong>
            </div>
            <div>
              <span>当日涨跌</span>
              <DeltaValue :value="dayChange" suffix="%" size="lg" />
            </div>
            <div>
              <span>区间涨跌</span>
              <DeltaValue :value="rangeStats.change" suffix="%" size="lg" />
            </div>
            <div>
              <span>区间最高</span>
              <strong class="num">{{ rangeStats.high?.toFixed(2) ?? "—" }}</strong>
            </div>
            <div>
              <span>区间最低</span>
              <strong class="num">{{ rangeStats.low?.toFixed(2) ?? "—" }}</strong>
            </div>
          </div>
          <VChart v-if="klineOption" class="kline-chart" :option="klineOption" autoresize aria-label="日 K 线" />
          <p class="footnote">{{ rangeStats.from }} 至 {{ rangeStats.to }} · 日线不复权 · 量单位：手</p>
        </PanelCard>

        <PanelCard id="sec-news" title="最新公告" :more-text="hasSec ? '' : '原始财报'" :more-to="hasSec ? '' : `/company/${encodeURIComponent(symbol)}/filings`">
          <template #icon>▤</template>
          <StateBlock v-if="announcementsPhase === 'loading'" state="loading" :rows="4" />
          <p v-else-if="!announcements.length" class="empty-line">
            {{ announcementsPhase === "error" ? "公告读取失败，稍后可重试" : "近一年暂无收录的公司公告" }}
          </p>
          <ul v-else class="news-list">
            <li v-for="item in announcements" :key="item.id">
              <span class="news-day num">{{ item.day }}</span>
              <StatusPill :text="item.typeLabel" tone="accent" />
              <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer noopener">{{ item.title }}</a>
              <span v-else>{{ item.title }}</span>
            </li>
          </ul>
        </PanelCard>
      </div>

      <template v-if="hasFinancials">
        <div class="grid-2-1 pair">
          <PanelCard id="sec-finance" title="核心财务指标" info="按所选报告期取数；单季口径优先，公司未单独披露单季时用累计口径并在卡片上注明">
            <template #icon>▤</template>
            <template #actions>
              <span class="muted">
                报告期 {{ referenceEnd || "—" }}<template v-if="compareEnd"> · 对比 {{ compareEnd }}</template>
              </span>
            </template>
            <StateBlock v-if="factsPhase === 'loading'" state="loading" :rows="3" />
            <p v-else-if="!metricTiles.length" class="empty-line">{{ trendEmptyMessage }}</p>
            <div v-else class="grid-3 tiles">
              <MetricTile
                v-for="tile in metricTiles"
                :key="tile.key"
                :label="tile.label"
                :value="tile.value"
                :unit="tile.unit"
                :period="tile.period"
                :info="tile.info"
                :delta-label="deltaLabel"
              >
                <template #delta>
                  <DeltaValue :value="tile.delta" :suffix="tile.deltaSuffix" size="sm" />
                </template>
                <template v-if="tile.spark.length >= 2" #chart>
                  <span class="spark-frame" :style="sparkFrame(tile)">
                    <MiniSpark
                      :points="tile.spark"
                      :kind="tile.sparkKind"
                      :tone="sparkTone(tile.delta)"
                      :width="SPARK_WIDTH"
                      :height="SPARK_HEIGHT"
                    />
                  </span>
                </template>
              </MetricTile>
            </div>
          </PanelCard>

          <PanelCard id="sec-risks" title="风险提示" more-text="原始财报" :more-to="`/company/${encodeURIComponent(symbol)}/filings`">
            <template #icon>⚠</template>
            <p v-if="!ruleRows.length" class="empty-line">所选报告期暂无规则检查结果</p>
            <template v-else>
              <p v-if="allUnevaluated" class="rule-note">
                规则引擎尚未对这两期给出判断。下方读数由服务端按已申报数据配对计算，仅供参考。
              </p>
              <ul class="rule-list">
                <li v-for="rule in ruleRows" :key="rule.ruleId" :title="rule.checks">
                  <div class="rule-head">
                    <strong>{{ rule.name }}</strong>
                    <StatusPill :text="rule.statusText" :tone="rule.tone" />
                  </div>
                  <p class="rule-obs" :class="{ dim: !rule.observation }">
                    {{ rule.observation ?? rule.rationale ?? "—" }}
                  </p>
                </li>
              </ul>
            </template>
          </PanelCard>

          <MetricSnapshotPanel v-if="instrument" :instrument-id="instrument.id" />
        </div>

        <div class="grid-2-1 pair">
          <div class="stack">
            <PanelCard id="sec-trend" title="财务趋势" info="柱为已申报数值，折线为与上一年同期相比的增速">
              <template #icon>◔</template>
              <template #actions>
                <SectionTabs :tabs="comparisonModeTabs" :model-value="trendMode" class="mini-tabs" @update:model-value="trendMode = $event as 'quarter' | 'annual'" />
              </template>
              <SectionTabs v-if="trendTabs.length" :tabs="trendTabs" :model-value="activeTrendKey" class="metric-tabs" @update:model-value="trendMetric = $event" />
              <StateBlock v-if="factsPhase === 'loading'" state="loading" :rows="3" />
              <p v-else-if="!trendOption" class="empty-line">{{ trendEmptyMessage }}</p>
              <VChart v-else class="trend-chart" :option="trendOption" autoresize aria-label="财务趋势" />
              <p v-if="trendOption && trendMode === 'quarter'" class="footnote">
                单季口径只含公司单独披露的季度；第四财季通常只在年报中给出全年数，可切换到「年度」查看。
              </p>
            </PanelCard>

            <PanelCard id="sec-compare" title="财务数据对比" info="同比 = 与上一年同期相比；环比 = 与上一季度相比；比率类指标的变化以百分点（pct）表示">
              <template #icon>▦</template>
              <template #actions>
                <SectionTabs :tabs="comparisonModeTabs" :model-value="tableMode" class="mini-tabs" @update:model-value="tableMode = $event as 'quarter' | 'annual'" />
              </template>
              <StateBlock v-if="factsPhase === 'loading'" state="loading" :rows="3" />
              <p v-else-if="!tableRows.length" class="empty-line">{{ trendEmptyMessage }}</p>
              <el-table v-else :data="tableRows" size="small" class="tabular compare-table">
                <el-table-column prop="label" label="指标" min-width="96" fixed />
                <el-table-column prop="unit" label="单位" width="68" />
                <el-table-column v-for="(column, index) in tableColumns" :key="column" :label="column" min-width="90" align="right">
                  <template #default="scope">{{ scope.row.cells[index] ?? "—" }}</template>
                </el-table-column>
                <el-table-column label="同比" width="84" align="right">
                  <template #default="scope">
                    <DeltaValue :value="scope.row.yoy" :suffix="scope.row.deltaSuffix" size="sm" />
                  </template>
                </el-table-column>
                <el-table-column v-if="tableMode === 'quarter'" label="环比" width="84" align="right">
                  <template #default="scope">
                    <DeltaValue :value="scope.row.qoq" :suffix="scope.row.deltaSuffix" size="sm" />
                  </template>
                </el-table-column>
              </el-table>
            </PanelCard>
          </div>

          <div class="stack">
            <PanelCard id="sec-ai" title="AI 财报解读" :info="modelLine">
              <template #icon>✦</template>
              <template #actions>
                <el-button
                  v-if="session.accessToken"
                  size="small"
                  type="primary"
                  :loading="generating"
                  :disabled="!compareEnd"
                  @click="generateReport"
                >
                  {{ run ? "重新生成" : "生成解读" }}
                </el-button>
              </template>

              <div class="ai-brief">
                <p class="ai-scope">
                  对比 <strong class="num">{{ compareEnd || "—" }}</strong> → <strong class="num">{{ referenceEnd || "—" }}</strong>
                </p>
                <p class="muted">{{ modelLine }}。当前解读由规则整理已申报数字，不含模型生成的观点。</p>
              </div>

              <StateBlock
                v-if="!session.accessToken"
                state="denied"
                title="登录后可生成财报解读"
                message="解读会保存到你的研究记录中"
              >
                <router-link class="link" :to="`/login?redirect=${encodeURIComponent(`/company/${symbol}`)}`">去登录 ›</router-link>
              </StateBlock>

              <template v-else-if="run">
                <div class="report-head">
                  <h3>{{ run.title }}</h3>
                  <StatusPill :text="runStatusLabel(run.status)" :tone="runTone(run.status)" dot />
                </div>
                <p v-if="runPeriods" class="muted">报告期 {{ runPeriods.earlier }} 与 {{ runPeriods.later }}</p>
                <ul class="run-metrics">
                  <li v-for="row in runMetrics" :key="row.key">
                    <span>{{ row.label }}</span>
                    <span class="num">{{ row.earlier }}</span>
                    <i aria-hidden="true">→</i>
                    <span class="num strong">{{ row.later }}</span>
                  </li>
                </ul>
                <p v-if="run.message" class="muted">{{ run.message }}</p>
                <div class="action-row">
                  <router-link class="ghost-btn" :to="runLink">打开完整报告</router-link>
                  <button class="ghost-btn" type="button" @click="copyReport">复制正文</button>
                </div>
                <p v-if="copyState" class="muted" role="status">{{ copyState }}</p>
              </template>

              <template v-else>
                <p class="empty-line">选好报告期后点「生成解读」，整理两期已申报数字并保存为研究记录。</p>
                <p v-if="recentReport" class="recent">
                  最近一次：{{ recentReport.title }} · {{ recentReport.asOf }}
                  <router-link class="link" :to="recentReportLink">打开 ›</router-link>
                </p>
              </template>
              <p v-if="runMessage" class="muted" role="status">{{ runMessage }}</p>
            </PanelCard>

            <PanelCard v-if="!bars.length" id="sec-news" title="最新公告">
              <template #icon>▤</template>
              <StateBlock v-if="announcementsPhase === 'loading'" state="loading" :rows="3" />
              <p v-else-if="!announcements.length" class="empty-line">
                {{ announcementsPhase === "error" ? "公告读取失败，稍后可重试" : "近一年暂无收录的公司公告" }}
              </p>
              <ul v-else class="news-list">
                <li v-for="item in announcements" :key="item.id">
                  <span class="news-day num">{{ item.day }}</span>
                  <StatusPill :text="item.typeLabel" tone="accent" />
                  <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer noopener">{{ item.title }}</a>
                  <span v-else>{{ item.title }}</span>
                </li>
              </ul>
            </PanelCard>
          </div>
        </div>
      </template>

      <PanelCard v-else id="sec-finance" title="财务数据" compact>
        <template #icon>▤</template>
        <StateBlock
          state="empty"
          title="A 股财报数据暂未接入"
          message="暂无利润表、资产负债表和现金流数据，也暂不能生成财报解读；定期报告原文可在「最新公告」中打开。"
        />
      </PanelCard>

      <PanelCard id="sec-valuation" title="估值分析" compact>
        <template #icon>◎</template>
        <p class="empty-line">PE、PB、PS 需要总市值或总股本，目前只有日线价格与财报科目，暂不计算估值倍数。</p>
      </PanelCard>

      <SourceFooter
        :as-of="researchAsOf || latestBar?.tradeDate || asOf"
        :sources="sourceList"
        :latency="bars.length ? '收盘后更新' : ''"
        :stale="barsPhase === 'error' || factsPhase === 'error'"
        :note="footerNote"
      />
    </template>
  </main>
</template>

<style scoped>
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

.ghost-btn {
  appearance: none;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-soft);
  color: var(--text);
  font-size: 13px;
  padding: 5px 12px;
  cursor: pointer;
  text-decoration: none;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}

.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.filter-label {
  color: var(--muted);
  font-size: 13px;
}

.filter-date {
  width: 150px;
}

.filter-period {
  width: 132px;
}

.company-head {
  display: grid;
  grid-template-columns: auto minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1.2fr);
  gap: 22px;
  align-items: start;
}

.logo {
  width: 64px;
  height: 64px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: linear-gradient(160deg, var(--panel-raise), var(--panel-deep));
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
  font-size: 22px;
  font-weight: 700;
  color: var(--text-strong);
}

.code {
  color: var(--muted);
  font-family: var(--font-num);
  font-size: 14px;
}

.full-name {
  margin: 8px 0 4px;
  font-size: 14px;
  color: var(--text);
}

.meta-line {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.quote {
  display: grid;
  gap: 6px;
  align-content: start;
  border-left: 1px solid var(--line-soft);
  padding-left: 22px;
}

.price {
  margin: 0;
  font-family: var(--font-num);
  font-size: 32px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--text-strong);
}

.price.dim {
  color: var(--faint);
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
  gap: 10px;
  margin: 0;
  font-size: 13px;
}

.quote-empty p {
  margin: 0 0 4px;
}

.key-values {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 18px;
  margin: 0;
  border-left: 1px solid var(--line-soft);
  padding-left: 22px;
}

.kv {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 5px;
  min-width: 0;
}

.kv dt {
  color: var(--muted);
  font-size: 13px;
  white-space: nowrap;
}

.kv dd {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kv dd.dim {
  color: var(--muted);
  font-weight: 500;
}

.footnote {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.tabs-bar {
  border-bottom: 1px solid var(--line);
  padding: 0 4px 10px;
}

.mini-tabs :deep(.tab) {
  padding: 3px 10px;
  margin-bottom: 0;
  font-size: 13px;
  border-bottom-width: 0;
  border-radius: 6px;
}

.mini-tabs :deep(.tab.active) {
  background: var(--accent-soft);
}

.metric-tabs {
  margin-bottom: 8px;
}

.metric-tabs :deep(.tab) {
  margin-bottom: 0;
  font-size: 13px;
  padding: 4px 12px;
}

.empty-line {
  margin: 0;
  max-width: 46em;
  padding: 18px 4px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.quote-stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 6px;
}

.quote-stats div {
  display: grid;
  gap: 4px;
}

.quote-stats span {
  color: var(--muted);
  font-size: 13px;
}

.quote-stats strong {
  font-size: 17px;
  font-weight: 600;
  color: var(--text-strong);
}

.kline-chart {
  width: 100%;
  height: 340px;
}

.news-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}

.news-list li {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.news-list li:last-child {
  border-bottom: 0;
}

.news-day {
  color: var(--muted);
}

.news-list a,
.news-list span:last-child {
  color: var(--text);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.news-list a:hover {
  color: var(--accent);
}

.tiles {
  gap: 12px;
}

.spark-frame {
  display: inline-block;
  overflow: hidden;
  line-height: 0;
}

.rule-note {
  margin: 0 0 10px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.rule-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}

.rule-list li {
  display: grid;
  gap: 4px;
  padding: 9px 2px;
  border-bottom: 1px solid var(--line-soft);
}

.rule-list li:last-child {
  border-bottom: 0;
}

.rule-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 14px;
}

.rule-head strong {
  font-weight: 600;
  color: var(--text-strong);
}

.rule-obs {
  margin: 0;
  font-family: var(--font-num);
  font-size: 13px;
  color: var(--text);
}

.rule-obs.dim {
  color: var(--muted);
  font-family: inherit;
}

.trend-chart {
  width: 100%;
  height: 300px;
}

.compare-table {
  width: 100%;
}

.ai-brief {
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  padding: 10px 12px;
  margin-bottom: 12px;
}

.ai-brief p {
  margin: 0;
  line-height: 1.6;
}

.ai-scope {
  font-size: 14px;
  color: var(--text);
  margin-bottom: 4px !important;
}

.ai-scope strong {
  color: var(--text-strong);
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

.run-metrics {
  list-style: none;
  margin: 10px 0;
  padding: 0;
  display: grid;
}

.run-metrics li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.run-metrics .num {
  color: var(--muted);
  text-align: right;
}

.run-metrics .strong {
  color: var(--text-strong);
}

.run-metrics i {
  color: var(--faint);
  font-style: normal;
}

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}

.recent {
  margin: 0;
  font-size: 13px;
  color: var(--text);
}

.link {
  color: var(--accent);
  text-decoration: none;
  font-size: 13px;
}

@media (max-width: 1400px) {
  .company-head {
    grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr);
  }

  .key-values {
    grid-column: 1 / -1;
    border-left: 0;
    padding-left: 0;
  }
}
</style>
