<script setup lang="ts">
/**
 * AI 财报解读（/company/:symbol/analysis/:runId）。视觉目标：设计稿 5「AI 财报解读」。
 *
 *  - GET  /api/v1/analysis/runs/{run_id}   报告正文（需 Bearer），页面主体
 *  - GET  /api/v1/analysis/model-status    模型是否已配置（规则生成时必须显式提示）
 *  - GET  /api/v1/instruments/search       路由 symbol → 证券元数据（id / CIK / 币种）
 *  - GET  /api/v1/companies/{id}/research  规则结论（highlights / risks / unevaluated）
 *  - GET  /api/v1/sec/facts?cik=&as_of=    已申报事实，右栏「关键数据」的证据
 *  - GET  /api/v1/sec/filings?cik=         最新财报与申报日（抬头条）
 *  - GET  /api/v1/market/bars/{id}         最新收盘与涨跌，未同步时按接口原话空态
 *
 * 后端返回的报告正文是纯文本行（不是 markdown）：逐行渲染，行内的来源 URL 抽成链接，
 * 不引入任何渲染库；正文里没有的段落（经营亮点 / 现金流匹配 / 结论）一律走 StateBlock 空态，
 * 绝不补写内容。规则引擎的 status 原样展示，不臆造「高/中」风险等级。
 *
 * 已知边界：AnalysisRunData 不含 as_of / period_a / period_b，因此本页无法重新生成
 * （POST /api/v1/analysis/filing 需要这三个字段）。「重新生成」在右上角按钮旁给出到公司页的入口。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, listOf } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
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
/** 辅助接口（research / facts / filings / bars）的观察日，与仓库其它公司页保持同一个可复现日期。 */
const AUX_AS_OF = "2024-06-01"

interface RunReport {
  runId: string
  reportId: string
  status: string
  title: string
  message: string
  body: string
  notice: string
}

interface RuleLine {
  ruleId: string
  status: string
  rationale: string
}

interface FactLine {
  concept: string
  unit: string
  value: string
  periodEnd: string
  form: string
  filed: string
  accessionNo: string
  sourceUrl: string
}

interface DocEvidence {
  documentId: string
  pageStart: string
  pageEnd: string
  snippet: string
}

interface FilingBrief {
  filingType: string
  fiscalYear: string
  fiscalPeriod: string
  filed: string
  accessionNo: string
}

interface LineRef {
  index: number
  text: string
  url: string
}

type Phase = "loading" | "ready" | "error"
type AuxPhase = "idle" | "loading" | "ready" | "empty" | "error" | "unavailable"

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

const rules = ref<RuleLine[]>([])
const unevaluated = ref<RuleLine[]>([])
const researchNotice = ref("")
const researchPhase = ref<AuxPhase>("idle")
const facts = ref<FactLine[]>([])
const factsPhase = ref<AuxPhase>("idle")
const latestFiling = ref<FilingBrief | null>(null)
const filingsPhase = ref<AuxPhase>("idle")
const bars = ref<{ tradeDate: string; closeText: string; simpleReturn: number | null }[]>([])
const barsPhase = ref<AuxPhase>("idle")
const barsMessage = ref("")

const evidenceTab = ref("filings")
const activeTab = ref("ai")

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const runId = computed<string>(() => (Array.isArray(props.runId) ? props.runId[0] ?? "" : props.runId))
const bodyText = computed<string>(() => report.value?.body ?? "")
const currency = computed<string>(() => instrument.value?.currency ?? "")

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

const runTone = computed<"ok" | "warn" | "danger" | "muted">(() => {
  const status = report.value?.status ?? ""
  if (status === "succeeded") {
    return "ok"
  }
  if (status === "failed") {
    return "danger"
  }
  return "muted"
})

/** 规则生成的明确提示：模型不可用时不得含糊。 */
const ruleBasedNotice = computed<string>(() => {
  if (modelAvailable.value === false) {
    return `未配置大模型，报告为规则生成。${modelMessage.value}`
  }
  if (modelAvailable.value === null) {
    return "未能确认模型状态，请以报告内的来源标注为准。"
  }
  return modelMessage.value
})

const bodyRows = computed<LineRef[]>(() =>
  bodyText.value
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line, index) => {
      const match = line.match(/https?:\/\/\S+/)
      const url = match ? match[0] : ""
      return { index: index + 1, text: url ? line.replace(url, "").trim() : line, url }
    }),
)

/** 正文里的文档引用行：`文档 {id} 第 {起}-{止} 页 {摘录}`。 */
const docEvidence = computed<DocEvidence[]>(() =>
  bodyText.value
    .split("\n")
    .flatMap((line) => {
      const match = line.trim().match(/^文档 (\S+) 第 (\d+)-(\d+) 页 (.+)$/)
      if (!match) {
        return []
      }
      return [
        {
          documentId: match[1] ?? "",
          pageStart: match[2] ?? "",
          pageEnd: match[3] ?? "",
          snippet: match[4] ?? "",
        },
      ]
    }),
)

const noDocumentCited = computed<boolean>(() => bodyText.value.includes(NO_DOCUMENT))

const highlights = computed<RuleLine[]>(() => rules.value.filter((rule) => rule.status !== "not_evaluated"))

const riskRules = computed<RuleLine[]>(() => rules.value)

const metricRows = computed<LineRef[]>(() =>
  bodyRows.value.filter((row) => row.text.includes(":")) ,
)

const cashConcept = computed<FactLine | null>(
  () =>
    facts.value.find((fact) => fact.concept.includes("OperatingActivities")) ?? null,
)

const profitConcept = computed<FactLine | null>(
  () =>
    facts.value.find(
      (fact) =>
        fact.concept.endsWith("NetIncomeLoss") ||
        fact.concept.endsWith("OperatingIncomeLoss") ||
        fact.concept.endsWith("GrossProfit"),
    ) ?? null,
)

const cashFlowOption = computed<EChartsOption | null>(() => {
  const cash = cashConcept.value
  const profit = profitConcept.value
  if (!cash || !profit) {
    return null
  }
  const periods = [...new Set(facts.value.map((fact) => fact.periodEnd))].sort()
  const seriesFor = (concept: string): (number | null)[] =>
    periods.map((period) => {
      const hit = facts.value.find((fact) => fact.concept === concept && fact.periodEnd === period)
      const value = hit ? Number(hit.value) : Number.NaN
      return Number.isFinite(value) ? value : null
    })
  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: [cash.concept, profit.concept], textStyle: { color: "#95a6be", fontSize: 11 } },
    grid: { left: 72, right: 24, top: 36, bottom: 40 },
    xAxis: {
      type: "category",
      data: periods,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      axisLine: { lineStyle: { color: "rgba(231,237,248,0.12)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#95a6be", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(231,237,248,0.06)" } },
    },
    series: [
      { name: cash.concept, type: "bar", barMaxWidth: 36, itemStyle: { color: "#3b82f6" }, data: seriesFor(cash.concept) },
      { name: profit.concept, type: "bar", barMaxWidth: 36, itemStyle: { color: "#34d399" }, data: seriesFor(profit.concept) },
    ],
  }
})

const cashFlowReason = computed<string>(() => {
  if (factsPhase.value === "loading") {
    return "正在读取已申报事实"
  }
  if (factsPhase.value === "unavailable") {
    return "该证券没有 CIK，接口不会返回 SEC XBRL 事实，无法对比现金流与利润。"
  }
  if (factsPhase.value === "error") {
    return "无法读取已申报事实，稍后可重试。"
  }
  const missing: string[] = []
  if (!cashConcept.value) {
    missing.push("现金流量表科目（例如 us-gaap:NetCashProvidedByUsedInOperatingActivities）")
  }
  if (!profitConcept.value) {
    missing.push("利润科目（例如 us-gaap:NetIncomeLoss）")
  }
  if (missing.length) {
    return `已申报事实里没有${missing.join("与")}，接口没给这两条线，因此不画匹配图。`
  }
  return "已申报事实不足两期，画不出匹配趋势。"
})

const priceChange = computed<number | null>(() => {
  const latest = bars.value[bars.value.length - 1]
  if (!latest || latest.simpleReturn === null) {
    return null
  }
  return Math.round(latest.simpleReturn * 10000) / 100
})

const companiesTabs = computed(() => [
  { key: "overview", label: "公司概况", to: `/company/${encodeURIComponent(symbol.value)}` },
  { key: "finance", label: "财务分析" },
  { key: "ai", label: "AI财报解读" },
  { key: "valuation", label: "估值模型" },
  { key: "peers", label: "同行对比" },
  { key: "institution", label: "机构观点" },
  { key: "news", label: "新闻舆情" },
])

const TAB_NOTICES: Record<string, { title: string; message: string }> = {
  finance: {
    title: "财务分析页签还没有对应接口",
    message:
      "公司页的财务总览与财务趋势已经在 /company/{symbol} 上展示 XBRL 事实；这里不做第二套口径，也不在缺少接口时补数字。",
  },
  valuation: {
    title: "估值模型需要市值与股本",
    message:
      "已接入接口只有 /market/bars 的价格与 SEC XBRL 财报科目，没有股本与市值字段，PE / PB / DCF 都算不出来。",
  },
  peers: {
    title: "同行对比需要行业归类",
    message: "证券元数据里没有行业字段，无法确定同行集合；接口补齐之前本页不列同行。",
  },
  institution: {
    title: "机构观点需要研报数据源",
    message: "当前没有接入卖方研报或评级接口，因此没有可展示的机构观点。",
  },
  news: {
    title: "新闻舆情未启用",
    message: "后端配置里 ENABLE_NEWS=false，新闻与舆情没有数据源。",
  },
}

const activeTabNotice = computed(() => TAB_NOTICES[activeTab.value] ?? null)

const evidenceTabs = computed(() => [
  { key: "filings", label: `财报原文（${docEvidence.value.length}）` },
  { key: "facts", label: `关键数据（${facts.value.length}）` },
  { key: "charts", label: `图表引用（${cashFlowOption.value ? 1 : 0}）` },
])

const filingSummary = computed<string>(() => {
  const filing = latestFiling.value
  if (!filing) {
    return "—"
  }
  const period = [filing.fiscalYear, filing.fiscalPeriod].filter(Boolean).join(" ")
  return [period, filing.filingType].filter(Boolean).join(" · ")
})

const stripStats = computed(() => [
  { label: "总市值", value: null as string | null, info: "已接入接口未提供市值字段（行情只有价格）" },
  {
    label: "所属行业",
    value: null as string | null,
    info: "证券元数据没有行业字段，接口未提供",
  },
  {
    label: "最新财报",
    value: filingSummary.value === "—" ? null : filingSummary.value,
    info: "来自 GET /api/v1/sec/filings?cik=",
  },
  {
    label: "发布日期",
    value: latestFiling.value?.filed ?? null,
    info: "最新一份申报的申报日（filed）",
  },
])

const sourceList = computed<string[]>(() => [
  "/api/v1/analysis/runs/{run_id}",
  "/api/v1/analysis/model-status",
  "/api/v1/companies/{id}/research",
  "/api/v1/sec/facts",
  "/api/v1/sec/filings",
  "/api/v1/market/bars/{id}",
])

function ruleTone(status: string): "ok" | "warn" | "danger" | "muted" | "accent" {
  return RULE_TONES[status] ?? "muted"
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadModelStatus(): Promise<void> {
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/model-status",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const body = await readJson(response)
    if (!response.ok) {
      modelAvailable.value = null
      return
    }
    const data = dataOf(body)
    modelAvailable.value = asBoolSafe(data?.available)
    modelMessage.value = asString(data?.message) ?? ""
  } catch {
    modelAvailable.value = null
    modelMessage.value = ""
  }
}

function asBoolSafe(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null
}

async function loadInstrument(): Promise<void> {
  const code = symbol.value.trim()
  if (!code) {
    return
  }
  try {
    const search = await searchInstruments(code, undefined, 10)
    instrument.value =
      search.results.find((hit) => hit.displaySymbol === code) ??
      search.results.find((hit) => hit.canonicalCode === code) ??
      search.results[0] ??
      null
  } catch {
    // 证券元数据只是抬头，取不到不影响报告正文。
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
      retryable.value = true
      failure.value = "请先登录后再查看分析报告"
      return
    }
  }

  const id = runId.value.trim()
  if (!id) {
    phase.value = "error"
    retryable.value = false
    failure.value = "路由里没有运行标识 runId"
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
      const error = asRecord(asRecord(payload)?.error)
      failure.value = asString(error?.message) ?? "找不到这份研究记录"
      retryable.value = response.status !== 404
      phase.value = "error"
      return
    }
    const data = dataOf(payload)
    if (!data) {
      phase.value = "error"
      retryable.value = false
      failure.value = "接口返回里没有报告数据"
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
    }
    generatedAt.value = asString(asRecord(asRecord(payload)?.meta)?.as_of) ?? ""
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

/** 规则结论（research）+ 已申报事实（facts）+ 抬头条所需的申报与行情。 */
async function loadContext(): Promise<void> {
  await loadInstrument()
  const item = instrument.value
  if (!item) {
    researchPhase.value = "error"
    factsPhase.value = "unavailable"
    filingsPhase.value = "unavailable"
    barsPhase.value = "empty"
    barsMessage.value = "无法定位证券，行情不可用"
    return
  }
  await Promise.all([loadRules(item.id), loadBars(item.id)])
  if (item.cik) {
    await Promise.all([loadFacts(item.cik), loadFilings(item.cik)])
  } else {
    factsPhase.value = "unavailable"
    filingsPhase.value = "unavailable"
  }
}

async function loadRules(instrumentId: string): Promise<void> {
  researchPhase.value = "loading"
  try {
    const params = new URLSearchParams({ as_of: AUX_AS_OF })
    const response = await fetch(apiUrl(`/api/v1/companies/${instrumentId}/research?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    researchNotice.value = asString(data?.notice) ?? ""
    const collected = [
      ...(Array.isArray(data?.highlights) ? data.highlights : []),
      ...(Array.isArray(data?.risks) ? data.risks : []),
    ]
    rules.value = collected.flatMap(parseRule)
    unevaluated.value = Array.isArray(data?.unevaluated) ? data.unevaluated.flatMap(parseRule) : []
    researchPhase.value = "ready"
  } catch {
    rules.value = []
    unevaluated.value = []
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
    const rows = listOf(dataOf(body)?.facts).flatMap(parseFact)
    facts.value = rows
    factsPhase.value = rows.length ? "ready" : "empty"
  } catch {
    facts.value = []
    factsPhase.value = "error"
  }
}

async function loadFilings(targetCik: string): Promise<void> {
  filingsPhase.value = "loading"
  try {
    const response = await fetch(apiUrl(`/api/v1/sec/filings?cik=${encodeURIComponent(targetCik)}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const rows = listOf(dataOf(body)?.filings).flatMap(parseFiling)
    latestFiling.value =
      rows.sort((left, right) => left.filed.localeCompare(right.filed)).pop() ?? null
    filingsPhase.value = rows.length ? "ready" : "empty"
  } catch {
    latestFiling.value = null
    filingsPhase.value = "error"
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
    barsMessage.value = asString(data?.empty_message) ?? "暂无已同步的日线"
    bars.value = listOf(data?.bars).flatMap((record) => {
      const row = asRecord(record)
      const tradeDate = asString(row?.trade_date)
      const close = asString(row?.close)
      if (!tradeDate || !close) {
        return []
      }
      return [{ tradeDate, closeText: close, simpleReturn: asNumber(row?.simple_return) }]
    })
    barsPhase.value = bars.value.length ? "ready" : "empty"
  } catch {
    bars.value = []
    barsMessage.value = "无法连接接口"
    barsPhase.value = "error"
  }
}

function parseRule(value: unknown): RuleLine[] {
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

function parseFact(value: unknown): FactLine[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  const amount = asString(record?.value)
  if (!concept || !amount) {
    return []
  }
  return [
    {
      concept,
      unit: asString(record?.unit) ?? "—",
      value: amount,
      periodEnd: asString(record?.period_end) ?? "—",
      form: asString(record?.form) ?? "—",
      filed: asString(record?.filed) ?? "—",
      accessionNo: asString(record?.accession_no) ?? "—",
      sourceUrl: asString(record?.source_url) ?? "",
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
      fiscalYear: asString(record?.fiscal_year) ?? "",
      fiscalPeriod: asString(record?.fiscal_period) ?? "",
      filed,
      accessionNo: asString(record?.accession_no) ?? "",
    },
  ]
}

async function copyBody(): Promise<void> {
  const text = bodyText.value
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

/** 导出报告的 Markdown：正文原样落盘，不含任何本地补写的内容。 */
function exportMarkdown(): void {
  if (!bodyText.value) {
    exportState.value = "没有可导出的正文"
    return
  }
  const header = [
    `# ${report.value?.title ?? "研究记录"}`,
    "",
    `- 证券：${heading.value}`,
    `- 运行标识：${report.value?.runId ?? ""}`,
    `- 记录标识：${report.value?.reportId ?? ""}`,
    `- 服务端截止：${generatedAt.value || "—"}`,
    `- 模型状态：${ruleBasedNotice.value || "—"}`,
    "",
  ].join("\n")
  const blob = new Blob([`${header}${bodyText.value}\n`], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = `${report.value?.runId ?? "report"}.md`
  anchor.click()
  URL.revokeObjectURL(url)
  exportState.value = "已导出 Markdown（正文与来源均来自接口）"
}

function printReport(): void {
  window.print()
}

function goCompany(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}`)
}

function goFilings(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}/filings`)
}

onMounted(() => {
  void load()
  void loadContext()
  void loadModelStatus()
})
</script>

<template>
  <main class="page stack">
    <nav class="crumb" aria-label="面包屑">
      <span>公司研究</span>
      <i aria-hidden="true">›</i>
      <strong>AI财报解读</strong>
    </nav>

    <PageHeader :title="heading" :subtitle="report ? `${report.title} · 运行 ${report.runId}` : 'AI 财报解读'">
      <template #actions>
        <el-dropdown trigger="click">
          <el-button size="small">
            导出报告
            <span aria-hidden="true">▾</span>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="exportMarkdown">导出 Markdown</el-dropdown-item>
              <el-dropdown-item @click="copyBody">复制正文</el-dropdown-item>
              <el-dropdown-item @click="printReport">打印为 PDF</el-dropdown-item>
              <el-dropdown-item divided @click="goCompany">到公司页重新生成（需选报告期）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button size="small" type="primary" :disabled="!retryable && phase === 'error'" @click="load">
          重新读取分析
        </el-button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取分析报告"
      :message="`${failure}（证券 ${symbol} · 运行 ${runId}）`"
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
            <p class="muted strip-meta">
              {{ instrument?.exchange || "—" }} · {{ instrument?.market || "—" }} · 币种
              {{ currency || "—" }}
            </p>
          </div>
          <div class="quote">
            <template v-if="barsPhase === 'ready' && bars.length">
              <span class="strip-price">
                {{ bars[bars.length - 1]?.closeText }}<small> {{ currency }}</small>
              </span>
              <DeltaValue :value="priceChange" suffix="%" />
            </template>
            <span v-else class="muted">
              {{ barsPhase === "loading" ? "正在读取行情" : barsMessage || "行情不可用" }}
            </span>
          </div>
          <dl class="strip-stats">
            <div v-for="row in stripStats" :key="row.label" class="kv">
              <dt>
                {{ row.label }}
                <span class="info" :title="row.info">?</span>
              </dt>
              <dd :class="{ dim: !row.value }">{{ row.value ?? "—" }}</dd>
            </div>
          </dl>
        </div>
      </PanelCard>

      <PanelCard compact>
        <SectionTabs :tabs="companiesTabs" :model-value="activeTab" @update:model-value="activeTab = $event" />
      </PanelCard>

      <PanelCard v-if="activeTabNotice" compact>
        <StateBlock
          state="empty"
          :title="activeTabNotice.title"
          :message="activeTabNotice.message"
        />
      </PanelCard>

      <div v-else class="grid-1-2">
        <PanelCard title="AI 财报解读">
          <template #icon>✦</template>
          <template #actions>
            <StatusPill :text="report.status || '—'" :tone="runTone" dot />
          </template>

          <el-alert
            :type="modelAvailable === false ? 'warning' : 'info'"
            :closable="false"
            show-icon
            class="notice"
          >
            <template #title>{{ ruleBasedNotice }}</template>
          </el-alert>

          <p class="muted">
            已深度阅读 {{ bodyRows.length }} 行报告正文；报告期、口径、申报号与服务端截止时间如下，
            正文之外的段落（经营亮点 / 结论）若接口没给，这里显示空态而不是补写内容。
          </p>

          <section class="block">
            <h3>经营亮点</h3>
            <StateBlock
              v-if="!highlights.length"
              state="empty"
              :title="researchPhase === 'loading' ? '正在读取规则结论' : '接口未返回经营亮点'"
              :message="`${researchNotice || '接口未提供来源说明'}规则引擎只有在拿到对应科目时才会给出结论。`"
            />
            <ol v-else class="item-list">
              <li v-for="rule in highlights" :key="rule.ruleId">
                <div class="item-head">
                  <strong>{{ rule.ruleId }}</strong>
                  <StatusPill :text="rule.status || '—'" :tone="ruleTone(rule.status)" />
                </div>
                <p class="item-text">{{ rule.rationale }}</p>
                <div class="item-actions">
                  <button class="link-btn" type="button" @click="goFilings">财报原文 ›</button>
                </div>
              </li>
            </ol>
          </section>

          <section class="block">
            <h3>潜在风险</h3>
            <StateBlock
              v-if="!riskRules.length"
              state="empty"
              title="接口未返回风险标记"
              :message="`${researchNotice || '接口未提供来源说明'}未标记不等于没有风险；下面列出规则引擎给出的未评估原因。`"
            />
            <div v-else class="risk-cards">
              <article v-for="rule in riskRules" :key="`risk-${rule.ruleId}`" class="risk-card">
                <div class="item-head">
                  <strong>{{ rule.ruleId }}</strong>
                  <StatusPill :text="rule.status || '—'" :tone="ruleTone(rule.status)" />
                </div>
                <p class="item-text">{{ rule.rationale }}</p>
              </article>
            </div>
            <details v-if="unevaluated.length" class="unevaluated" open>
              <summary>未评估的规则（{{ unevaluated.length }} 条，接口给出的原因）</summary>
              <ul class="plain-list">
                <li v-for="rule in unevaluated" :key="`u-${rule.ruleId}`">
                  <strong>{{ rule.ruleId }}</strong>
                  <span class="muted">{{ rule.rationale }}</span>
                </li>
              </ul>
            </details>
          </section>

          <section class="block">
            <h3>现金流与利润匹配</h3>
            <StateBlock
              v-if="!cashFlowOption"
              state="empty"
              title="画不出匹配图"
              :message="cashFlowReason"
            />
            <template v-else>
              <VChart class="cash-chart" :option="cashFlowOption" autoresize aria-label="现金流与利润" />
              <p class="footnote">
                数据源 /api/v1/sec/facts，观察日 {{ AUX_AS_OF }}；两条都是接口给的已申报数值，未做任何换算。
              </p>
            </template>
          </section>

          <section class="block">
            <h3>报告正文</h3>
            <p v-if="report.message" class="muted">{{ report.message }}</p>
            <StateBlock
              v-if="!bodyRows.length"
              state="empty"
              title="这份记录没有报告正文"
              :message="report.notice || '接口返回的 body 为空。'"
            />
            <ol v-else class="item-list">
              <li v-for="row in bodyRows" :key="row.index">
                <span class="line-index">{{ row.index }}</span>
                <span class="item-text">{{ row.text }}</span>
                <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer noopener">来源 ›</a>
              </li>
            </ol>
          </section>

          <section class="block">
            <h3>管理层表述</h3>
            <StateBlock
              v-if="!docEvidence.length"
              state="empty"
              title="没有可引用的上传文档"
              :message="noDocumentCited
                ? `后端原文：${NO_DOCUMENT}上传与解析自己的财报后，正文会逐页引用原文。`
                : '这份记录里没有文档引用行，接口未给出原文摘录。'"
            />
            <blockquote v-for="doc in docEvidence" :key="`${doc.documentId}-${doc.pageStart}`" class="quote-block">
              <p>{{ doc.snippet }}</p>
              <footer>
                文档 {{ doc.documentId }} · P.{{ doc.pageStart }}-{{ doc.pageEnd }}
                <button class="link-btn" type="button" @click="goFilings">查看原文 ›</button>
              </footer>
            </blockquote>
          </section>

          <section class="block">
            <h3>结论</h3>
            <div class="conclusion" :class="`tone-${runTone}`">
              <StatusPill :text="report.status || '—'" :tone="runTone" dot />
              <p class="item-text">
                {{ report.message || "接口没有给出结论说明。" }}
              </p>
            </div>
            <p v-if="report.notice" class="footnote">{{ report.notice }}</p>
          </section>
        </PanelCard>

        <PanelCard title="证据与数据来源" info="正文中的文档引用行、/sec/facts 的已申报事实、以及本页实际渲染的图表">
          <template #icon>▤</template>
          <SectionTabs :tabs="evidenceTabs" :model-value="evidenceTab" @update:model-value="evidenceTab = $event" />

          <div class="evidence-body">
            <template v-if="evidenceTab === 'filings'">
              <StateBlock
                v-if="!docEvidence.length"
                state="empty"
                title="没有引用任何财报原文"
                :message="noDocumentCited ? `后端原文：${NO_DOCUMENT}` : '正文里没有「文档 … 第 x-y 页 …」引用行。'"
              />
              <ol v-else class="item-list">
                <li v-for="(doc, index) in docEvidence" :key="`ev-${doc.documentId}-${index}`">
                  <span class="line-index">{{ index + 1 }}</span>
                  <div class="evidence-text">
                    <p class="page-ref">P.{{ doc.pageStart }}-{{ doc.pageEnd }} 文档 {{ doc.documentId }}</p>
                    <p class="excerpt">“{{ doc.snippet }}”</p>
                    <button class="link-btn" type="button" @click="goFilings">查看原文 ›</button>
                  </div>
                </li>
              </ol>
            </template>

            <template v-else-if="evidenceTab === 'facts'">
              <StateBlock
                v-if="factsPhase === 'loading'"
                state="loading"
                :rows="3"
              />
              <StateBlock
                v-else-if="factsPhase === 'unavailable'"
                state="empty"
                title="该证券没有 CIK"
                message="没有 SEC 申报就没有 XBRL 事实，本栏没有可引用的关键数据。"
              />
              <StateBlock
                v-else-if="!facts.length"
                state="empty"
                title="没有可引用的关键数据"
                :message="`观察日 ${AUX_AS_OF} 之前没有已申报事实。`"
              />
              <ol v-else class="item-list">
                <li v-for="(fact, index) in facts" :key="`fact-${fact.concept}-${fact.periodEnd}-${index}`">
                  <span class="line-index">{{ index + 1 }}</span>
                  <div class="evidence-text">
                    <p class="page-ref">报告期 {{ fact.periodEnd }} · {{ fact.form }} · 申报 {{ fact.filed }}</p>
                    <p class="excerpt">
                      {{ fact.concept }}：{{ fact.value }} {{ fact.unit }}（申报号 {{ fact.accessionNo }}）
                    </p>
                    <a v-if="fact.sourceUrl" :href="fact.sourceUrl" target="_blank" rel="noreferrer noopener">
                      查看原文 ›
                    </a>
                  </div>
                </li>
              </ol>
              <p v-if="metricRows.length" class="footnote">
                正文里的指标行 {{ metricRows.length }} 条，申报号与来源见上表。
              </p>
            </template>

            <template v-else>
              <StateBlock
                v-if="!cashFlowOption"
                state="empty"
                title="本页没有渲染任何图表"
                :message="cashFlowReason"
              />
              <ol v-else class="item-list">
                <li>
                  <span class="line-index">1</span>
                  <div class="evidence-text">
                    <p class="page-ref">现金流与利润匹配（双柱）</p>
                    <p class="excerpt">
                      {{ cashConcept?.concept }} 与 {{ profitConcept?.concept }}，报告期
                      {{ facts.map((fact) => fact.periodEnd).join("、") }}
                    </p>
                    <p class="muted">数据源 /api/v1/sec/facts?cik=&as_of={{ AUX_AS_OF }}</p>
                  </div>
                </li>
              </ol>
            </template>
          </div>

          <p v-if="copyState" class="footnote" role="status">{{ copyState }}</p>
          <p v-if="exportState" class="footnote" role="status">{{ exportState }}</p>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="generatedAt"
        :sources="sourceList"
        :stale="phase !== 'ready'"
        :note="`报告正文来自 /analysis/runs/{run_id}；规则结论观察日 ${AUX_AS_OF}；${report.notice || '接口未提供来源说明'}`"
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

.crumb strong {
  color: var(--text);
  font-weight: 600;
}

.strip {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) minmax(0, 1.5fr) minmax(0, 1.6fr);
  gap: 16px;
  align-items: center;
}

.logo {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
}

.strip-name {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.strip-meta {
  margin: 4px 0 0;
  font-size: 12px;
}

.quote {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  min-width: 0;
}

/* 行情空态文案较长时按两行截断，避免挤到右侧的口径列表。 */
.quote > .muted {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.strip-price {
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.strip-price small {
  margin-left: 3px;
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
}

.strip-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 16px;
  margin: 0;
}

.kv {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 3px;
}

.kv dt {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  font-size: 12px;
}

.kv dd {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
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

.notice {
  margin-bottom: 12px;
}

.block {
  margin-top: 16px;
  border-top: 1px solid var(--line-soft);
  padding-top: 12px;
}

.block h3 {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 600;
}

.item-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 10px;
}

.item-list li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border-bottom: 1px solid var(--line-soft);
  padding-bottom: 10px;
}

.item-list li:last-child {
  border-bottom: 0;
  padding-bottom: 0;
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

.item-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.item-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.item-list .item-text {
  margin: 0;
  flex: 1;
}

.item-actions {
  margin-top: 6px;
}

.risk-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.risk-card {
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--danger) 10%, transparent);
  padding: 10px 12px;
}

.unevaluated {
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
}

.unevaluated summary {
  cursor: pointer;
}

.plain-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: grid;
  gap: 6px;
}

.plain-list li {
  display: flex;
  gap: 8px;
}

.plain-list strong {
  flex: none;
  color: var(--text);
  font-weight: 600;
}

.cash-chart {
  width: 100%;
  height: 260px;
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
  font-size: 12px;
}

.conclusion {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  padding: 12px 14px;
}

.conclusion.tone-ok {
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
  background: color-mix(in srgb, var(--ok) 10%, transparent);
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
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}

.evidence-body {
  margin-top: 12px;
}

.evidence-text {
  flex: 1;
  display: grid;
  gap: 4px;
}

.page-ref {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.excerpt {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  word-break: break-word;
}

.evidence-text a {
  color: var(--accent);
  font-size: 12px;
  text-decoration: none;
}

.footnote {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 1280px) {
  .strip {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .risk-cards {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
