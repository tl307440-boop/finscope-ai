<script setup lang="ts">
/**
 * 数据中心（效果图 10）。
 *
 * 数据来源只有两个真实接口，页面上所有数字都直接来自它们的响应：
 *   GET  /api/v1/admin/providers   → 数据源配置与最近健康检查
 *   GET  /api/v1/admin/sync-jobs   → 最近同步任务（limit 上限 50）
 * 三个写接口保持原有行为不变：provider-probes / fred-syncs / akshare-syncs，
 * 外加 POST /api/v1/admin/sync-jobs/{job_id}/retries（失败任务重试）。
 *
 * 刻意不渲染效果图里有、但后端没有对应接口的内容：
 *   数据表数量、成功率、任务结束时间、插入行数以外的队列耗时、查看日志。
 * 这些位置一律留空或写明原因，不编造数字。
 */
import { computed, onMounted, ref } from "vue"
import { useRoute, useRouter } from "vue-router"
import {
  Bell,
  Connection,
  DataLine,
  Files,
  Odometer,
  Refresh,
  Timer,
  Warning,
} from "@element-plus/icons-vue"
import MetricTile from "../components/ui/MetricTile.vue"
import MiniRing from "../components/ui/MiniRing.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../api/client"
import { formatShanghai } from "../api/health"
import { enqueueMessage, healthLabel, jobStatusLabel } from "../api/jobs"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import { providerStatusLabel } from "../api/macro"
import { describeProbeFailure } from "../api/providerProbe"
import { restoreSession, useSessionStore } from "../stores/auth"

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
  attempts: number
  maxAttempts: number
  rowsWritten: number
  errorCode: string | null
  errorSummary: string | null
  progressStage: string
  requestedAt: string | null
}

const phase = ref<"loading" | "ready" | "denied" | "error">("loading")
const rows = ref<ProviderRow[]>([])
const jobs = ref<SyncJobRow[]>([])
const failure = ref("无法读取数据源状态")
const jobsAtLimit = ref(false)
const providersFailed = ref("")
const jobsFailed = ref("")

/* ---- 保持原行为的三组管理动作 ---- */
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
  fred: "美国宏观经济序列",
  sec_edgar: "美国上市公司披露文件",
  tushare: "A 股行情与基本面数据",
  akshare: "A 股日线行情（开源数据源）",
}

const JOB_LABEL: Record<string, string> = {
  fred_series: "FRED 宏观序列同步",
  akshare_daily: "A 股日线行情同步",
}

const JOB_STATUS_TONE: Record<string, "ok" | "danger" | "accent" | "warn" | "muted"> = {
  succeeded: "ok",
  failed: "danger",
  running: "accent",
  queued: "warn",
  cancelled: "muted",
}

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
  providersFailed.value = ""
  jobsFailed.value = ""
  try {
    await loadProviders()
    await loadJobs()
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
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
    phase.value = "denied"
    throw new ApiError("当前账号无权访问数据源管理", "FORBIDDEN", null)
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
    "/api/v1/admin/sync-jobs?limit=50",
    { method: "GET" },
    session.accessToken,
    authRetry(),
  )
  const body = await readJson(response)
  if (!response.ok) {
    return
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
  jobsAtLimit.value = listed.length >= 50
}

const jobsReloading = ref(false)

async function reloadJobs(): Promise<void> {
  jobsFailed.value = ""
  jobsReloading.value = true
  try {
    await loadJobs()
  } catch (caught) {
    jobsFailed.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    jobsReloading.value = false
  }
}

function label(row: ProviderRow): string {
  return providerStatusLabel(row)
}

/** 「运行正常」只代表密钥可用、且最近一次探测没有失败；没有探测记录时如实说明。 */
function providerIsHealthy(row: ProviderRow): boolean {
  return row.availability !== "unavailable" && row.lastHealthStatus !== "down"
}

const healthyCount = computed<number>(() => rows.value.filter(providerIsHealthy).length)
const abnormalCount = computed<number>(() => rows.value.length - healthyCount.value)

function providerDesc(row: ProviderRow): string {
  return SOURCE_DESC[row.code] ?? "尚未在本项目登记说明"
}

function providerLogo(row: ProviderRow): string {
  return (SOURCE_LABEL[row.code] ?? row.code).slice(0, 2).toUpperCase()
}

function providerTone(row: ProviderRow): "ok" | "warn" | "danger" | "muted" {
  if (row.availability === "unavailable") {
    return "danger"
  }
  if (row.lastHealthStatus === "down") {
    return "warn"
  }
  if (!row.lastHealthAt) {
    return "muted"
  }
  return "ok"
}

function providerPill(row: ProviderRow): string {
  if (row.availability === "unavailable") {
    return "未配置密钥"
  }
  if (row.lastHealthStatus === "down") {
    return "最近探测失败"
  }
  if (!row.lastHealthAt) {
    return "尚未探测"
  }
  return "运行正常"
}

function credentialText(row: ProviderRow): string {
  if (row.availability === "unavailable") {
    return "缺少密钥"
  }
  if (!row.credentialRequired) {
    return "不需要密钥"
  }
  return row.credentialPresent ? "密钥已配置" : "密钥未配置"
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

/** 越新鲜条越长；没有任何记录时条长为 0，右侧写明「尚无记录」。 */
function freshnessBar(row: ProviderRow): number {
  const minutes = minutesAgo(row.lastHealthAt)
  if (minutes === null) {
    return 0
  }
  return Math.max(6, Math.min(100, Math.round(100 - minutes / 43.2)))
}

const freshestMinutes = computed<number>(() => {
  const values = rows.value
    .map((row) => minutesAgo(row.lastHealthAt))
    .filter((value): value is number => value !== null)
  return values.length ? Math.max(...values) : 0
})

/* ---- 同步任务状态：只统计接口真实返回的这批任务 ---- */
const jobsInWindow = computed<SyncJobRow[]>(() => {
  if (windowKey.value === "all") {
    return jobs.value
  }
  const days = windowKey.value === "7d" ? 7 : 30
  const floor = Date.now() - days * 86400000
  return jobs.value.filter((job) => {
    if (!job.requestedAt) {
      return false
    }
    const parsed = new Date(job.requestedAt)
    return !Number.isNaN(parsed.getTime()) && parsed.getTime() >= floor
  })
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
    ? `接口按 limit=50 最多返回 50 条，下面统计只覆盖这 50 条最近任务`
    : `统计覆盖接口返回的 ${jobs.value.length} 条最近任务`,
)

/** 队列处理进度 = 这批最近任务里已经跑完（成功 + 失败）的比例。 */
const queueDone = computed<number>(() => countSucceeded.value + countFailed.value)
const queueTotal = computed<number>(() => jobsInWindow.value.length)
const queuePercent = computed<number>(() =>
  queueTotal.value ? Math.round((queueDone.value / queueTotal.value) * 100) : 0,
)

const queueTileInfo = "来自 /api/v1/admin/sync-jobs 返回的最近任务，不是全量任务表"

/* ---- 最近同步任务表格 ---- */
const statusOptions = [
  { value: "queued", label: "等待中" },
  { value: "running", label: "运行中" },
  { value: "succeeded", label: "成功" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
]

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

function resetFilters(): void {
  filterSource.value = "all"
  filterStatus.value = "all"
  filterRange.value = null
}

function jobName(job: SyncJobRow): string {
  const base = JOB_LABEL[job.jobType] ?? `未登记任务类型：${job.jobType}`
  if (job.status === "failed" && job.attempts > 1) {
    return `${base}（第 ${job.attempts} 次尝试）`
  }
  return base
}

function sourceText(job: SyncJobRow): string {
  if (job.jobType === "fred_series") {
    return "FRED"
  }
  if (job.jobType === "akshare_daily") {
    return "AKShare"
  }
  return job.jobType
}

function statusText(job: SyncJobRow): string {
  if (job.status === "running") {
    return "运行中"
  }
  if (job.status === "queued") {
    return "等待中"
  }
  if (job.status === "succeeded") {
    return "成功"
  }
  return jobStatusLabel(job.status)
}

function rowTone(job: SyncJobRow): "ok" | "danger" | "accent" | "warn" | "muted" {
  const tone = JOB_STATUS_TONE[job.status]
  if (tone) {
    return tone
  }
  return "muted"
}

/** 只有真的写入了行数才显示数字；排队/失败时后端返回 0，这里显示占位而不是 0。 */
function rowsText(job: SyncJobRow): string {
  if (job.status !== "succeeded") {
    return "—"
  }
  return job.rowsWritten.toLocaleString("en-US")
}

function attemptText(job: SyncJobRow): string {
  return `${job.attempts}/${job.maxAttempts}`
}

function stageText(job: SyncJobRow): string {
  return job.progressStage || "—"
}

function failureText(job: SyncJobRow): string {
  return job.errorSummary ?? job.errorCode ?? "上游未返回原因"
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
    retryNote.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  } finally {
    retryBusy.value = ""
  }
}

/* ---- 三个原有写操作，行为不变 ---- */
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
    probeText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    probePhase.value = "error"
  }
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
    syncText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
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
    barText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
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
    <template v-if="phase === 'loading'">
      <PageHeader title="数据中心">
        <template #actions>
          <el-button disabled>任务管理</el-button>
        </template>
      </PageHeader>
      <div class="stack">
        <PanelCard title="数据源状态" info="正在读取 /api/v1/admin/providers">
          <StateBlock state="loading" :rows="4" />
        </PanelCard>
      </div>
    </template>

    <template v-else-if="phase === 'denied'">
      <PageHeader title="数据中心" subtitle="管理全市场数据源，监控数据同步状态，保障数据及时、准确、完整" />
      <div class="stack">
        <PanelCard title="数据源状态">
          <StateBlock state="denied" message="当前账号没有管理员权限，无法读取 /api/v1/admin/providers" />
        </PanelCard>
      </div>
    </template>

    <template v-else-if="phase === 'error'">
      <PageHeader title="数据中心" subtitle="管理全市场数据源，监控数据同步状态，保障数据及时、准确、完整" />
      <div class="stack">
        <PanelCard title="数据源状态">
          <StateBlock state="error" :message="failure" @retry="load" />
        </PanelCard>
      </div>
    </template>

    <template v-else>
      <PageHeader
        title="数据中心"
        subtitle="管理全市场数据源，监控数据同步状态，保障数据及时、准确、完整"
      >
        <template #actions>
          <el-button :icon="Refresh" @click="load">刷新</el-button>
          <el-button :icon="Connection" @click="goQueue">任务管理</el-button>
        </template>
      </PageHeader>

      <div class="stack">
        <!-- 数据源状态 -->
        <PanelCard
          title="数据源状态"
          info="来自 /api/v1/admin/providers。密钥可用只代表配置齐全，且最近一次探测没有失败，不代表行情已经同步入库。"
        >
          <template #icon><el-icon><Files /></el-icon></template>
          <template #actions>
            <div class="src-summary">
              <span class="src-total">共 {{ rows.length }} 个数据源</span>
              <span class="src-counter">
                <i class="dot ok" aria-hidden="true" />全部正常 {{ healthyCount }}
              </span>
              <span class="src-counter">
                <i class="dot danger" aria-hidden="true" />异常 {{ abnormalCount }}
              </span>
            </div>
          </template>

          <p v-if="providersFailed" class="inline-note danger">{{ providersFailed }}</p>
          <StateBlock v-else-if="!rows.length" state="empty" message="后端没有返回任何数据源登记" />
          <div v-else class="src-grid">
            <article v-for="row in rows" :key="row.code" class="src-card">
              <header class="src-head">
                <span class="src-logo" aria-hidden="true">{{ providerLogo(row) }}</span>
                <span class="src-name">{{ SOURCE_LABEL[row.code] ?? row.code }}</span>
                <StatusPill :text="providerPill(row)" :tone="providerTone(row)" />
              </header>
              <p class="src-desc">{{ providerDesc(row) }}</p>
              <dl class="src-stats">
                <div>
                  <dt>凭证</dt>
                  <dd :title="label(row)">
                    {{ credentialText(row) }}
                  </dd>
                </div>
                <div>
                  <dt>最后更新</dt>
                  <dd :title="row.lastHealthAt ? formatShanghai(row.lastHealthAt) : '接口没有返回检查时间'">
                    {{ relativeText(row.lastHealthAt) }}
                  </dd>
                </div>
                <div>
                  <dt>健康检查</dt>
                  <dd>{{ healthLabel(row.lastHealthStatus) }}</dd>
                </div>
              </dl>
            </article>
          </div>
          <p class="card-note">
            效果图里的「数据表数量」与「成功率」需要数据覆盖度接口，当前后端没有该端点，因此这两个字段没有渲染。
          </p>
        </PanelCard>

        <!-- 三栏小卡 -->
        <div class="grid-3 section">
          <PanelCard title="数据新鲜度" info="条长按最近一次探测的间隔归一化，最长 3 天；没有探测记录的显示为空条并注明原因">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <StateBlock v-if="!rows.length" state="empty" message="没有数据源登记，无法计算新鲜度" />
            <ul v-else class="fresh-list">
              <li v-for="row in rows" :key="row.code">
                <span class="fresh-name">{{ SOURCE_LABEL[row.code] ?? row.code }}</span>
                <span class="fresh-track">
                  <span
                    class="fresh-fill"
                    :style="{ width: `${freshnessBar(row)}%`, background: providerIsHealthy(row) ? 'var(--accent)' : 'var(--warn)' }"
                  />
                </span>
                <span class="fresh-time">{{ relativeText(row.lastHealthAt) }}</span>
              </li>
            </ul>
            <p v-if="rows.length" class="card-note">
              参照点：当前记录里最久的一次探测是 {{ freshestMinutes }} 分钟前；这里用 /api/v1/admin/providers 的 last_health_at，不含未接入的数据源。
            </p>
          </PanelCard>

          <PanelCard title="同步任务状态" :info="windowHint">
            <template #icon><el-icon><Odometer /></el-icon></template>
            <template #actions>
              <el-select
                :model-value="windowKey"
                size="small"
                style="width: 108px"
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
              :message="jobs.length ? '所选时间窗内没有同步任务' : '还没有同步任务，先在上面的表单里登记一条'"
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
            <p v-if="countOther" class="card-note">另有 {{ countOther }} 条非上述四种状态（已取消或未知）。</p>
          </PanelCard>

          <PanelCard id="sync-queue" title="任务队列" info="来自同一批 /api/v1/admin/sync-jobs 结果，不是独立的队列接口">
            <template #icon><el-icon><Timer /></el-icon></template>
            <div class="queue-tiles">
              <MetricTile
                label="运行中"
                :value="countRunning"
                unit="条"
                delta-label="当前执行"
                :info="queueTileInfo"
              />
              <MetricTile
                label="等待中"
                :value="countQueued"
                unit="条"
                delta-label="排队等待领取"
                :info="queueTileInfo"
              />
              <MetricTile
                label="失败重试"
                :value="countFailed"
                unit="条"
                delta-label="可点重试重新入队"
                :info="queueTileInfo"
              />
              <MetricTile
                label="平均排队时间"
                :value="null"
                delta-label="后端未提供"
                info="sync_jobs 没有暴露 started_at，接口的 requested_at 只是登记时间，无法据此计算真实排队时长；不估算、不编造。"
              />
            </div>
            <div class="queue-progress">
              <div class="queue-track"><span class="queue-fill" :style="{ width: `${queuePercent}%` }" /></div>
              <p class="queue-caption">
                队列处理进度 {{ queuePercent }}%（{{ queueDone }} / {{ queueTotal }}）
                <span class="muted">· 已完成 = 成功 + 失败，覆盖范围见上方说明</span>
              </p>
            </div>
          </PanelCard>
        </div>

        <!-- 最近同步任务 -->
        <PanelCard class="section" title="最近同步任务" info="GET /api/v1/admin/sync-jobs?limit=50，按 requested_at 倒序">
          <template #icon><el-icon><Bell /></el-icon></template>

          <div class="filter-row">
            <el-select v-model="filterSource" size="small" style="width: 148px">
              <el-option label="全部数据源" value="all" />
              <el-option v-for="(text, code) in SOURCE_LABEL" :key="code" :label="text" :value="code" />
            </el-select>
            <el-select v-model="filterStatus" size="small" style="width: 140px">
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
            <el-button size="small" :icon="Refresh" :loading="jobsReloading" @click="reloadJobs">
              刷新
            </el-button>
            <span class="filter-count">共 {{ tableRows.length }} 条</span>
          </div>

          <p v-if="jobsFailed" class="inline-note danger">{{ jobsFailed }}</p>
          <StateBlock
            v-else-if="!tableRows.length"
            state="empty"
            :message="jobs.length ? '当前筛选条件没有匹配的任务' : '还没有同步任务'"
          />
          <el-table v-else :data="tableRows" size="small" style="width: 100%">
            <el-table-column type="index" label="#" width="52" />
            <el-table-column label="任务名称" min-width="200">
              <template #default="{ row }">
                <span class="cell-strong">{{ jobName(row) }}</span>
                <span class="cell-sub">{{ stageText(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="数据源" width="110">
              <template #default="{ row }">{{ sourceText(row) }}</template>
            </el-table-column>
            <el-table-column label="登记时间" width="170">
              <template #default="{ row }">{{ formatShanghai(row.requestedAt ?? undefined) }}</template>
            </el-table-column>
            <el-table-column label="尝试" width="76">
              <template #default="{ row }">{{ attemptText(row) }}</template>
            </el-table-column>
            <el-table-column label="插入行数" width="110" align="right">
              <template #default="{ row }">{{ rowsText(row) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="140">
              <template #default="{ row }">
                <StatusPill :text="statusText(row)" :tone="rowTone(row)" dot />
              </template>
            </el-table-column>
            <el-table-column label="失败原因" min-width="180">
              <template #default="{ row }">
                <span v-if="row.status === 'failed'" class="cell-fail" :title="failureText(row)">
                  {{ failureText(row) }}
                </span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="96" fixed="right">
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
          <p class="card-note">
            效果图里的「结束时间」「日志」两列已省略：SyncJobPublic 不返回 started_at / finished_at，后端也没有任务日志端点，因此无法给出真实值。
          </p>
        </PanelCard>

        <!-- 管理动作 -->
        <div class="grid-3 section">
          <PanelCard title="同步 FRED 序列" info="POST /api/v1/admin/fred-syncs">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <form class="form-stack" @submit.prevent="syncFred">
              <el-input v-model="syncCode" size="small" placeholder="序列代码，例如 UNRATE" />
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

          <PanelCard title="同步 A 股日线" info="POST /api/v1/admin/sync-jobs（job_type=akshare_daily）">
            <template #icon><el-icon><DataLine /></el-icon></template>
            <form class="form-stack" @submit.prevent="syncBars">
              <el-select v-model="barSymbol" size="small" style="width: 100%">
                <el-option v-for="item in SYNC_OPTIONS" :key="item.value" :label="item.label" :value="item.value" />
              </el-select>
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

          <PanelCard title="模拟上游故障" info="POST /api/v1/admin/provider-probes，用于验证错误处理链路">
            <template #icon><el-icon><Warning /></el-icon></template>
            <form class="form-stack" @submit.prevent="runProbe">
              <el-select v-model="scenario" size="small" style="width: 100%">
                <el-option label="限流（429）" value="status_429" />
                <el-option label="服务不可用（503）" value="status_503" />
                <el-option label="超时" value="timeout" />
              </el-select>
              <el-button size="small" native-type="submit" :loading="probePhase === 'loading'">探测</el-button>
            </form>
            <p v-if="probeText" class="inline-note" :class="{ danger: probePhase === 'error' }" role="status">
              {{ probeText }}
            </p>
            <p class="card-note">
              研究者账号做这些操作会返回 403，页面会把后端给的原因原样显示出来。
            </p>
          </PanelCard>
        </div>

        <p class="page-note">
          本页只读取 <code>/api/v1/admin/providers</code> 与 <code>/api/v1/admin/sync-jobs</code>。
          后端没有数据覆盖度、任务日志、队列统计三类端点，效果图里对应的数字一律留空或写明原因。
        </p>
      </div>
    </template>
  </main>
</template>

<style scoped>
.stack {
  display: grid;
  gap: var(--gap);
}

.section {
  margin-top: 0;
}

/* 数据源状态卡右上角计数 */
.src-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 12px;
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

.dot.muted {
  background: var(--muted);
}

.src-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
}

.src-card {
  background: var(--panel-soft);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  display: grid;
  gap: 8px;
  min-width: 0;
}

.src-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.src-logo {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  flex: none;
}

.src-name {
  font-size: 14px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.src-desc {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.src-stats {
  margin: 0;
  padding-top: 8px;
  border-top: 1px solid var(--line-soft);
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.src-stats div {
  min-width: 0;
}

.src-stats dt {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
}

.src-stats dd {
  margin: 3px 0 0;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 数据新鲜度 */
.fresh-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 14px;
}

.fresh-list li {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr) 68px;
  align-items: center;
  gap: 10px;
}

.fresh-name {
  font-size: 12px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.fresh-track {
  display: block;
  height: 10px;
  border-radius: 5px;
  background: var(--panel-soft);
  overflow: hidden;
}

.fresh-fill {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: var(--accent);
}

.fresh-time {
  font-size: 12px;
  color: var(--muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* 同步任务状态 */
.ring-row {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.ring-legend {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  font-size: 12px;
  flex: 1 1 150px;
}

.ring-legend li {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 8px;
}

.legend-label {
  color: var(--muted);
}

.legend-count,
.legend-percent {
  font-variant-numeric: tabular-nums;
}

.legend-percent {
  color: var(--muted);
  min-width: 42px;
  text-align: right;
}

/* 任务队列 */
.queue-tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.queue-progress {
  margin-top: 14px;
}

.queue-track {
  height: 10px;
  border-radius: 5px;
  background: var(--panel-soft);
  overflow: hidden;
}

.queue-fill {
  display: block;
  height: 100%;
  border-radius: 5px;
  background: var(--accent);
}

.queue-caption {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
}

/* 表格与筛选 */
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.filter-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
}

.cell-strong {
  display: block;
  font-size: 13px;
}

.cell-sub {
  display: block;
  font-size: 11px;
  color: var(--muted);
}

.cell-fail {
  display: block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger);
  font-size: 12px;
}

.muted {
  color: var(--muted);
}

/* 表单 */
.form-stack {
  display: grid;
  gap: 10px;
}

.inline-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.inline-note.danger {
  color: var(--danger);
}

.card-note,
.page-note {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.page-note {
  padding-bottom: 4px;
}

.page-note code {
  font-size: 11px;
  color: var(--text);
}

@media (max-width: 1280px) {
  .queue-tiles {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
