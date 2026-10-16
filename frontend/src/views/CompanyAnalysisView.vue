<script setup lang="ts">
/**
 * AI 财报解读（/company/:symbol/analysis/:runId）。视觉目标：设计稿「AI 财报解读」。
 *
 *  - GET  /api/v1/analysis/runs/{run_id}   研究记录正文（需 Bearer），页面主体
 *  - GET  /api/v1/analysis/model-status    模型是否已配置（规则生成时必须显式提示）
 *  - POST /api/v1/analysis/filing          按正文里的报告期重新生成（需 Bearer）
 *  - GET  /api/v1/instruments/search       路由 symbol → 证券元数据（id / CIK / 币种）
 *  - GET  /api/v1/companies/{id}/research  规则引擎结论
 *  - GET  /api/v1/companies/{id}/financials 规则读数、年度现金流与利润（后端已算好）
 *  - GET  /api/v1/sec/facts?cik=&as_of=    已申报事实：只用来标注两期是单季还是累计
 *  - GET  /api/v1/sec/filings?cik=         最新财报与申报日（抬头条）
 *  - GET  /api/v1/market/bars/{id}         最新收盘与涨跌
 *
 * 正文是规则整理的纯文本行，用 parseReportBody 拆成报告期 + 指标行；
 * 正文没有的段落（模型观点、管理层表述）走空态，不补写内容。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, formatMoment, listOf } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { fetchFinancials, type CompanyFinancials } from "../api/companyFinancials"
import {
  parseReportBody,
  ruleChecks,
  ruleName,
  ruleStatusLabel,
  ruleTone,
  runStatusLabel,
  runTone,
  type ReportMetricLine,
} from "../api/research"
import {
  conceptLabel,
  formatFact,
  METRICS,
  parseFactPoint,
  periodKindLabel,
  sameShape,
  todayText,
  type FactPoint,
} from "../api/secFacts"
import { restoreSession, useSessionStore } from "../stores/auth"
import DeltaValue from "../components/ui/DeltaValue.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const props = defineProps<{ symbol: string; runId: string }>()
const session = useSessionStore()
const router = useRouter()

const NO_DOCUMENT = "没有可引用的上传文档。"
/** 重新生成与口径标注用的观察日：今天。 */
const AUX_AS_OF = todayText()

interface RunReport {
  runId: string
  reportId: string
  status: string
  title: string
  message: string
  body: string
  notice: string
  /** 报告生成时的观察日；规则读数和现金流对比都按它取数 */
  asOf: string
}

interface RuleLine {
  ruleId: string
  status: string
  rationale: string
}

interface DocEvidence {
  documentId: string
  pageStart: string
  pageEnd: string
  snippet: string
}

interface FilingBrief {
  filingType: string
  fiscalYear: number | null
  fiscalPeriod: string
  filed: string
}

interface ComparedLine {
  key: string
  label: string
  earlier: string
  later: string
  change: number | null
  comparable: boolean
  basis: string
  accession: string
  source: string
}

type Phase = "loading" | "ready" | "error"
type AuxPhase = "idle" | "loading" | "ready" | "empty" | "error" | "unavailable"

/** 指标在报告里的展示顺序；不在表里的排在后面。 */
const ORDER = [
  "revenue",
  "grossProfit",
  "operatingIncome",
  "netIncome",
  "epsDiluted",
  "operatingCashFlow",
  "rnd",
  "cost",
  "assets",
  "liabilities",
  "equity",
  "currentAssets",
  "currentLiabilities",
  "receivables",
  "inventory",
  "cash",
]

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
const report = ref<RunReport | null>(null)
const instrument = ref<InstrumentHit | null>(null)
const modelAvailable = ref<boolean | null>(null)
const modelMessage = ref("")
const generatedAt = ref("")
const copyState = ref("")
const exportState = ref("")
const regenerating = ref(false)
const regenerateMessage = ref("")

const rules = ref<RuleLine[]>([])
const researchPhase = ref<AuxPhase>("idle")
const facts = ref<FactPoint[]>([])
const factsPhase = ref<AuxPhase>("idle")
const financials = ref<CompanyFinancials | null>(null)
const financialsPhase = ref<AuxPhase>("idle")
const latestFiling = ref<FilingBrief | null>(null)
const bars = ref<{ tradeDate: string; closeText: string; simpleReturn: number | null }[]>([])
const barsPhase = ref<AuxPhase>("idle")

const evidenceTab = ref("facts")

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const runId = computed<string>(() => (Array.isArray(props.runId) ? props.runId[0] ?? "" : props.runId))
const bodyText = computed<string>(() => report.value?.body ?? "")
const reportAsOf = computed<string>(() => report.value?.asOf || AUX_AS_OF)
const parsed = computed(() => parseReportBody(bodyText.value))
const currencyUnit = computed<string>(() => (instrument.value?.currency === "CNY" ? "元" : instrument.value?.currency === "USD" ? "美元" : ""))

const heading = computed<string>(() => {
  const item = instrument.value
  const name = item?.nameCn || item?.nameEn || ""
  const code = item?.displaySymbol ?? symbol.value
  return name ? `${name} ${code}` : report.value?.title ?? "AI 财报解读"
})

const logoText = computed<string>(() => {
  const item = instrument.value
  const source = item?.nameEn || item?.nameCn || item?.displaySymbol || symbol.value
  return source.replace(/[^A-Za-z0-9\u4e00-\u9fa5]/g, "").slice(0, 4).toUpperCase() || "—"
})

const ruleBasedNotice = computed<string>(() => {
  if (modelAvailable.value === false) {
    return "未配置大模型：本报告由规则整理两期已申报数字，不含模型生成的观点。"
  }
  if (modelAvailable.value === null) {
    return "未能确认模型状态，请以报告内的数据来源为准。"
  }
  return modelMessage.value || "模型已配置，结论请与数据来源核对。"
})

const generatedMode = computed<string>(() => {
  if (modelAvailable.value === false) {
    return "规则整理"
  }
  return modelAvailable.value ? "模型生成" : "—"
})

/** 正文里的文档引用行：`文档 {id} 第 {起}-{止} 页 {摘录}`。 */
const docEvidence = computed<DocEvidence[]>(() =>
  parsed.value.lines.flatMap((line) => {
    const match = line.match(/^文档 (\S+) 第 (\d+)-(\d+) 页 (.+)$/)
    if (!match) {
      return []
    }
    return [{ documentId: match[1] ?? "", pageStart: match[2] ?? "", pageEnd: match[3] ?? "", snippet: match[4] ?? "" }]
  }),
)

const noDocumentCited = computed<boolean>(() => bodyText.value.includes(NO_DOCUMENT))

const changeLabel = computed<string>(() => financials.value?.deltaLabel || "较对比期")

const periodsText = computed<string>(() => {
  const range = parsed.value.periods
  return range ? `${range.earlier} → ${range.later}` : "—"
})

function metricOrder(concept: string): number {
  const short = concept.replace(/^[A-Za-z-]+:/, "")
  const index = ORDER.findIndex((key) => METRICS[key]?.concepts.includes(short))
  return index < 0 ? ORDER.length : index
}

/** 用已申报事实核对报告里两期数字的期间长度：长度不同（单季 vs 累计）就不算变化率。 */
function locate(line: ReportMetricLine, end: string, raw: string | null): FactPoint | null {
  const value = asNumber(raw)
  if (value === null) {
    return null
  }
  return (
    facts.value.find(
      (point) => point.concept === line.concept && point.unit === line.unit && point.periodEnd === end && point.value === value,
    ) ?? null
  )
}

const comparedLines = computed<ComparedLine[]>(() => {
  const range = parsed.value.periods
  return [...parsed.value.metrics]
    .sort((left, right) => metricOrder(left.concept) - metricOrder(right.concept))
    .map((line) => {
      const earlier = range ? locate(line, range.earlier, line.earlier) : null
      const later = range ? locate(line, range.later, line.later) : null
      const sameBasis = Boolean(earlier && later && sameShape(earlier, later))
      const basis =
        earlier && later
          ? sameBasis
            ? periodKindLabel(later)
            : `${periodKindLabel(earlier)} vs ${periodKindLabel(later)}`
          : ""
      const ratio = asNumber(line.change)
      return {
        key: `${line.concept}|${line.unit}`,
        label: conceptLabel(line.concept),
        earlier: formatFact(asNumber(line.earlier), line.unit),
        later: formatFact(asNumber(line.later), line.unit),
        change: ratio === null ? null : Math.round(ratio * 10000) / 100,
        comparable: sameBasis || !basis,
        basis,
        accession: line.laterAccession || line.earlierAccession,
        source: line.laterSource || line.earlierSource,
      }
    })
})

const headlineLines = computed<ComparedLine[]>(() =>
  comparedLines.value.filter((line) => line.comparable && line.change !== null).slice(0, 4),
)

const mismatchCount = computed<number>(() => comparedLines.value.filter((line) => line.basis && !line.comparable).length)

const ruleRows = computed(() => {
  const observations = financials.value?.ruleObservations ?? {}
  return rules.value.map((rule) => ({
    ...rule,
    name: ruleName(rule.ruleId),
    checks: ruleChecks(rule.ruleId),
    statusText: ruleStatusLabel(rule.status),
    tone: ruleTone(rule.status),
    observation: observations[rule.ruleId] ?? null,
  }))
})

/* ---------------- 现金流与利润匹配（年度口径，后端配对） ---------------- */

/** 金额换算成「亿」只为排版，数值本身来自接口。 */
const toYi = (value: number | null): number | null => (value === null ? null : Math.round((value / 1e8) * 100) / 100)

const cashSeries = computed<{ end: string; cash: number | null; profit: number | null; multiple: number | null }[]>(() =>
  (financials.value?.cashVsProfit ?? []).map((row) => ({
    end: row.periodEnd,
    cash: toYi(row.operatingCashFlow),
    profit: toYi(row.netIncome),
    multiple: row.multiple,
  })),
)

const cashLatest = computed(() => {
  const rows = cashSeries.value.filter((row) => row.cash !== null && row.profit !== null && row.multiple !== null)
  const last = rows[rows.length - 1]
  if (!last || last.cash === null || last.profit === null || last.multiple === null) {
    return null
  }
  return { end: last.end, cash: last.cash, profit: last.profit, ratio: last.multiple }
})

const cashFlowOption = computed<EChartsOption | null>(() => {
  const rows = cashSeries.value.filter((row) => row.cash !== null || row.profit !== null)
  if (rows.length < 2) {
    return null
  }
  const unit = `亿${currencyUnit.value}`
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { data: ["净利润", "经营现金流"], top: 0, right: 0 },
    grid: { left: 56, right: 12, top: 32, bottom: 28 },
    xAxis: { type: "category", data: rows.map((row) => `FY ${row.end.slice(0, 4)}`) },
    yAxis: { type: "value", name: unit },
    series: [
      { name: "净利润", type: "bar", barMaxWidth: 22, color: "#3b82f6", data: rows.map((row) => row.profit) },
      { name: "经营现金流", type: "bar", barMaxWidth: 22, color: "#34d399", data: rows.map((row) => row.cash) },
    ],
  }
})

const cashFlowReason = computed<string>(() => {
  if (financialsPhase.value === "loading" || financialsPhase.value === "idle") {
    return "正在读取已申报数据"
  }
  if (financialsPhase.value === "empty") {
    return financials.value?.notice || "该公司没有已申报的财务数据，无法对比现金流与利润。"
  }
  if (financialsPhase.value === "error") {
    return "已申报数据读取失败，稍后可重试。"
  }
  return "年度经营现金流或净利润不足两期，暂不绘制。"
})

/* ---------------- 抬头 / 页签 / 来源 ---------------- */

const priceChange = computed<number | null>(() => {
  const latest = bars.value[bars.value.length - 1]
  if (!latest || latest.simpleReturn === null) {
    return null
  }
  return Math.round(latest.simpleReturn * 10000) / 100
})

const companiesTabs = computed(() => {
  const base = `/company/${encodeURIComponent(symbol.value)}`
  return [
    { key: "overview", label: "公司概况", to: base },
    { key: "ai", label: "AI财报解读" },
    { key: "filings", label: "原始财报", to: `${base}/filings` },
  ]
})

const evidenceTabs = computed(() => [
  { key: "facts", label: `关键数据（${comparedLines.value.length}）` },
  { key: "filings", label: `财报原文（${docEvidence.value.length}）` },
  { key: "charts", label: `图表引用（${cashFlowOption.value ? 1 : 0}）` },
])

const filingSummary = computed<string>(() => {
  const filing = latestFiling.value
  if (!filing) {
    return ""
  }
  const period = [filing.fiscalYear ? `FY${filing.fiscalYear}` : "", filing.fiscalPeriod].filter(Boolean).join(" ")
  return `${period}${filing.filingType ? `（${filing.filingType}）` : ""}`
})

const stripStats = computed(() => [
  { label: "解读报告期", value: periodsText.value === "—" ? null : periodsText.value },
  { label: "生成方式", value: generatedMode.value === "—" ? null : generatedMode.value },
  { label: "最新财报", value: filingSummary.value || null },
  { label: "发布日期", value: latestFiling.value?.filed ?? null },
])

const sourceList = computed<string[]>(() => {
  const list = ["本站研究记录"]
  if (instrument.value?.cik) {
    list.push("SEC EDGAR")
  }
  if (bars.value.length) {
    list.push("AKShare 日线")
  }
  return list
})

/* ---------------- 加载 ---------------- */

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadModelStatus(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/analysis/model-status"))
    const body = await readJson(response)
    if (!response.ok) {
      modelAvailable.value = null
      return
    }
    const data = dataOf(body)
    modelAvailable.value = typeof data?.available === "boolean" ? data.available : null
    modelMessage.value = asString(data?.message) ?? ""
  } catch {
    modelAvailable.value = null
    modelMessage.value = ""
  }
}

async function loadInstrument(): Promise<void> {
  const code = symbol.value.trim()
  if (!code) {
    return
  }
  try {
    const search = await searchInstruments(code, undefined, 10)
    instrument.value =
      search.results.find((hit) => hit.displaySymbol.toUpperCase() === code.toUpperCase()) ??
      search.results.find((hit) => hit.canonicalCode.toUpperCase() === code.toUpperCase()) ??
      search.results[0] ??
      null
  } catch {
    instrument.value = null
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  copyState.value = ""
  exportState.value = ""
  report.value = null
  generatedAt.value = ""

  if (!session.accessToken) {
    const restored = await restoreSession()
    if (!restored) {
      phase.value = "error"
      failure.value = "请先登录后再查看研究记录"
      return
    }
  }

  const id = runId.value.trim()
  if (!id) {
    phase.value = "error"
    retryable.value = false
    failure.value = "地址里缺少研究记录编号"
    return
  }

  try {
    const response = await authorizedFetch(
      `/api/v1/analysis/runs/${encodeURIComponent(id)}`,
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    if (!response.ok) {
      failure.value = asString(asRecord(asRecord(payload)?.error)?.message) ?? "找不到这份研究记录"
      retryable.value = response.status !== 404
      phase.value = "error"
      return
    }
    const data = dataOf(payload)
    if (!data) {
      phase.value = "error"
      retryable.value = false
      failure.value = "这份研究记录没有内容"
      return
    }
    report.value = {
      runId: asString(data.run_id) ?? id,
      reportId: asString(data.report_id) ?? "",
      status: asString(data.status) ?? "",
      title: asString(data.title) ?? "研究记录",
      message: asString(data.message) ?? "",
      body: typeof data.body === "string" ? data.body : "",
      notice: asString(data.notice) ?? "",
      asOf: asString(data.as_of) ?? "",
    }
    generatedAt.value = formatMoment(asRecord(asRecord(payload)?.meta)?.as_of)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    phase.value = "error"
  }
}

async function loadContext(): Promise<void> {
  await loadInstrument()
  const item = instrument.value
  if (!item) {
    researchPhase.value = "error"
    factsPhase.value = "unavailable"
    barsPhase.value = "empty"
    return
  }
  await Promise.all([loadBars(item.id), item.cik ? loadFacts(item.cik) : Promise.resolve(), item.cik ? loadFilings(item.cik) : Promise.resolve()])
  if (!item.cik) {
    factsPhase.value = "unavailable"
  }
}

async function loadRules(instrumentId: string): Promise<void> {
  const range = parsed.value.periods
  researchPhase.value = "loading"
  try {
    const params = new URLSearchParams({ as_of: reportAsOf.value })
    if (range) {
      params.set("period_a", range.earlier)
      params.set("period_b", range.later)
    }
    const response = await fetch(apiUrl(`/api/v1/companies/${instrumentId}/research?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    rules.value = [
      ...(Array.isArray(data?.risks) ? data.risks.flatMap(parseRule) : []),
      ...(Array.isArray(data?.highlights) ? data.highlights.flatMap(parseRule) : []),
      ...(Array.isArray(data?.unevaluated) ? data.unevaluated.flatMap(parseRule) : []),
    ]
    researchPhase.value = "ready"
  } catch {
    rules.value = []
    researchPhase.value = "error"
  }
}

async function loadFacts(targetCik: string): Promise<void> {
  factsPhase.value = "loading"
  try {
    const params = new URLSearchParams({ cik: targetCik, as_of: AUX_AS_OF })
    const response = await fetch(apiUrl(`/api/v1/sec/facts?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    facts.value = listOf(dataOf(body)?.facts).flatMap(parseFactPoint)
    factsPhase.value = facts.value.length ? "ready" : "empty"
  } catch {
    facts.value = []
    factsPhase.value = "error"
  }
}

async function loadFinancials(instrumentId: string): Promise<void> {
  const range = parsed.value.periods
  financialsPhase.value = "loading"
  try {
    financials.value = await fetchFinancials(instrumentId, {
      asOf: reportAsOf.value,
      reference: range?.later,
      compare: range?.earlier,
    })
    financialsPhase.value = financials.value && financials.value.dataStatus !== "empty" ? "ready" : "empty"
  } catch {
    financials.value = null
    financialsPhase.value = "error"
  }
}

async function loadFilings(targetCik: string): Promise<void> {
  try {
    const response = await fetch(apiUrl(`/api/v1/sec/filings?cik=${encodeURIComponent(targetCik)}`))
    const body = await readJson(response)
    if (!response.ok) {
      return
    }
    const rows = listOf(dataOf(body)?.filings).flatMap(parseFiling)
    latestFiling.value = rows.sort((left, right) => left.filed.localeCompare(right.filed)).pop() ?? null
  } catch {
    latestFiling.value = null
  }
}

async function loadBars(instrumentId: string): Promise<void> {
  barsPhase.value = "loading"
  try {
    const response = await fetch(apiUrl(`/api/v1/market/bars/${instrumentId}?limit=5`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    bars.value = listOf(dataOf(body)?.bars).flatMap((row) => {
      const tradeDate = asString(row.trade_date)
      const close = asString(row.close)
      return tradeDate && close ? [{ tradeDate, closeText: close, simpleReturn: asNumber(row.simple_return) }] : []
    })
    barsPhase.value = bars.value.length ? "ready" : "empty"
  } catch {
    bars.value = []
    barsPhase.value = "error"
  }
}

function parseRule(value: unknown): RuleLine[] {
  const record = asRecord(value)
  const ruleId = asString(record?.rule_id)
  if (!ruleId) {
    return []
  }
  return [{ ruleId, status: asString(record?.status) ?? "", rationale: asString(record?.rationale) ?? "" }]
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
    },
  ]
}

/* ---------------- 操作 ---------------- */

async function copyBody(): Promise<void> {
  if (!bodyText.value) {
    copyState.value = "没有可复制的正文"
    return
  }
  try {
    await navigator.clipboard.writeText(bodyText.value)
    copyState.value = "已复制正文"
  } catch {
    copyState.value = "浏览器不允许访问剪贴板，请改用导出"
  }
}

function exportMarkdown(): void {
  if (!bodyText.value) {
    exportState.value = "没有可导出的正文"
    return
  }
  const header = [
    `# ${report.value?.title ?? "研究记录"}`,
    "",
    `- 证券：${heading.value}`,
    `- 报告期：${periodsText.value}`,
    `- 生成时间：${generatedAt.value || "—"}`,
    `- 生成方式：${ruleBasedNotice.value}`,
    "",
  ].join("\n")
  const blob = new Blob([`${header}${bodyText.value}\n`], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${report.value?.title ?? "report"}.md`
  anchor.click()
  URL.revokeObjectURL(url)
  exportState.value = "已导出 Markdown"
}

function printReport(): void {
  window.print()
}

/** 按正文里的同一对报告期、以今天为观察日重新生成一份新记录。 */
async function regenerate(): Promise<void> {
  regenerateMessage.value = ""
  const item = instrument.value
  const range = parsed.value.periods
  if (!item || !range) {
    regenerateMessage.value = "这份记录缺少报告期，请回到公司页选择报告期后生成"
    return
  }
  regenerating.value = true
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/filing",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instrument_id: item.id, as_of: AUX_AS_OF, period_a: range.earlier, period_b: range.later }),
      },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const nextRun = asString(dataOf(payload)?.run_id)
    if (!response.ok || !nextRun) {
      regenerateMessage.value = asString(asRecord(asRecord(payload)?.error)?.message) ?? "重新生成失败，请稍后重试"
      return
    }
    void router.push(`/company/${encodeURIComponent(symbol.value)}/analysis/${encodeURIComponent(nextRun)}`)
  } catch {
    regenerateMessage.value = "无法连接服务器"
  } finally {
    regenerating.value = false
  }
}

function goFilings(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}/filings`)
}

async function bootstrap(): Promise<void> {
  await Promise.all([load(), loadContext(), loadModelStatus()])
  if (instrument.value) {
    await Promise.all([loadRules(instrument.value.id), loadFinancials(instrument.value.id)])
  } else {
    financialsPhase.value = "empty"
  }
}

onMounted(() => {
  void bootstrap()
})

watch(runId, (next, previous) => {
  if (next && next !== previous) {
    void bootstrap()
  }
})
</script>

<template>
  <main class="page stack">
    <nav class="crumb" aria-label="面包屑">
      <router-link :to="`/company/${encodeURIComponent(symbol)}`">公司研究</router-link>
      <i aria-hidden="true">›</i>
      <strong>AI财报解读</strong>
    </nav>

    <PageHeader :title="heading" :subtitle="report ? `${report.title} · 生成于 ${generatedAt || '—'}` : 'AI 财报解读'">
      <template #actions>
        <el-dropdown trigger="click">
          <el-button size="small">
            导出报告
            <span aria-hidden="true">&nbsp;▾</span>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="exportMarkdown">导出 Markdown</el-dropdown-item>
              <el-dropdown-item @click="copyBody">复制正文</el-dropdown-item>
              <el-dropdown-item @click="printReport">打印 / 存为 PDF</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button
          size="small"
          type="primary"
          :loading="regenerating"
          :disabled="phase !== 'ready' || !parsed.periods"
          @click="regenerate"
        >
          重新生成分析
        </el-button>
      </template>
    </PageHeader>

    <p v-if="regenerateMessage || copyState || exportState" class="muted" role="status">
      {{ regenerateMessage || copyState || exportState }}
    </p>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取研究记录"
      :message="failure"
      @retry="load"
    />

    <template v-else-if="report">
      <PanelCard compact>
        <div class="strip">
          <div class="logo" aria-hidden="true">{{ logoText }}</div>
          <div class="ident">
            <p class="strip-name">
              {{ instrument?.nameCn || instrument?.nameEn || symbol }}
              <span class="muted">{{ instrument?.displaySymbol ?? symbol }}</span>
            </p>
            <p class="muted strip-meta">{{ instrument?.nameEn || "—" }}</p>
          </div>
          <div class="quote">
            <template v-if="barsPhase === 'ready' && bars.length">
              <span class="strip-price num">
                {{ bars[bars.length - 1]?.closeText }}<small> {{ currencyUnit }}</small>
              </span>
              <DeltaValue :value="priceChange" suffix="%" />
              <span class="muted">{{ bars[bars.length - 1]?.tradeDate }} 收盘</span>
            </template>
            <span v-else class="muted">
              {{ barsPhase === "loading" ? "正在读取行情" : instrument?.market === "US" ? "暂未同步美股日线" : "暂无已同步的日线" }}
            </span>
          </div>
          <dl class="strip-stats">
            <div v-for="row in stripStats" :key="row.label" class="kv">
              <dt>{{ row.label }}</dt>
              <dd :class="{ dim: !row.value }">{{ row.value ?? "—" }}</dd>
            </div>
          </dl>
        </div>
      </PanelCard>

      <div class="tabs-bar">
        <SectionTabs :tabs="companiesTabs" model-value="ai" />
      </div>

      <div class="analysis-grid">
        <PanelCard title="AI 财报解读">
          <template #icon>✦</template>
          <template #actions>
            <StatusPill :text="runStatusLabel(report.status)" :tone="runTone(report.status)" dot />
          </template>

          <div class="intro">
            <span class="badge">基于 {{ periodsText }}</span>
            <p>{{ ruleBasedNotice }}</p>
          </div>

          <section class="block">
            <h3><span class="h-icon tone-ok" aria-hidden="true">★</span>核心变化</h3>
            <p v-if="!headlineLines.length" class="empty-line">
              {{ factsPhase === "loading" ? "正在核对两期口径" : "两期数字口径不一致或缺少数据，暂不给出变化率。" }}
            </p>
            <ol v-else class="headline-list">
              <li v-for="(line, index) in headlineLines" :key="line.key">
                <span class="line-index">{{ index + 1 }}</span>
                <div>
                  <p class="headline">
                    {{ line.label }} {{ line.later }}，{{ changeLabel }}
                    <DeltaValue :value="line.change" suffix="%" />
                  </p>
                  <p class="muted">{{ line.basis }} · 上期 {{ line.earlier }}</p>
                </div>
                <button class="link-btn" type="button" @click="evidenceTab = 'facts'">数据证据 ›</button>
              </li>
            </ol>
          </section>

          <section class="block">
            <h3><span class="h-icon tone-danger" aria-hidden="true">⚠</span>潜在风险</h3>
            <StateBlock v-if="researchPhase === 'loading'" state="loading" :rows="2" />
            <p v-else-if="!ruleRows.length" class="empty-line">暂无规则检查结果</p>
            <div v-else class="risk-cards">
              <article v-for="rule in ruleRows" :key="rule.ruleId" class="risk-card" :class="`tone-${rule.tone}`" :title="rule.checks">
                <div class="item-head">
                  <strong>{{ rule.name }}</strong>
                  <StatusPill :text="rule.statusText" :tone="rule.tone" />
                </div>
                <p class="risk-obs" :class="{ dim: !rule.observation }">{{ rule.observation ?? rule.rationale }}</p>
              </article>
            </div>
            <p v-if="ruleRows.length" class="footnote">
              卡片中的读数由服务端按同口径已申报数据配对计算，仅供参考；悬停卡片可查看判定口径。
            </p>
          </section>

          <section class="block">
            <h3><span class="h-icon tone-accent" aria-hidden="true">▥</span>现金流与利润匹配</h3>
            <p v-if="!cashFlowOption" class="empty-line">{{ cashFlowReason }}</p>
            <div v-else class="cash-row">
              <p v-if="cashLatest" class="cash-text">
                FY {{ cashLatest.end.slice(0, 4) }} 经营现金流 {{ cashLatest.cash.toFixed(2) }} 亿{{ currencyUnit }}，
                净利润 {{ cashLatest.profit.toFixed(2) }} 亿{{ currencyUnit }}，现金流为利润的
                <strong class="num">{{ cashLatest.ratio.toFixed(2) }}</strong> 倍。
              </p>
              <VChart class="cash-chart" :option="cashFlowOption" autoresize aria-label="现金流与利润" />
            </div>
          </section>

          <section class="block">
            <h3><span class="h-icon tone-violet" aria-hidden="true">❝</span>管理层表述</h3>
            <p v-if="!docEvidence.length" class="empty-line">
              {{ noDocumentCited ? "暂无上传的财报原文。上传公司财报 PDF 并完成解析后，这里会逐页引用管理层表述。" : "这份记录没有引用财报原文。" }}
              <router-link class="link" :to="`/company/${encodeURIComponent(symbol)}/uploads`">上传财报 ›</router-link>
            </p>
            <blockquote v-for="doc in docEvidence" :key="`${doc.documentId}-${doc.pageStart}`" class="quote-block">
              <p>{{ doc.snippet }}</p>
              <footer>
                P.{{ doc.pageStart }}-{{ doc.pageEnd }}
                <button class="link-btn" type="button" @click="goFilings">查看原文 ›</button>
              </footer>
            </blockquote>
          </section>

          <section class="block">
            <h3><span class="h-icon tone-ok" aria-hidden="true">✓</span>结论</h3>
            <div class="conclusion" :class="`tone-${runTone(report.status)}`">
              <p>
                {{ report.message || "本记录没有给出结论说明。" }}
                <template v-if="mismatchCount">其中 {{ mismatchCount }} 项两期期间长度不同（单季与累计），未计算变化率。</template>
              </p>
            </div>
          </section>
        </PanelCard>

        <PanelCard title="证据与数据来源" info="每个数字都可以追溯到 SEC 申报号">
          <template #icon>▤</template>
          <SectionTabs :tabs="evidenceTabs" :model-value="evidenceTab" class="evidence-tabs" @update:model-value="evidenceTab = $event" />

          <div class="evidence-body">
            <template v-if="evidenceTab === 'facts'">
              <p v-if="!comparedLines.length" class="empty-line">这份记录没有指标数据。</p>
              <ol v-else class="evidence-list">
                <li v-for="(line, index) in comparedLines" :key="line.key">
                  <span class="line-index">{{ index + 1 }}</span>
                  <div class="evidence-text">
                    <p class="evidence-title">
                      {{ line.label }}
                      <DeltaValue v-if="line.change !== null" :value="line.change" suffix="%" size="sm" />
                      <span v-else-if="line.basis" class="muted">口径不同</span>
                    </p>
                    <p class="excerpt num">{{ line.earlier }} → {{ line.later }}</p>
                    <p class="page-ref">
                      {{ line.basis || "—" }}<template v-if="line.accession"> · 申报号 {{ line.accession }}</template>
                      <a v-if="line.source" :href="line.source" target="_blank" rel="noreferrer noopener">查看原文 ›</a>
                    </p>
                  </div>
                </li>
              </ol>
            </template>

            <template v-else-if="evidenceTab === 'filings'">
              <p v-if="!docEvidence.length" class="empty-line">
                {{ noDocumentCited ? "暂无上传的财报原文可供引用。" : "这份记录没有引用财报原文。" }}
              </p>
              <ol v-else class="evidence-list">
                <li v-for="(doc, index) in docEvidence" :key="`ev-${doc.documentId}-${index}`">
                  <span class="line-index">{{ index + 1 }}</span>
                  <div class="evidence-text">
                    <p class="page-ref">P.{{ doc.pageStart }}-{{ doc.pageEnd }}</p>
                    <p class="excerpt">“{{ doc.snippet }}”</p>
                    <button class="link-btn" type="button" @click="goFilings">查看原文 ›</button>
                  </div>
                </li>
              </ol>
            </template>

            <template v-else>
              <p v-if="!cashFlowOption" class="empty-line">{{ cashFlowReason }}</p>
              <ol v-else class="evidence-list">
                <li>
                  <span class="line-index">1</span>
                  <div class="evidence-text">
                    <p class="evidence-title">现金流与利润匹配</p>
                    <p class="excerpt">年度经营现金流与净利润，{{ cashSeries.map((row) => `FY ${row.end.slice(0, 4)}`).join("、") }}</p>
                    <p class="page-ref">来源：SEC EDGAR 年报（10-K）</p>
                  </div>
                </li>
              </ol>
            </template>
          </div>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="generatedAt"
        :sources="sourceList"
        :note="modelAvailable === false ? '未配置大模型，报告为规则整理' : '结论请与数据来源核对'"
      />
    </template>
  </main>
</template>

<style scoped>
.crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

.crumb a {
  color: var(--muted);
  text-decoration: none;
}

.crumb a:hover {
  color: var(--accent);
}

.crumb strong {
  color: var(--text);
  font-weight: 600;
}

.strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.8fr);
  gap: 18px;
  align-items: center;
}

.logo {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: linear-gradient(160deg, var(--panel-raise), var(--panel-deep));
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
}

.strip-name {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-strong);
}

.strip-name .muted {
  margin-left: 6px;
  font-weight: 500;
}

.strip-meta {
  margin: 4px 0 0;
}

.quote {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 13px;
  min-width: 0;
  border-left: 1px solid var(--line-soft);
  padding-left: 18px;
}

.strip-price {
  font-size: 24px;
  font-weight: 700;
  color: var(--text-strong);
}

.strip-price small {
  margin-left: 3px;
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.strip-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 18px;
  margin: 0;
  border-left: 1px solid var(--line-soft);
  padding-left: 18px;
}

.kv {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 3px;
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kv dd.dim {
  color: var(--muted);
  font-weight: 500;
}

.tabs-bar {
  border-bottom: 1px solid var(--line);
  padding: 0 4px 10px;
}

.analysis-grid {
  display: grid;
  gap: var(--gap);
  grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr);
  align-items: start;
}

.intro {
  display: grid;
  gap: 8px;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--accent) 8%, transparent);
  padding: 10px 12px;
}

.intro p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.badge {
  justify-self: start;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.block {
  margin-top: 16px;
  border-top: 1px solid var(--line-soft);
  padding-top: 12px;
}

.block h3 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-strong);
}

.h-icon {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-size: 12px;
}

.h-icon.tone-ok {
  color: var(--ok);
  background: color-mix(in srgb, var(--ok) 16%, transparent);
}

.h-icon.tone-danger {
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 16%, transparent);
}

.h-icon.tone-accent {
  color: var(--accent);
  background: var(--accent-soft);
}

.h-icon.tone-violet {
  color: var(--violet);
  background: color-mix(in srgb, var(--violet) 16%, transparent);
}

.empty-line {
  margin: 0;
  max-width: 46em;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.headline-list,
.evidence-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.headline-list li {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: start;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 10px;
}

.headline-list li:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.headline {
  margin: 0 0 3px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}

.headline-list .muted {
  margin: 0;
}

.line-index {
  flex: none;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
}

.item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.item-head strong {
  font-size: 14px;
  color: var(--text-strong);
}

.risk-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.risk-card {
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  padding: 10px 12px;
  display: grid;
  gap: 6px;
}

.risk-card.tone-danger {
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
}

.risk-obs {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.risk-obs.dim {
  color: var(--muted);
}

.cash-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr);
  gap: 16px;
  align-items: center;
}

.cash-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
  color: var(--text);
}

.cash-text strong {
  color: var(--accent);
  font-size: 15px;
}

.cash-chart {
  width: 100%;
  height: 220px;
}

.quote-block {
  margin: 0 0 10px;
  border-left: 3px solid var(--accent);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: var(--panel-soft);
  padding: 10px 14px;
}

.quote-block p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.quote-block footer {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
  font-size: 13px;
}

.conclusion {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  padding: 12px 14px;
}

.conclusion p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
}

.conclusion.tone-ok {
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
  background: color-mix(in srgb, var(--ok) 8%, transparent);
}

.conclusion.tone-danger {
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
}

.link-btn {
  appearance: none;
  border: 0;
  background: none;
  color: var(--accent);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
  white-space: nowrap;
}

.link {
  color: var(--accent);
  text-decoration: none;
}

.evidence-tabs :deep(.tab) {
  margin-bottom: 0;
  font-size: 13px;
  padding: 5px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 6px;
}

.evidence-tabs :deep(.tab.active) {
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}

.evidence-body {
  margin-top: 12px;
}

.evidence-list li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 10px;
}

.evidence-list li:last-child {
  border-bottom: 0;
}

.evidence-text {
  flex: 1;
  display: grid;
  gap: 3px;
  min-width: 0;
}

.evidence-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}

.excerpt {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
  word-break: break-word;
}

.page-ref {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.page-ref a {
  color: var(--accent);
  text-decoration: none;
}

.footnote {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

@media (max-width: 1400px) {
  .strip {
    grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr);
  }

  .strip-stats {
    grid-column: 1 / -1;
    border-left: 0;
    padding-left: 0;
  }

  .risk-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1200px) {
  .analysis-grid,
  .cash-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
