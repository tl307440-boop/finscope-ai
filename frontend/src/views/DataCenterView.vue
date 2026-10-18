<script setup lang="ts">
/**
 * 数据中心（效果图 10）。
 *
 * 页面上的数字只来自两个读接口：
 *   GET  /api/v1/admin/providers   → 数据源接入方式与最近健康检查
 *   GET  /api/v1/admin/sync-jobs   → 最近同步任务（limit 上限 50）
 * 写操作保持原有行为：fred-syncs、sync-jobs（akshare_daily 入队）、
 * sync-jobs/{job_id}/retries（失败任务重试）、provider-probes（模拟上游故障）。
 *
 * 后端没有各数据表行数 / 最新日期的汇总接口，所以这里不展示「数据覆盖」数字；
 * 同步任务也不返回开始 / 结束时间与日志，表格不出这两列。
 */
import { computed, onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  Bell,
  Clock,
  Connection,
  DataLine,
  Files,
  List,
  Odometer,
  Refresh,
  Timer,
  Warning,
} from "@element-plus/icons-vue"
import FreshnessPanel from "../components/ui/FreshnessPanel.vue"
import IntegrationsPanel from "../components/ui/IntegrationsPanel.vue"
import MiniRing from "../components/ui/MiniRing.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../api/client"
import { formatShanghai } from "../api/health"
import { describeJobScope, enqueueMessage, providerAccess, type ProviderAccess } from "../api/jobs"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import { describeProbeFailure } from "../api/providerProbe"
import {
  BAR_RANGE_PRESETS,
  FRED_RANGE_PRESETS,
  presetRange,
  type SyncRangePreset,
} from "../api/syncRange"
import { restoreSession, useSessionStore } from "../stores/auth"
import ChinaImportPanel from "./parts/data/ChinaImportPanel.vue"

const session = useSessionStore()
const router = useRouter()
const route = useRoute()

interface ProviderRow {
  code: string
  credentialRequired: boolean
  credentialPresent: boolean
  availability: string
  lastHealthStatus: string | null
  lastHealthAt: string | null
}

interface SyncJobRow {
  id: string
  jobType: string
  status: string
  dedupeKey: string
  attempts: number
  maxAttempts: number
  rowsWritten: number
  errorCode: string | null
  errorSummary: string | null
  progressStage: string
  requestedAt: string | null
}

type Tone = "ok" | "danger" | "accent" | "warn" | "muted"

const JOB_LIMIT = 50

const phase = ref<"loading" | "ready" | "denied" | "error">("loading")
const rows = ref<ProviderRow[]>([])
const jobs = ref<SyncJobRow[]>([])
const failure = ref("无法读取数据源状态")
const jobsAtLimit = ref(false)
const jobsFailed = ref("")

/* ---- 管理动作 ---- */
const scenario = ref("timeout")
const probePhase = ref<"idle" | "loading" | "done" | "error">("idle")
const probeText = ref("")
const syncCode = ref("UNRATE")
const syncStart = ref("")
const syncEnd = ref("")
const syncPhase = ref<"idle" | "loading" | "done" | "error">("idle")
const syncText = ref("")
const barSymbol = ref("600519.SH")
const barStart = ref("")
const barEnd = ref("")
const barPhase = ref<"idle" | "loading" | "done" | "error">("idle")
const barText = ref("")

/* ---- 表格筛选（全部在前端过滤，不额外请求后端）---- */
const filterSource = ref("all")
const filterStatus = ref("all")
const filterRange = ref<string[] | null>(null)
const retryBusy = ref("")
const retryNote = ref("")

/* ---- 同步任务状态的时间窗（前端切片，不改请求参数）---- */
const windowKey = ref<"7d" | "30d" | "all">("7d")

/** 时间窗写进查询参数：刷新与整页截图之间保持一致，也方便复制链接。 */
function applyWindow(value: "7d" | "30d" | "all"): void {
  windowKey.value = value
  void router.replace({ query: { ...route.query, window: value } })
}

function readWindow(): "7d" | "30d" | "all" {
  const raw = route.query.window
  return raw === "30d" || raw === "all" || raw === "7d" ? raw : "7d"
}

const SOURCE_LABEL: Record<string, string> = {
  fred: "FRED",
  sec_edgar: "SEC EDGAR",
  tushare: "Tushare",
  akshare: "AKShare",
}

const SOURCE_DESC: Record<string, string> = {
  fred: "美联储圣路易斯分行 · 美国宏观经济序列",
  sec_edgar: "美国证监会电子披露系统 · 美股财报与公告",
  tushare: "A 股行情与基本面数据",
  akshare: "开源数据接口 · A 股日线行情",
}

const JOB_LABEL: Record<string, string> = {
  fred_series: "FRED 宏观序列同步",
  akshare_daily: "A 股日线行情同步",
}

const JOB_SOURCE: Record<string, string> = {
  fred_series: "FRED",
  akshare_daily: "AKShare",
}

const JOB_STATUS_TEXT: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  running: "运行中",
  queued: "等待中",
  cancelled: "已取消",
}

const JOB_STATUS_TONE: Record<string, Tone> = {
  succeeded: "ok",
  failed: "danger",
  running: "accent",
  queued: "warn",
  cancelled: "muted",
}

const FRED_SERIES = [
  { code: "GDP", name: "美国国内生产总值" },
  { code: "UNRATE", name: "美国失业率" },
  { code: "PAYEMS", name: "非农就业人数" },
  { code: "CPIAUCSL", name: "美国消费者物价指数" },
  { code: "PCEPILFE", name: "核心个人消费支出价格指数" },
  { code: "FEDFUNDS", name: "联邦基金有效利率" },
  { code: "DGS2", name: "美国2年期国债收益率" },
  { code: "DGS10", name: "美国10年期国债收益率" },
  { code: "T10Y2Y", name: "10年与2年期国债利差" },
  { code: "CES0500000003", name: "平均时薪（私营非农）" },
  { code: "GDPC1", name: "实际国内生产总值" },
  { code: "PCEC96", name: "实际个人消费支出" },
  { code: "DGS5", name: "美国5年期国债收益率" },
  { code: "DGS20", name: "美国20年期国债收益率" },
  { code: "DFEDTARL", name: "联邦基金目标利率下限" },
  { code: "DFEDTARU", name: "联邦基金目标利率上限" },
  { code: "ICSA", name: "初请失业金人数" },
  { code: "ISM_MFG", name: "ISM 制造业 PMI" },
  { code: "ISM_SVC", name: "ISM 服务业 PMI" },
]

const SYNC_OPTIONS = [
  { value: "600519.SH", label: "600519.SH 贵州茅台" },
  { value: "601318.SH", label: "601318.SH 中国平安" },
  { value: "000858.SZ", label: "000858.SZ 五粮液" },
  { value: "000001.SH", label: "000001.SH 上证指数" },
  { value: "399001.SZ", label: "399001.SZ 深证成指" },
  { value: "399006.SZ", label: "399006.SZ 创业板指" },
  { value: "000300.SH", label: "000300.SH 沪深300" },
]

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  jobsFailed.value = ""
  try {
    await loadProviders()
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === "FORBIDDEN") {
      phase.value = "denied"
      return
    }
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
    return
  }
  try {
    await loadJobs()
  } catch (caught) {
    jobsFailed.value = caught instanceof ApiError ? caught.message : "无法读取同步任务"
  }
  phase.value = "ready"
}

function authRetry(): () => Promise<string | null> {
  return async () => {
    const restored = await restoreSession()
    return restored ? session.accessToken : null
  }
}

async function loadProviders(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/admin/providers",
    { method: "GET" },
    session.accessToken,
    authRetry(),
  )
  const body = await readJson(response)
  if (response.status === 403 || response.status === 401) {
    throw new ApiError("当前账号无权访问数据中心", "FORBIDDEN", null)
  }
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const data = asRecord(asRecord(body)?.data)
  rows.value = listOf(data?.providers).flatMap((record) => {
    const code = asString(record.code)
    if (!code) {
      return []
    }
    return [
      {
        code,
        credentialRequired: record.credential_required === true,
        credentialPresent: record.credential_present === true,
        availability: asString(record.availability) ?? "unavailable",
        lastHealthStatus: asString(record.last_health_status),
        lastHealthAt: asString(record.last_health_at),
      },
    ]
  })
}

async function loadJobs(): Promise<void> {
  const response = await authorizedFetch(
    `/api/v1/admin/sync-jobs?limit=${JOB_LIMIT}`,
    { method: "GET" },
    session.accessToken,
    authRetry(),
  )
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const data = asRecord(asRecord(body)?.data)
  const listed = listOf(data?.jobs)
  jobs.value = listed.flatMap((record) => {
    const id = asString(record.id)
    const status = asString(record.status)
    if (!id || !status) {
      return []
    }
    return [
      {
        id,
        jobType: asString(record.job_type) ?? "unknown",
        status,
        dedupeKey: asString(record.dedupe_key) ?? "",
        attempts: asNumber(record.attempts) ?? 0,
        maxAttempts: asNumber(record.max_attempts) ?? 0,
        rowsWritten: asNumber(record.rows_written) ?? 0,
        errorCode: asString(record.error_code),
        errorSummary: asString(record.error_summary),
        progressStage: asString(record.progress_stage) ?? "",
        requestedAt: asString(record.requested_at),
      },
    ]
  })
  jobsAtLimit.value = listed.length >= JOB_LIMIT
}

const jobsReloading = ref(false)

async function reloadJobs(): Promise<void> {
  jobsFailed.value = ""
  jobsReloading.value = true
  try {
    await loadJobs()
  } catch (caught) {
    jobsFailed.value = caught instanceof ApiError ? caught.message : "无法读取同步任务"
  } finally {
    jobsReloading.value = false
  }
}

/* ---- 数据源 ---- */
function accessOf(row: ProviderRow): ProviderAccess {
  return providerAccess(row)
}

/** 「可用」只代表接入方式齐全且最近一次检查没有失败，不代表数据已经同步入库。 */
function providerIsHealthy(row: ProviderRow): boolean {
  return accessOf(row).mode !== "unavailable" && row.lastHealthStatus !== "down"
}

const healthyCount = computed<number>(() => rows.value.filter(providerIsHealthy).length)
const abnormalCount = computed<number>(() => rows.value.length - healthyCount.value)

function providerName(row: ProviderRow): string {
  return SOURCE_LABEL[row.code] ?? row.code
}

function providerDesc(row: ProviderRow): string {
  return SOURCE_DESC[row.code] ?? "其他数据源"
}

function providerLogo(row: ProviderRow): string {
  return providerName(row).slice(0, 2).toUpperCase()
}

function healthTone(row: ProviderRow): Tone {
  if (row.lastHealthStatus === "down") {
    return "warn"
  }
  if (row.lastHealthStatus === "up" && row.lastHealthAt) {
    return "ok"
  }
  return "muted"
}

function healthText(row: ProviderRow): string {
  if (row.lastHealthStatus === "down") {
    return "检查失败"
  }
  if (row.lastHealthStatus === "up" && row.lastHealthAt) {
    return "运行正常"
  }
  return "尚未检查"
}

/** 给管理员的一句操作建议；没有需要处理的事项时不显示。 */
function providerHint(row: ProviderRow): string {
  const mode = accessOf(row).mode
  if (mode === "public_csv") {
    return "当前使用 FRED 官方公开 CSV，只含最新修订值；配置 FRED_API_KEY 可切换到官方 API，保留历史版本数据。"
  }
  if (mode === "unavailable") {
    if (row.code === "tushare") {
      return "配置 TUSHARE_TOKEN 后可启用。"
    }
    if (row.code === "sec_edgar") {
      return "配置 SEC_USER_AGENT（附联系邮箱）后可启用。"
    }
    return "缺少访问凭证，配置后可启用。"
  }
  if (row.lastHealthStatus === "down") {
    return "最近一次连通性检查失败，可重新发起一次同步确认是否恢复。"
  }
  return ""
}

function minutesAgo(iso: string | null): number | null {
  if (!iso) {
    return null
  }
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }
  return Math.max(0, Math.round((Date.now() - parsed.getTime()) / 60000))
}

function relativeText(iso: string | null): string {
  const minutes = minutesAgo(iso)
  if (minutes === null) {
    return "尚无记录"
  }
  if (minutes < 1) {
    return "刚刚"
  }
  if (minutes < 60) {
    return `${minutes} 分钟前`
  }
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    return `${hours} 小时前`
  }
  return `${Math.floor(hours / 24)} 天前`
}

/** 越新鲜条越长，3 天以上按最短显示；没有检查记录时条长为 0。 */
function freshnessBar(row: ProviderRow): number {
  const minutes = minutesAgo(row.lastHealthAt)
  if (minutes === null) {
    return 0
  }
  return Math.max(6, Math.min(100, Math.round(100 - minutes / 43.2)))
}

/* ---- 顶部统计：全部来自最近任务列表 ---- */
function countAll(status: string): number {
  return jobs.value.filter((job) => job.status === status).length
}

const allSucceeded = computed<number>(() => countAll("succeeded"))
const allFailed = computed<number>(() => countAll("failed"))
const allActive = computed<number>(() => countAll("running") + countAll("queued"))

function stampOf(job: SyncJobRow): number {
  const value = job.requestedAt ? new Date(job.requestedAt).getTime() : Number.NaN
  return Number.isNaN(value) ? 0 : value
}

const latestJob = computed<SyncJobRow | null>(() =>
  jobs.value.reduce<SyncJobRow | null>((best, job) => (!best || stampOf(job) > stampOf(best) ? job : best), null),
)

const latestSucceeded = computed<SyncJobRow | null>(() =>
  jobs.value
    .filter((job) => job.status === "succeeded")
    .reduce<SyncJobRow | null>((best, job) => (!best || stampOf(job) > stampOf(best) ? job : best), null),
)

const jobCountText = computed<string>(() => (jobsAtLimit.value ? `${JOB_LIMIT}+` : String(jobs.value.length)))

/* ---- 同步任务状态：只统计时间窗内的最近任务 ---- */
const jobsInWindow = computed<SyncJobRow[]>(() => {
  if (windowKey.value === "all") {
    return jobs.value
  }
  const days = windowKey.value === "7d" ? 7 : 30
  const floor = Date.now() - days * 86400000
  return jobs.value.filter((job) => stampOf(job) >= floor)
})

function countOf(status: string): number {
  return jobsInWindow.value.filter((job) => job.status === status).length
}

const countSucceeded = computed<number>(() => countOf("succeeded"))
const countFailed = computed<number>(() => countOf("failed"))
const countRunning = computed<number>(() => countOf("running"))
const countQueued = computed<number>(() => countOf("queued"))
const countOther = computed<number>(
  () => jobsInWindow.value.length - countSucceeded.value - countFailed.value - countRunning.value - countQueued.value,
)

const ringSegments = computed(() => [
  { label: "成功", ratio: countSucceeded.value, tone: "ok" as const },
  { label: "失败", ratio: countFailed.value, tone: "danger" as const },
  { label: "运行中", ratio: countRunning.value, tone: "accent" as const },
  { label: "等待中", ratio: countQueued.value, tone: "warn" as const },
])

const ringLegend = computed(() =>
  ringSegments.value.map((segment) => ({
    label: segment.label,
    count: segment.ratio,
    tone: segment.tone,
    percent: jobsInWindow.value.length
      ? `${((segment.ratio / jobsInWindow.value.length) * 100).toFixed(1)}%`
      : "—",
  })),
)

const windowHint = computed<string>(() =>
  jobsAtLimit.value
    ? `只统计最近登记的 ${JOB_LIMIT} 条同步任务`
    : `统计最近登记的 ${jobs.value.length} 条同步任务`,
)

const queueStats = computed(() => [
  { label: "运行中", value: countRunning.value, caption: "正在执行", tone: "accent" },
  { label: "等待中", value: countQueued.value, caption: "排队等待执行", tone: "warn" },
  { label: "失败待重试", value: countFailed.value, caption: "可在任务列表重试", tone: "danger" },
])

/** 队列处理进度 = 时间窗内已经跑完（成功 + 失败）的比例。 */
const queueDone = computed<number>(() => countSucceeded.value + countFailed.value)
const queueTotal = computed<number>(() => jobsInWindow.value.length)
const queuePercent = computed<number>(() =>
  queueTotal.value ? Math.round((queueDone.value / queueTotal.value) * 100) : 0,
)

/* ---- 最近同步任务表格 ---- */
const statusOptions = Object.entries(JOB_STATUS_TEXT).map(([value, label]) => ({ value, label }))
const sourceOptions = Object.entries(JOB_SOURCE).map(([value, label]) => ({ value, label }))

const tableRows = computed(() =>
  jobs.value.filter((job) => {
    if (filterSource.value !== "all" && job.jobType !== filterSource.value) {
      return false
    }
    if (filterStatus.value !== "all" && job.status !== filterStatus.value) {
      return false
    }
    if (filterRange.value && filterRange.value.length === 2 && job.requestedAt) {
      const stamp = new Date(job.requestedAt).getTime()
      const from = new Date(`${filterRange.value[0]}T00:00:00`).getTime()
      const to = new Date(`${filterRange.value[1]}T23:59:59`).getTime()
      if (Number.isNaN(stamp) || stamp < from || stamp > to) {
        return false
      }
    }
    return true
  }),
)

const hasFailedRows = computed<boolean>(() => tableRows.value.some((job) => job.status === "failed"))

function resetFilters(): void {
  filterSource.value = "all"
  filterStatus.value = "all"
  filterRange.value = null
}

function jobTitle(job: SyncJobRow): string {
  return JOB_LABEL[job.jobType] ?? "其他同步任务"
}

function jobName(job: SyncJobRow): string {
  const base = jobTitle(job)
  if (job.status === "failed" && job.attempts > 1) {
    return `${base}（第 ${job.attempts} 次尝试）`
  }
  return base
}

function scopeText(job: SyncJobRow): string {
  return describeJobScope(job.jobType, job.dedupeKey)
}

function sourceText(job: SyncJobRow): string {
  return JOB_SOURCE[job.jobType] ?? "其他"
}

function statusText(job: SyncJobRow): string {
  if (job.status === "queued" && job.progressStage === "retry") {
    return "等待重试"
  }
  return JOB_STATUS_TEXT[job.status] ?? "状态未知"
}

function rowTone(job: SyncJobRow): Tone {
  return JOB_STATUS_TONE[job.status] ?? "muted"
}

/** 只有真的写入了行数才显示数字；排队 / 失败时后端返回 0，这里显示占位而不是 0。 */
function rowsText(job: SyncJobRow): string {
  if (job.status !== "succeeded") {
    return "—"
  }
  return job.rowsWritten.toLocaleString("en-US")
}

function attemptText(job: SyncJobRow): string {
  return `${job.attempts}/${job.maxAttempts}`
}

function failureText(job: SyncJobRow): string {
  return job.errorSummary ?? "未记录失败原因"
}

async function retryJob(job: SyncJobRow): Promise<void> {
  if (retryBusy.value) {
    return
  }
  retryBusy.value = job.id
  retryNote.value = ""
  try {
    const response = await authorizedFetch(
      `/api/v1/admin/sync-jobs/${job.id}/retries`,
      { method: "POST" },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    retryNote.value = `已重试：${enqueueMessage(data?.created === true)}`
    await reloadJobs()
  } catch (caught) {
    retryNote.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
  } finally {
    retryBusy.value = ""
  }
}

/* ---- 写操作，行为不变 ---- */
async function runProbe(): Promise<void> {
  probePhase.value = "loading"
  probeText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/provider-probes",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: scenario.value }),
      },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    probeText.value = describeProbeFailure(body)
    probePhase.value = response.ok ? "done" : "error"
  } catch (caught) {
    probeText.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    probePhase.value = "error"
  }
}

function fillFredRange(preset: SyncRangePreset): void {
  const range = presetRange(preset, new Date())
  syncStart.value = range.start
  syncEnd.value = range.end
}

function fillBarRange(preset: SyncRangePreset): void {
  const range = presetRange(preset, new Date())
  barStart.value = range.start
  barEnd.value = range.end
}

async function syncFred(): Promise<void> {
  syncPhase.value = "loading"
  syncText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/fred-syncs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          series_code: syncCode.value.trim(),
          observation_start: syncStart.value,
          observation_end: syncEnd.value,
        }),
      },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const written = asNumber(data?.rows_written)
    syncText.value = written === null ? "同步完成" : `已写入 ${written} 条修订`
    syncPhase.value = "done"
  } catch (caught) {
    syncText.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    syncPhase.value = "error"
  }
}

async function syncBars(): Promise<void> {
  barPhase.value = "loading"
  barText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/sync-jobs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_type: "akshare_daily",
          scope: {
            display_symbol: barSymbol.value,
            observation_start: barStart.value,
            observation_end: barEnd.value,
            adjustment: "none",
          },
        }),
      },
      session.accessToken,
      authRetry(),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    barText.value = enqueueMessage(data?.created === true)
    barPhase.value = "done"
    await reloadJobs()
  } catch (caught) {
    barText.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    barPhase.value = "error"
  }
}

function goQueue(): void {
  document.getElementById("sync-queue")?.scrollIntoView({ behavior: "smooth", block: "start" })
}

onMounted(() => {
  windowKey.value = readWindow()
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="数据中心"
      subtitle="管理全市场数据源，监控数据同步状态，保障数据及时、准确、完整"
    >
      <template #actions>
        <el-button :icon="Refresh" :disabled="phase === 'loading'" @click="load">刷新</el-button>
        <el-button :icon="Connection" :disabled="phase !== 'ready'" @click="goQueue">任务管理</el-button>
      </template>
    </PageHeader>

    <div v-if="phase === 'loading'" class="stack">
      <PanelCard title="数据源状态">
        <StateBlock state="loading" :rows="4" />
      </PanelCard>
    </div>

    <div v-else-if="phase === 'denied'" class="stack">
      <PanelCard title="数据源状态">
        <StateBlock state="denied" message="数据中心仅对管理员开放，当前账号无法查看数据源与同步任务" />
      </PanelCard>
    </div>

    <div v-else-if="phase === 'error'" class="stack">
      <PanelCard title="数据源状态">
        <StateBlock state="error" :message="failure" @retry="load" />
      </PanelCard>
    </div>

    <div v-else class="stack">
      <!-- 顶部统计 -->
      <div class="grid-4">
        <StatCard
          title="数据源"
          :value="rows.length"
          unit="个"
          :caption="`可用 ${healthyCount} · 需处理 ${abnormalCount}`"
          info="可用指接入方式齐全且最近一次检查没有失败，不代表数据已全部入库"
          tone="accent"
        >
          <template #icon><el-icon><Files /></el-icon></template>
        </StatCard>
        <StatCard
          title="同步任务"
          :value="jobsFailed ? null : jobCountText"
          unit="条"
          :caption="jobsFailed ? '同步任务暂时无法读取' : `成功 ${allSucceeded} · 失败 ${allFailed} · 进行中 ${allActive}`"
          :info="`最近登记的同步任务，最多统计 ${JOB_LIMIT} 条`"
          tone="cyan"
        >
          <template #icon><el-icon><List /></el-icon></template>
        </StatCard>
        <StatCard
          title="最近同步"
          :value="latestJob ? relativeText(latestJob.requestedAt) : null"
          :caption="latestJob ? `${jobTitle(latestJob)} · ${statusText(latestJob)}` : '还没有同步任务'"
          :info="latestJob ? `登记于 ${formatShanghai(latestJob.requestedAt ?? undefined)}` : '最近一条同步任务的登记时间'"
          tone="violet"
        >
          <template #icon><el-icon><Clock /></el-icon></template>
        </StatCard>
        <StatCard
          title="最近成功写入"
          :value="latestSucceeded ? latestSucceeded.rowsWritten.toLocaleString('en-US') : null"
          unit="行"
          :caption="
            latestSucceeded
              ? [jobTitle(latestSucceeded), scopeText(latestSucceeded)].filter(Boolean).join(' · ')
              : '最近的任务里还没有成功写入'
          "
          info="最近一条成功任务实际写入的行数，重复数据不计入"
          tone="ok"
        >
          <template #icon><el-icon><DataLine /></el-icon></template>
        </StatCard>
      </div>

      <!-- 数据源列表 -->
      <PanelCard
        title="数据源状态"
        info="接入方式看密钥是否齐全；健康状态来自最近一次同步时的连通性检查"
        flush
      >
        <template #icon><el-icon><Files /></el-icon></template>
        <template #actions>
          <div class="src-summary">
            <span class="src-total">共 {{ rows.length }} 个数据源</span>
            <span class="src-counter"><i class="dot ok" aria-hidden="true" />可用 {{ healthyCount }}</span>
            <span class="src-counter"><i class="dot danger" aria-hidden="true" />需处理 {{ abnormalCount }}</span>
          </div>
        </template>

        <StateBlock v-if="!rows.length" state="empty" message="还没有登记任何数据源" />
        <div v-else class="src-list">
          <div class="src-row src-headrow" aria-hidden="true">
            <span>数据源</span>
            <span>接入方式</span>
            <span>健康状态</span>
            <span>最近检查</span>
          </div>
          <div v-for="row in rows" :key="row.code" class="src-row">
            <div class="src-id">
              <span class="src-logo" aria-hidden="true">{{ providerLogo(row) }}</span>
              <div class="src-text">
                <p class="src-name">{{ providerName(row) }}</p>
                <p class="src-desc">{{ providerDesc(row) }}</p>
              </div>
            </div>
            <div class="src-cell">
              <StatusPill :text="accessOf(row).text" :tone="accessOf(row).tone" />
            </div>
            <div class="src-cell">
              <StatusPill :text="healthText(row)" :tone="healthTone(row)" dot />
            </div>
            <div class="src-fresh">
              <span class="fresh-track">
                <span
                  class="fresh-fill"
                  :class="providerIsHealthy(row) ? 'ok' : 'warn'"
                  :style="{ width: `${freshnessBar(row)}%` }"
                />
              </span>
              <span
                class="fresh-time"
                :title="row.lastHealthAt ? formatShanghai(row.lastHealthAt) : '还没有检查记录'"
              >{{ relativeText(row.lastHealthAt) }}</span>
            </div>
            <p v-if="providerHint(row)" class="src-hint">{{ providerHint(row) }}</p>
          </div>
        </div>
      </PanelCard>

      <FreshnessPanel />

      <!-- 密钥与数据源配置：在界面里填，不用改服务器 .env -->
      <IntegrationsPanel />

      <!-- 任务统计 -->
      <div class="grid-2">
        <PanelCard title="同步任务状态" :info="windowHint">
          <template #icon><el-icon><Odometer /></el-icon></template>
          <template #actions>
            <el-select
              :model-value="windowKey"
              size="small"
              style="width: 108px"
              aria-label="统计时间范围"
              @update:model-value="applyWindow"
            >
              <el-option label="近 7 天" value="7d" />
              <el-option label="近 30 天" value="30d" />
              <el-option label="全部" value="all" />
            </el-select>
          </template>

          <p v-if="jobsFailed" class="inline-note danger">{{ jobsFailed }}</p>
          <StateBlock
            v-else-if="!jobsInWindow.length"
            state="empty"
            :message="jobs.length ? '所选时间范围内没有同步任务' : '还没有同步任务，可在页面底部发起一次同步'"
          />
          <div v-else class="ring-row">
            <MiniRing
              :segments="ringSegments"
              :size="132"
              :thickness="14"
              :center-text="String(queueTotal)"
              center-caption="总任务数"
            />
            <ul class="ring-legend">
              <li v-for="item in ringLegend" :key="item.label">
                <i :class="['dot', item.tone]" aria-hidden="true" />
                <span class="legend-label">{{ item.label }}</span>
                <span class="legend-count">{{ item.count }}</span>
                <span class="legend-percent">{{ item.percent }}</span>
              </li>
            </ul>
          </div>
          <p v-if="countOther" class="card-note">另有 {{ countOther }} 条已取消的任务。</p>
        </PanelCard>

        <PanelCard title="任务队列" :info="windowHint">
          <template #icon><el-icon><Timer /></el-icon></template>
          <p v-if="jobsFailed" class="inline-note danger">{{ jobsFailed }}</p>
          <template v-else>
            <dl class="queue-stats">
              <div v-for="item in queueStats" :key="item.label">
                <dt>{{ item.label }}</dt>
                <dd :class="['queue-value', item.value ? item.tone : '']">{{ item.value }}<small>条</small></dd>
                <dd class="queue-sub">{{ item.caption }}</dd>
              </div>
            </dl>
            <div class="queue-progress">
              <div
                class="queue-track"
                role="progressbar"
                :aria-valuenow="queuePercent"
                aria-valuemin="0"
                aria-valuemax="100"
                aria-label="队列处理进度"
              >
                <span class="queue-fill" :style="{ width: `${queuePercent}%` }" />
              </div>
              <p class="queue-caption">
                队列处理进度 {{ queuePercent }}%（{{ queueDone }} / {{ queueTotal }}）
                <span class="muted">· 已完成包含成功与失败</span>
              </p>
            </div>
          </template>
        </PanelCard>
      </div>

      <!-- 最近同步任务 -->
      <PanelCard id="sync-queue" title="最近同步任务" info="按登记时间倒序，失败任务可直接重试" flush>
        <template #icon><el-icon><Bell /></el-icon></template>
        <template #actions>
          <span class="filter-count">共 {{ tableRows.length }} 条</span>
        </template>

        <div class="filter-row">
          <el-select v-model="filterSource" size="small" style="width: 140px" aria-label="数据源">
            <el-option label="全部数据源" value="all" />
            <el-option v-for="item in sourceOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="filterStatus" size="small" style="width: 130px" aria-label="任务状态">
            <el-option label="全部状态" value="all" />
            <el-option v-for="item in statusOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-date-picker
            v-model="filterRange"
            size="small"
            type="daterange"
            value-format="YYYY-MM-DD"
            range-separator="~"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            style="width: 260px"
          />
          <el-button size="small" text @click="resetFilters">重置</el-button>
          <el-button size="small" :icon="Refresh" :loading="jobsReloading" @click="reloadJobs">刷新</el-button>
        </div>

        <p v-if="jobsFailed" class="inline-note danger">{{ jobsFailed }}</p>
        <StateBlock
          v-else-if="!tableRows.length"
          state="empty"
          :message="jobs.length ? '当前筛选条件没有匹配的任务' : '还没有同步任务'"
        />
        <el-table v-else :data="tableRows" size="small" style="width: 100%">
          <el-table-column type="index" label="#" width="52" />
          <el-table-column label="任务名称" min-width="220">
            <template #default="{ row }">
              <span class="cell-strong">{{ jobName(row) }}</span>
              <span v-if="scopeText(row)" class="cell-sub">{{ scopeText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="数据源" width="110">
            <template #default="{ row }">{{ sourceText(row) }}</template>
          </el-table-column>
          <el-table-column label="登记时间" width="180">
            <template #default="{ row }">
              <span class="tabular">{{ formatShanghai(row.requestedAt ?? undefined) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="尝试次数" width="90" align="right">
            <template #default="{ row }"><span class="tabular">{{ attemptText(row) }}</span></template>
          </el-table-column>
          <el-table-column label="写入行数" width="110" align="right">
            <template #default="{ row }"><span class="tabular">{{ rowsText(row) }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <StatusPill :text="statusText(row)" :tone="rowTone(row)" dot />
            </template>
          </el-table-column>
          <el-table-column v-if="hasFailedRows" label="失败原因" min-width="200">
            <template #default="{ row }">
              <span v-if="row.status === 'failed'" class="cell-fail" :title="failureText(row)">
                {{ failureText(row) }}
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column v-if="hasFailedRows" label="操作" width="96" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'failed'"
                size="small"
                type="primary"
                :loading="retryBusy === row.id"
                @click="retryJob(row)"
              >
                重试
              </el-button>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        <p v-if="retryNote" class="inline-note" role="status">{{ retryNote }}</p>
      </PanelCard>

      <!-- 管理动作 -->
      <div class="grid-3">
        <PanelCard
          title="同步 FRED 序列"
          info="按观察区间拉取美国宏观序列。5–50 年与「全部」填入截至今天的区间；全部是 36500 天，正好是接口上限，再多一天会被拒绝。未配置 FRED_API_KEY 时只能取到最新修订值"
        >
          <template #icon><el-icon><DataLine /></el-icon></template>
          <form class="form-stack" @submit.prevent="syncFred">
            <el-select
              v-model="syncCode"
              filterable
              size="small"
              style="width: 100%"
              aria-label="序列代码"
              placeholder="选择序列"
            >
              <el-option
                v-for="item in FRED_SERIES"
                :key="item.code"
                :label="`${item.code} ${item.name}`"
                :value="item.code"
              />
            </el-select>
            <div class="range-picks" role="group" aria-label="观察区间">
              <el-button
                v-for="preset in FRED_RANGE_PRESETS"
                :key="preset.label"
                size="small"
                native-type="button"
                @click="fillFredRange(preset)"
              >
                {{ preset.label }}
              </el-button>
            </div>
            <el-date-picker
              v-model="syncStart"
              size="small"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="起始观察日"
              style="width: 100%"
            />
            <el-date-picker
              v-model="syncEnd"
              size="small"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="结束观察日"
              style="width: 100%"
            />
            <el-button size="small" type="primary" native-type="submit" :loading="syncPhase === 'loading'">
              同步
            </el-button>
          </form>
          <p v-if="syncText" class="inline-note" :class="{ danger: syncPhase === 'error' }" role="status">
            {{ syncText }}
          </p>
        </PanelCard>

        <PanelCard
          title="同步 A 股日线"
          info="把所选标的与日期区间加入同步队列，由后台任务依次执行。全部是 3660 天，正好是日线接口上限"
        >
          <template #icon><el-icon><DataLine /></el-icon></template>
          <form class="form-stack" @submit.prevent="syncBars">
            <el-select v-model="barSymbol" size="small" style="width: 100%" aria-label="同步标的">
              <el-option v-for="item in SYNC_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
            <div class="range-picks" role="group" aria-label="日线区间">
              <el-button
                v-for="preset in BAR_RANGE_PRESETS"
                :key="preset.label"
                size="small"
                native-type="button"
                @click="fillBarRange(preset)"
              >
                {{ preset.label }}
              </el-button>
            </div>
            <el-date-picker
              v-model="barStart"
              size="small"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="开始日期"
              style="width: 100%"
            />
            <el-date-picker
              v-model="barEnd"
              size="small"
              type="date"
              value-format="YYYY-MM-DD"
              placeholder="结束日期"
              style="width: 100%"
            />
            <el-button size="small" type="primary" native-type="submit" :loading="barPhase === 'loading'">
              加入队列
            </el-button>
          </form>
          <p v-if="barText" class="inline-note" :class="{ danger: barPhase === 'error' }" role="status">
            {{ barText }}
          </p>
        </PanelCard>

        <PanelCard title="模拟上游故障" info="触发一次模拟的限流、服务不可用或超时，检验故障提示是否清楚">
          <template #icon><el-icon><Warning /></el-icon></template>
          <form class="form-stack" @submit.prevent="runProbe">
            <el-select v-model="scenario" size="small" style="width: 100%" aria-label="故障类型">
              <el-option label="限流（429）" value="status_429" />
              <el-option label="服务不可用（503）" value="status_503" />
              <el-option label="超时" value="timeout" />
            </el-select>
            <el-button size="small" native-type="submit" :loading="probePhase === 'loading'">探测</el-button>
          </form>
          <p v-if="probeText" class="inline-note" :class="{ danger: probePhase === 'error' }" role="status">
            {{ probeText }}
          </p>
          <p class="card-note">只做演练，不会改动已入库的数据。</p>
        </PanelCard>
      </div>

      <ChinaImportPanel />
    </div>
  </main>
</template>

<style scoped>
/* 数据源列表 */
.src-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  color: var(--muted);
}

.src-total {
  color: var(--text);
}

.src-counter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  flex: none;
}

.dot.ok {
  background: var(--ok);
}

.dot.danger {
  background: var(--danger);
}

.dot.accent {
  background: var(--accent);
}

.dot.warn {
  background: var(--warn);
}

.src-list {
  display: grid;
}

.src-row {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(0, 0.8fr) minmax(0, 0.8fr) minmax(0, 1.3fr);
  align-items: center;
  gap: 6px 16px;
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
}

.src-headrow {
  padding: 6px 0 8px;
  border-top: 0;
  font-size: 12px;
  color: var(--muted);
}

.src-id {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.src-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 26%, transparent) inset;
  color: var(--accent);
  font-family: var(--font-num);
  font-size: 13px;
  font-weight: 700;
  flex: none;
}

.src-text {
  min-width: 0;
}

.src-name {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.src-desc {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.src-cell {
  min-width: 0;
}

.src-fresh {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px;
  align-items: center;
  gap: 10px;
}

.fresh-track {
  display: block;
  height: 8px;
  border-radius: 4px;
  background: var(--panel-soft);
  overflow: hidden;
}

.fresh-fill {
  display: block;
  height: 100%;
  border-radius: 4px;
}

.fresh-fill.ok {
  background: linear-gradient(90deg, color-mix(in srgb, var(--accent) 60%, transparent), var(--accent));
}

.fresh-fill.warn {
  background: var(--warn);
}

.fresh-time {
  font-size: 13px;
  color: var(--text);
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.src-hint {
  grid-column: 1 / -1;
  margin: 0;
  padding-left: 46px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

/* 同步任务状态 */
.ring-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.ring-legend {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
  font-size: 13px;
  flex: 1 1 180px;
}

.ring-legend li {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
}

.legend-label {
  color: var(--muted);
}

.legend-count,
.legend-percent {
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
}

.legend-percent {
  color: var(--muted);
  min-width: 48px;
  text-align: right;
}

/* 任务队列 */
.queue-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
}

.queue-stats > div {
  padding: 4px 16px;
  border-left: 1px solid var(--line-soft);
  min-width: 0;
}

.queue-stats > div:first-child {
  padding-left: 0;
  border-left: 0;
}

.queue-stats dt {
  font-size: 13px;
  color: var(--muted);
}

.queue-value {
  margin: 6px 0 2px;
  font-family: var(--font-num);
  font-size: 28px;
  font-weight: 600;
  line-height: 1.1;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.queue-value small {
  margin-left: 3px;
  font-family: var(--font-ui);
  font-size: 13px;
  font-weight: 500;
  color: var(--muted);
}

.queue-value.accent {
  color: var(--accent);
}

.queue-value.warn {
  color: var(--warn);
}

.queue-value.danger {
  color: var(--danger);
}

.queue-sub {
  margin: 0;
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.queue-progress {
  margin-top: 16px;
}

.queue-track {
  height: 8px;
  border-radius: 4px;
  background: var(--panel-soft);
  overflow: hidden;
}

.queue-fill {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--accent);
}

.queue-caption {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

/* 表格与筛选 */
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}

.filter-count {
  font-size: 13px;
  color: var(--muted);
}

.cell-strong {
  display: block;
  font-size: 13px;
  color: var(--text-strong);
}

.cell-sub {
  display: block;
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.cell-fail {
  display: block;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger);
  font-size: 13px;
}

.muted {
  color: var(--muted);
}

/* 表单 */
.form-stack {
  display: grid;
  gap: 10px;
}

.range-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.inline-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.inline-note.danger {
  color: var(--danger);
}

.card-note {
  margin: 12px 0 0;
  font-size: 13px;
  color: var(--muted);
  line-height: 1.6;
}

@media (max-width: 1280px) {
  .src-row {
    grid-template-columns: minmax(0, 1.5fr) minmax(0, 0.9fr) minmax(0, 0.9fr) minmax(0, 1.2fr);
  }
}

@media (max-width: 900px) {
  .src-headrow {
    display: none;
  }

  .src-row {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .src-fresh {
    grid-column: 1 / -1;
  }

  .src-hint {
    padding-left: 0;
  }

  .queue-stats > div {
    padding: 4px 10px;
  }
}
</style>
