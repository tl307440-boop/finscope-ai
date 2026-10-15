<script setup lang="ts">
/**
 * 总览驾驶舱。
 *
 * 规格书要求本页展示「全球经济状态、A股市场温度、热点、重点公司、数据更新状态」。
 * 这里只呈现后端真实返回的字段：没有数据就显示后端给的空状态或「不可用」，
 * 任何分数、条数、日期都用 asNumber/asString 收窄后再渲染，不写死演示数值。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import {
  asList,
  asNumber,
  asRecord,
  asString,
  dataOf,
  formatMoment,
  listOf,
} from "../api/json"
import { useSessionStore } from "../stores/auth"

/** 四个指数的搜索符号；id 全部由 /instruments/search 实时换取，不写死 UUID。 */
const INDEX_SYMBOLS = ["000001.SH", "000300.SH", "399001.SZ", "399006.SZ"]

type Phase = "loading" | "ready" | "error"

interface MetaInfo {
  asOf: string | null
  source: string | null
  stale: boolean
  dataMode: string | null
  notice: string | null
  requestId: string | null
}

interface TemperatureFactor {
  code: string
  label: string
  weight: number | null
  score: number | null
  raw: string
  note: string
}

interface TemperatureState {
  status: string
  label: string
  score: number | null
  coverage: string
  notice: string
  factors: TemperatureFactor[]
}

interface MacroRegion {
  code: string
  label: string
  reason: string
  evidence: number
}

interface MacroState {
  pitDate: string
  regimeCode: string
  regions: MacroRegion[]
  notice: string
  version: string
}

interface ChinaSeries {
  code: string
  name: string
  status: string
  observationCount: number | null
  latestValue: string | null
  latestDate: string | null
  source: string | null
}

interface ChinaState {
  series: ChinaSeries[]
  notice: string
}

interface HotspotState {
  label: string
  count: number | null
}

interface ProviderState {
  code: string
  credentialRequired: boolean | null
  credentialPresent: boolean | null
  availability: string
  healthStatus: string | null
  healthAt: string | null
}

interface IndexRow {
  symbol: string
  name: string
  barCount: number
  latestTradeDate: string | null
  problem: string | null
}

interface DigestLine {
  title: string
  summary: string
}

interface DigestState {
  lines: DigestLine[]
  signedOut: boolean
}

interface JobSummary {
  total: number
  succeeded: number
  failed: number
  running: number
  other: number
}

const phase = ref<Phase>("loading")
const errorMessage = ref("")
const errorCode = ref("")
const requestId = ref("")
const meta = ref<MetaInfo>({
  asOf: null,
  source: null,
  stale: false,
  dataMode: null,
  notice: null,
  requestId: null,
})

const sectionErrors = ref<Record<string, string>>({})
/** 首屏之后点「刷新」时为 true：模板里不能用 phase === "loading" 判断，因为视图分支已把 phase 收窄成 "ready"。 */
const busy = ref(false)

const temperature = ref<TemperatureState | null>(null)
const macro = ref<MacroState | null>(null)
const china = ref<ChinaState | null>(null)
const hotspots = ref<HotspotState | null>(null)
const digest = ref<DigestState | null>(null)

const readyState = ref<string | null>(null)
const readyChecks = ref<{ key: string; label: string; status: string; detail: string }[]>([])
const jobSummary = ref<JobSummary | null>(null)
const providers = ref<ProviderState[]>([])
const indexRows = ref<IndexRow[]>([])
const indexLoading = ref(false)
const indexLoaded = ref(false)

const session = useSessionStore()
const staleNotice = computed(() => meta.value.stale && meta.value.notice !== null)
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")
const asOfText = computed(() => formatMoment(meta.value.asOf))
const missingIndexRows = computed(() => indexRows.value.filter((row) => row.problem !== null).length)

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError("无法连接接口", "NETWORK_ERROR", null)
}

function readMeta(body: unknown): MetaInfo {
  const record = asRecord(asRecord(body)?.meta)
  const stale = record?.stale
  return {
    asOf: asString(record?.as_of),
    source: asString(record?.source),
    stale: typeof stale === "boolean" ? stale : false,
    dataMode: asString(record?.data_mode),
    notice: asString(record?.notice),
    requestId: asString(record?.request_id),
  }
}

/** 通用 GET：非成功状态码一律抛出 ApiError，调用方决定是整页失败还是单卡片失败。 */
async function getJson(path: string, token: string | null): Promise<{ data: Record<string, unknown> | null; meta: MetaInfo }> {
  const headers = new Headers({ Accept: "application/json" })
  if (token) {
    headers.set("Authorization", `Bearer ${token}`)
  }
  const response = await fetch(apiUrl(path), { headers, credentials: "include" })
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  return { data: dataOf(body), meta: readMeta(body) }
}

function recordError(key: string, error: unknown): void {
  sectionErrors.value = { ...sectionErrors.value, [key]: toApiError(error).message }
}

async function loadTemperature(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/market/temperature", null)
    temperature.value = {
      status: asString(data?.status) ?? "unknown",
      label: asString(data?.label) ?? "无法判断",
      score: asNumber(data?.score),
      coverage: asString(data?.coverage) ?? "",
      notice: asString(data?.notice) ?? "",
      factors: listOf(data?.factors).map((item) => ({
        code: asString(item.code) ?? "",
        label: asString(item.label) ?? "未命名因子",
        weight: asNumber(item.weight),
        score: asNumber(item.score),
        raw: asString(item.raw) ?? "—",
        note: asString(item.note) ?? "",
      })),
    }
  } catch (error) {
    recordError("temperature", error)
  }
}

function readRegions(data: Record<string, unknown> | null): MacroRegion[] {
  const keys: { key: string; label: string }[] = [
    { key: "growth", label: "增长" },
    { key: "inflation", label: "通胀" },
    { key: "liquidity", label: "流动性" },
  ]
  const regions: MacroRegion[] = []
  for (const item of keys) {
    const record = asRecord(data?.[item.key])
    if (!record) {
      continue
    }
    regions.push({
      code: asString(record.code) ?? item.key,
      label: item.label,
      reason: asString(record.reason) ?? "unknown",
      evidence: asList(record.evidence).length,
    })
  }
  return regions
}

async function loadMacro(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/macro/assessments", null)
    macro.value = {
      pitDate: asString(data?.pit_date) ?? "—",
      regimeCode: asString(data?.regime_code) ?? "unknown",
      regions: readRegions(data),
      notice: asString(data?.notice) ?? "",
      version: asString(data?.version) ?? "",
    }
  } catch (error) {
    recordError("macro", error)
  }
  try {
    const { data } = await getJson("/api/v1/macro/china", null)
    china.value = {
      series: listOf(data?.series).map((item) => ({
        code: asString(item.code) ?? "",
        name: asString(item.name) ?? "未命名序列",
        status: asString(item.status) ?? "unknown",
        observationCount: asNumber(item.observation_count),
        latestValue: asString(item.latest_value),
        latestDate: asString(item.latest_observation_date),
        source: asString(item.source),
      })),
      notice: asString(data?.notice) ?? "",
    }
  } catch (error) {
    recordError("china", error)
  }
}

async function loadHotspots(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/market/hotspots", null)
    hotspots.value = {
      label: asString(data?.label) ?? "暂无热点数据",
      count: asNumber(data?.count),
    }
  } catch (error) {
    recordError("hotspots", error)
  }
}

async function loadDigest(): Promise<void> {
  if (!session.accessToken) {
    digest.value = { lines: [], signedOut: true }
    return
  }
  try {
    const { data } = await getJson("/api/v1/me/digest", session.accessToken)
    digest.value = {
      lines: listOf(data?.items).map((item) => ({
        title: asString(item.title) ?? "未命名条目",
        summary: asString(item.summary) ?? "",
      })),
      signedOut: false,
    }
  } catch (error) {
    recordError("digest", error)
  }
}

function checkDetail(record: Record<string, unknown>): string {
  const latency = asNumber(record.latency_ms)
  if (latency !== null) {
    return `延迟 ${latency} 毫秒`
  }
  const reason = asString(record.reason)
  if (reason) {
    return `原因 ${reason}`
  }
  const lastSeen = asString(record.last_seen_at)
  if (lastSeen) {
    return `最近心跳 ${formatMoment(lastSeen)}`
  }
  return "—"
}

async function loadReady(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/health/ready", null)
    readyState.value = asString(data?.status) ?? "unknown"
    const checks = asRecord(data?.checks)
    const rows: { key: string; label: string; status: string; detail: string }[] = []
    for (const item of [
      { key: "database", label: "数据库" },
      { key: "worker", label: "后台 Worker" },
    ]) {
      const record = asRecord(checks?.[item.key])
      if (!record) {
        continue
      }
      rows.push({
        key: item.key,
        label: item.label,
        status: asString(record.status) ?? "unknown",
        detail: checkDetail(record),
      })
    }
    readyChecks.value = rows
  } catch (error) {
    recordError("ready", error)
  }
}

function summarizeJobs(items: Record<string, unknown>[]): JobSummary {
  const summary: JobSummary = { total: items.length, succeeded: 0, failed: 0, running: 0, other: 0 }
  for (const item of items) {
    const status = asString(item.status) ?? ""
    if (status === "succeeded") {
      summary.succeeded += 1
    } else if (status === "failed") {
      summary.failed += 1
    } else if (status === "running" || status === "queued") {
      summary.running += 1
    } else {
      summary.other += 1
    }
  }
  return summary
}

async function loadJobs(): Promise<void> {
  if (!session.accessToken) {
    return
  }
  try {
    const { data } = await getJson("/api/v1/admin/sync-jobs?limit=20", session.accessToken)
    jobSummary.value = summarizeJobs(listOf(data?.jobs))
  } catch (error) {
    recordError("jobs", error)
  }
}

async function loadProviders(): Promise<void> {
  if (!session.accessToken) {
    return
  }
  try {
    const { data } = await getJson("/api/v1/admin/providers", session.accessToken)
    providers.value = listOf(data?.providers).map((item) => ({
      code: asString(item.code) ?? "unknown",
      credentialRequired: typeof item.credential_required === "boolean" ? item.credential_required : null,
      credentialPresent: typeof item.credential_present === "boolean" ? item.credential_present : null,
      availability: asString(item.availability) ?? "unknown",
      healthStatus: asString(item.last_health_status),
      healthAt: asString(item.last_health_at),
    }))
  } catch (error) {
    recordError("providers", error)
  }
}

/** 四个指数的入库情况：搜索换 id，再取日线，条数与最新交易日都来自真实返回。 */
async function loadIndexRows(): Promise<void> {
  indexLoading.value = true
  const rows: IndexRow[] = []
  for (const symbol of INDEX_SYMBOLS) {
    const query = symbol.slice(0, 6)
    try {
      const { data } = await getJson(
        `/api/v1/instruments/search?q=${query}&market=CN`,
        null,
      )
      const hit = listOf(data?.results).find(
        (item) => asString(item.display_symbol) === symbol,
      )
      const instrumentId = asString(hit?.id)
      if (!hit || !instrumentId) {
        rows.push({
          symbol,
          name: "—",
          barCount: 0,
          latestTradeDate: null,
          problem: "该指数未入库",
        })
        continue
      }
      const bars = await getJson(`/api/v1/market/bars/${instrumentId}?limit=3000`, null)
      const items = listOf(bars.data?.bars)
      rows.push({
        symbol,
        name: asString(hit.name_cn) ?? asString(hit.name_en) ?? "—",
        barCount: items.length,
        latestTradeDate: asString(items[items.length - 1]?.trade_date),
        problem: asString(bars.data?.empty_message),
      })
    } catch (error) {
      rows.push({
        symbol,
        name: "—",
        barCount: 0,
        latestTradeDate: null,
        problem: toApiError(error).message,
      })
    }
  }
  indexRows.value = rows
  indexLoading.value = false
  indexLoaded.value = true
}

async function load(): Promise<void> {
  busy.value = true
  phase.value = "loading"
  errorMessage.value = ""
  errorCode.value = ""
  requestId.value = ""
  sectionErrors.value = {}
  temperature.value = null
  macro.value = null
  china.value = null
  hotspots.value = null
  digest.value = null
  readyState.value = null
  readyChecks.value = []
  jobSummary.value = null
  providers.value = []

  // 先要一份 meta，用于顶部「数据模式 / 是否过期」提示；失败即整页错误。
  try {
    try {
      const { meta: head } = await getJson("/api/v1/health/ready", null)
      meta.value = head
      requestId.value = head.requestId ?? ""
      phase.value = "ready"
    } catch (error) {
      const apiError = toApiError(error)
      errorMessage.value = apiError.message
      errorCode.value = apiError.code
      requestId.value = apiError.requestId ?? ""
      phase.value = "error"
      return
    }

    await Promise.all([
      loadTemperature(),
      loadMacro(),
      loadHotspots(),
      loadReady(),
      loadDigest(),
      loadJobs(),
      loadProviders(),
    ])
  } finally {
    busy.value = false
  }
}

function statusText(value: string): string {
  if (value === "ok" || value === "ready" || value === "up" || value === "alive") {
    return "正常"
  }
  if (value === "insufficient") {
    return "不可用"
  }
  if (value === "unavailable") {
    return "不可用"
  }
  if (value === "configured") {
    return "已配置"
  }
  if (value === "not_required") {
    return "无需凭据"
  }
  if (value === "down") {
    return "不可用"
  }
  if (value === "stale") {
    return "数据陈旧"
  }
  return value
}

function statusClass(value: string): string {
  if (value === "ok" || value === "ready" || value === "up" || value === "alive" || value === "configured" || value === "not_required") {
    return "chip chip-ok"
  }
  if (value === "insufficient" || value === "unavailable" || value === "down") {
    return "chip chip-warn"
  }
  return "chip"
}

function factorScoreText(value: number | null): string {
  return value === null ? "样本不足" : value.toFixed(1)
}

function regionReason(value: string): string {
  return value === "insufficient_data" ? "样本不足" : value
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <header class="page-head">
      <div>
        <h1>总览驾驶舱</h1>
        <p class="muted">
          只展示已落库、可追溯的数据；没有数据的位置显示后端的空状态，不用演示数字顶替。
        </p>
      </div>
      <el-button :loading="busy" @click="load">刷新</el-button>
    </header>

    <p v-if="phase === 'ready'" class="meta-line muted">
      数据时间 {{ asOfText }} · 来源 {{ meta.source ?? "—" }} · 数据模式
      {{ meta.dataMode ?? "—" }} · 请求号 {{ requestId || "—" }}
    </p>

    <div v-if="demoMode" class="banner" role="status">
      当前返回的是{{ meta.dataMode }}数据，不是实时行情，请勿据此下单。
    </div>
    <div v-if="staleNotice" class="banner" role="status">
      数据已过期（stale）：{{ meta.notice }}
    </div>

    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在汇总驾驶舱数据</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法加载驾驶舱</h1>
      <p>{{ errorMessage }}</p>
      <p v-if="errorCode" class="muted">错误码 {{ errorCode }} · 请求号 {{ requestId || "—" }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <div class="dash-grid">
        <div class="dash-main">
          <section class="panel">
            <div class="panel-head">
              <h2>全球经济状态</h2>
              <span v-if="macro" class="chip" :class="macro.regimeCode === 'insufficient_data' ? 'chip-warn' : 'chip-ok'">
                {{ macro.regimeCode }}
              </span>
            </div>
            <el-skeleton v-if="!macro && !sectionErrors.macro" :rows="3" animated />
            <div v-else-if="sectionErrors.macro" class="section-error">
              <p>{{ sectionErrors.macro }}</p>
              <button class="retry" type="button" @click="loadMacro">重试</button>
            </div>
            <template v-else-if="macro">
              <el-descriptions :column="2" border size="small">
                <el-descriptions-item label="PIT 日期">{{ macro.pitDate }}</el-descriptions-item>
                <el-descriptions-item label="规则版本">{{ macro.version || "—" }}</el-descriptions-item>
              </el-descriptions>
              <el-table :data="macro.regions" size="small" stripe>
                <el-table-column prop="label" label="维度" width="100" />
                <el-table-column label="结论" min-width="160">
                  <template #default="{ row }">{{ regionReason(row.reason) }}</template>
                </el-table-column>
                <el-table-column label="证据条数" width="110">
                  <template #default="{ row }">{{ row.evidence }}</template>
                </el-table-column>
              </el-table>
              <p class="muted">{{ macro.notice }}</p>

              <h3 class="sub-title">中国宏观序列落库状态</h3>
              <el-skeleton v-if="!china && !sectionErrors.china" :rows="3" animated />
              <div v-else-if="sectionErrors.china" class="section-error">
                <p>{{ sectionErrors.china }}</p>
                <button class="retry" type="button" @click="loadMacro">重试</button>
              </div>
              <template v-else-if="china">
                <el-table :data="china.series" size="small" stripe>
                  <el-table-column prop="name" label="序列" min-width="180" />
                  <el-table-column label="状态" width="110">
                    <template #default="{ row }">
                      <span :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="条数" width="90">
                    <template #default="{ row }">{{ row.observationCount ?? "—" }}</template>
                  </el-table-column>
                  <el-table-column label="最新值" min-width="110">
                    <template #default="{ row }">{{ row.latestValue ?? "—" }}</template>
                  </el-table-column>
                  <el-table-column label="最新日期" width="120">
                    <template #default="{ row }">{{ row.latestDate ?? "—" }}</template>
                  </el-table-column>
                </el-table>
                <el-empty v-if="!china.series.length" description="后端没有返回任何中国宏观序列" />
                <p class="muted">{{ china.notice }}</p>
              </template>
            </template>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>A股市场温度</h2>
              <span v-if="temperature" :class="statusClass(temperature.status)">
                {{ statusText(temperature.status) }}
              </span>
            </div>
            <el-skeleton v-if="!temperature && !sectionErrors.temperature" :rows="3" animated />
            <div v-else-if="sectionErrors.temperature" class="section-error">
              <p>{{ sectionErrors.temperature }}</p>
              <button class="retry" type="button" @click="loadTemperature">重试</button>
            </div>
            <template v-else-if="temperature">
              <p class="state">{{ temperature.label }}</p>
              <p class="muted">分数 {{ temperature.score === null ? "—" : temperature.score.toFixed(1) }} · {{ temperature.coverage }}</p>
              <el-table v-if="temperature.factors.length" :data="temperature.factors" size="small" stripe>
                <el-table-column prop="label" label="因子" width="120" />
                <el-table-column label="权重" width="90">
                  <template #default="{ row }">{{ row.weight === null ? "—" : row.weight }}</template>
                </el-table-column>
                <el-table-column label="得分" width="110">
                  <template #default="{ row }">{{ factorScoreText(row.score) }}</template>
                </el-table-column>
                <el-table-column prop="raw" label="依据" min-width="220" />
              </el-table>
              <el-empty v-else description="后端判定样本不足，没有可展示的因子得分" />
              <p class="muted">{{ temperature.notice }}</p>
            </template>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>热点（合规公告流）</h2>
            </div>
            <el-skeleton v-if="!hotspots && !sectionErrors.hotspots" :rows="2" animated />
            <div v-else-if="sectionErrors.hotspots" class="section-error">
              <p>{{ sectionErrors.hotspots }}</p>
              <button class="retry" type="button" @click="loadHotspots">重试</button>
            </div>
            <template v-else-if="hotspots">
              <p class="state">{{ hotspots.label }}</p>
              <p class="muted">
                近 14 天收录 {{ hotspots.count ?? 0 }} 条合规公告。完整主题、时间线与事件明细见
                <RouterLink to="/hotspots">热点主题</RouterLink>。
              </p>
              <el-empty v-if="!hotspots.count" description="暂无可展示的数据：近 14 天没有收录合规公告" />
            </template>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>重点公司</h2>
            </div>
            <el-empty description="暂无可展示的数据" />
            <p class="muted">
              后端没有「重点公司」接口，本页不编造推荐标的。请用检索定位具体公司后查看其自选与研判：
              <RouterLink to="/company">公司分析</RouterLink>、
              <RouterLink to="/watchlist">自选列表</RouterLink>、
              <RouterLink to="/board">研究看板</RouterLink>。
            </p>
          </section>
        </div>

        <aside class="dash-side">
          <section class="panel">
            <div class="panel-head">
              <h2>数据更新状态</h2>
              <el-button size="small" text :loading="busy" @click="load">刷新</el-button>
            </div>

            <h3 class="sub-title">接口就绪</h3>
            <el-skeleton v-if="!readyState && !sectionErrors.ready" :rows="2" animated />
            <div v-else-if="sectionErrors.ready" class="section-error">
              <p>{{ sectionErrors.ready }}</p>
              <button class="retry" type="button" @click="loadReady">重试</button>
            </div>
            <template v-else-if="readyState">
              <p :class="statusClass(readyState)">{{ statusText(readyState) }}</p>
              <el-table :data="readyChecks" size="small" stripe>
                <el-table-column prop="label" label="组件" width="110" />
                <el-table-column label="状态" width="100">
                  <template #default="{ row }">
                    <span :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="detail" label="明细" min-width="130" />
              </el-table>
            </template>

            <h3 class="sub-title">同步任务</h3>
            <p v-if="!session.accessToken" class="muted">登录后可查看同步任务与数据源配置（需管理员）。</p>
            <template v-else>
              <div v-if="sectionErrors.jobs" class="section-error">
                <p>{{ sectionErrors.jobs }}</p>
                <button class="retry" type="button" @click="loadJobs">重试</button>
              </div>
              <p v-else-if="jobSummary" class="muted">
                任务 {{ jobSummary.total }} 条：成功 {{ jobSummary.succeeded }}，失败
                {{ jobSummary.failed }}，进行中 {{ jobSummary.running }}，其他 {{ jobSummary.other }}。
              </p>
              <el-empty v-if="jobSummary && !jobSummary.total" description="还没有同步任务记录" />
            </template>

            <h3 class="sub-title">数据源配置</h3>
            <div v-if="sectionErrors.providers" class="section-error">
              <p>{{ sectionErrors.providers }}</p>
              <button class="retry" type="button" @click="loadProviders">重试</button>
            </div>
            <el-table v-else-if="providers.length" :data="providers" size="small" stripe>
              <el-table-column prop="code" label="数据源" width="100" />
              <el-table-column label="可用性" width="110">
                <template #default="{ row }">
                  <span :class="statusClass(row.availability)">{{ statusText(row.availability) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="凭据" width="90">
                <template #default="{ row }">
                  {{ row.credentialRequired === false ? "不需要" : row.credentialPresent ? "已配置" : "缺失" }}
                </template>
              </el-table-column>
              <el-table-column label="最近体检" min-width="130">
                <template #default="{ row }">
                  <span v-if="row.healthStatus" :class="statusClass(row.healthStatus)">
                    {{ statusText(row.healthStatus) }}
                  </span>
                  <span v-else>—</span>
                  <span v-if="row.healthStatus" class="muted"> {{ formatMoment(row.healthAt) }}</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-else-if="session.accessToken && !sectionErrors.providers" description="后端没有返回数据源配置" />
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>指数日线覆盖</h2>
              <el-button size="small" text :loading="indexLoading" @click="loadIndexRows">
                {{ indexLoaded ? "重新检查" : "检查" }}
              </el-button>
            </div>
            <p v-if="!indexLoaded && !indexLoading" class="muted">
              点击「检查」按 000001.SH、000300.SH、399001.SZ、399006.SZ 逐个查询入库条数与最新交易日。
            </p>
            <el-skeleton v-if="indexLoading" :rows="4" animated />
            <template v-else-if="indexLoaded">
              <p v-if="missingIndexRows" class="muted">
                有 {{ missingIndexRows }} 个指数尚未入库，状态列给出后端返回的原因。
              </p>
              <el-table :data="indexRows" size="small" stripe>
                <el-table-column prop="symbol" label="指数" width="110" />
                <el-table-column prop="name" label="名称" min-width="110" />
                <el-table-column label="日线条数" width="100">
                  <template #default="{ row }">{{ row.barCount }}</template>
                </el-table-column>
                <el-table-column label="最新交易日" width="120">
                  <template #default="{ row }">{{ row.latestTradeDate ?? "—" }}</template>
                </el-table-column>
                <el-table-column label="状态" min-width="200">
                  <template #default="{ row }">
                    <span v-if="row.problem" class="chip chip-warn">{{ row.problem }}</span>
                    <span v-else class="chip chip-ok">已有日线</span>
                  </template>
                </el-table-column>
              </el-table>
            </template>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>站内摘要</h2>
            </div>
            <el-skeleton v-if="!digest && !sectionErrors.digest" :rows="3" animated />
            <div v-else-if="sectionErrors.digest" class="section-error">
              <p>{{ sectionErrors.digest }}</p>
              <button class="retry" type="button" @click="loadDigest">重试</button>
            </div>
            <template v-else-if="digest">
              <p v-if="digest.signedOut" class="muted">未登录，摘要需要登录后查看。</p>
              <template v-else>
                <el-empty v-if="!digest.lines.length" description="后端没有返回摘要条目" />
                <ul v-else class="digest-list">
                  <li v-for="line in digest.lines" :key="line.title">
                    <strong>{{ line.title }}</strong>
                    <span class="muted">{{ line.summary }}</span>
                  </li>
                </ul>
              </template>
            </template>
          </section>
        </aside>
      </div>
    </template>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 22px;
}

.meta-line {
  margin: 0 0 16px;
  word-break: break-all;
}

.dash-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.panel-head h2 {
  margin: 0;
  font-size: 17px;
}

.sub-title {
  margin: 18px 0 8px;
  font-size: 14px;
  color: var(--muted);
}

.state {
  margin: 4px 0;
  font-size: 22px;
  font-weight: 700;
}

.chip {
  display: inline-block;
  padding: 2px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12px;
  color: var(--muted);
}

.chip-ok {
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
}

.chip-warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
}

.section-error {
  margin: 8px 0;
  padding: 12px 16px;
  border: 1px solid color-mix(in srgb, var(--danger) 40%, transparent);
  border-radius: 12px;
  background: rgb(248 113 113 / 10%);
  color: var(--danger);
}

.section-error p {
  margin: 0 0 8px;
}

.digest-list {
  display: grid;
  gap: 10px;
  margin: 0;
  padding-left: 18px;
}

.digest-list span {
  display: block;
}

@media (min-width: 1100px) {
  .dash-grid {
    grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr);
  }
}
</style>
