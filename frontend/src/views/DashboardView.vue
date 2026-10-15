<script setup lang="ts">
/**
 * 总览驾驶舱。
 *
 * 规格书要求本页展示「全球经济状态、A股市场温度、热点、重点公司、数据更新状态」。
 * 这里只呈现后端真实返回的字段：没有数据就显示后端给的空状态或「不可用」，
 * 任何分数、条数、日期都用 asNumber/asString 收窄后再渲染，不写死演示数值。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsOption } from "echarts"
import {
  DataAnalysis,
  Histogram,
  MagicStick,
  Monitor,
  TrendCharts,
  Trophy,
} from "@element-plus/icons-vue"
import { VChart } from "../charts"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
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
  /** 最近 60 个交易日的收盘价，只有真实返回的日期，不补零 */
  closes: number[]
  latestClose: number | null
  changePercent: number | null
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
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")
const asOfText = computed(() => formatMoment(meta.value.asOf))

/** 市场温度卡片的迷你柱：五个因子的真实得分，取不到就不画。 */
const temperatureFactorPoints = computed<number[]>(() =>
  (temperature.value?.factors ?? [])
    .map((factor) => factor.score)
    .filter((score): score is number => score !== null),
)

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
          closes: [],
          latestClose: null,
          changePercent: null,
          problem: "该指数未入库",
        })
        continue
      }
      const bars = await getJson(`/api/v1/market/bars/${instrumentId}?limit=3000`, null)
      const items = listOf(bars.data?.bars)
      const closes = items
        .map((item) => asNumber(item.close))
        .filter((value): value is number => value !== null)
      const tail = closes.slice(-60)
      const lastClose = closes.length ? closes[closes.length - 1] : null
      const previousClose = closes.length > 1 ? closes[closes.length - 2] : null
      rows.push({
        symbol,
        name: asString(hit.name_cn) ?? asString(hit.name_en) ?? "—",
        barCount: items.length,
        latestTradeDate: asString(items[items.length - 1]?.trade_date),
        closes: tail,
        latestClose: lastClose,
        changePercent:
          lastClose !== null && previousClose !== null && previousClose !== 0
            ? ((lastClose - previousClose) / previousClose) * 100
            : null,
        problem: asString(bars.data?.empty_message),
      })
    } catch (error) {
      rows.push({
        symbol,
        name: "—",
        barCount: 0,
        latestTradeDate: null,
        closes: [],
        latestClose: null,
        changePercent: null,
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
      loadIndexRows(),
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

function regionReason(value: string): string {
  return value === "insufficient_data" ? "样本不足" : value
}

/** 驾驶舱的四张大卡：数值全部来自上面几个接口，取不到就渲染「—」。 */
const cardMacroValue = computed<string>(() => {
  const code = macro.value?.regimeCode ?? ""
  if (!code || code === "unknown" || code === "insufficient_data") {
    return "—"
  }
  return code
})

const cardMacroCaption = computed<string>(() => {
  if (!macro.value) {
    return sectionErrors.value.macro ?? "读取中"
  }
  const regions = macro.value.regions
  if (regions.length && regions.every((item) => item.reason === "insufficient_data")) {
    return "增长/通胀/流动性样本均不足"
  }
  return macro.value.notice || `时点 ${macro.value.pitDate}`
})

const cardTemperatureCaption = computed<string>(() => {
  if (!temperature.value) {
    return sectionErrors.value.temperature ?? "读取中"
  }
  return [temperature.value.label, temperature.value.coverage].filter(Boolean).join(" · ")
})

const cardHotspotCaption = computed<string>(() => {
  if (!hotspots.value) {
    return sectionErrors.value.hotspots ?? "读取中"
  }
  return hotspots.value.label
})

const cardReadyValue = computed<string>(() => {
  if (readyState.value === null) {
    return "—"
  }
  if (readyState.value === "ready" || readyState.value === "ok" || readyState.value === "alive") {
    return "正常"
  }
  return "异常"
})

const cardReadyCaption = computed<string>(() => {
  if (readyChecks.value.length === 0) {
    return sectionErrors.value.ready ?? "就绪检查读取中"
  }
  return readyChecks.value.map((item) => `${item.label} ${item.detail}`).join(" · ")
})

/** 四个指数近 60 个交易日的收盘价，缺失日期不补值。 */
const indexChartOption = computed<EChartsOption>(() => {
  const palette = ["#3b82f6", "#f87171", "#34d399", "#fbbf24"]
  const usable = indexRows.value.filter((row) => row.closes.length > 1)
  const longest = usable.reduce((max, row) => Math.max(max, row.closes.length), 0)
  const axis = Array.from({ length: longest }, (_, index) => `T-${longest - 1 - index}`)
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: {
      data: usable.map((row) => row.symbol),
      textStyle: { color: "#95a6be", fontSize: 11 },
      icon: "circle",
      top: 0,
    },
    grid: { left: 8, right: 8, top: 34, bottom: 4, containLabel: true },
    xAxis: {
      type: "category",
      data: axis,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      axisLine: { lineStyle: { color: "rgba(149,166,190,0.3)" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(149,166,190,0.12)" } },
    },
    series: usable.map((row, index) => ({
      name: row.symbol,
      type: "line" as const,
      data: row.closes.map((value, offset) => [offset + (longest - row.closes.length), value]),
      smooth: false,
      symbol: "none",
      lineStyle: { color: palette[index % palette.length], width: 1.6 },
      itemStyle: { color: palette[index % palette.length] },
    })),
  }
})

/** 数据来源条用的数据源标识。 */
const sourceList = computed<string[]>(() => {
  const source = meta.value.source
  return source ? [source] : []
})

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="page">
    <PageHeader title="智能投研驾驶舱" subtitle="全球视野 · 数据驱动 · AI赋能 · 发现更好的投资机会">
      <template #actions>
        <el-button :loading="busy" @click="load">刷新数据</el-button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'error'" state="error" :message="errorMessage" @retry="load" />

    <template v-else>
      <!-- 顶部四张大卡 -->
      <div class="grid-4">
        <StatCard
          title="全球经济状态"
          :value="cardMacroValue"
          :caption="cardMacroCaption"
          info="来自 /macro/assessments 的历史时点打分；未接 FRED 凭证时增长/通胀/流动性均为样本不足"
        >
          <template #icon><el-icon><TrendCharts /></el-icon></template>
        </StatCard>

        <StatCard
          title="A股市场温度"
          :value="temperature?.score === null || temperature?.score === undefined ? null : temperature.score.toFixed(1)"
          unit="/100"
          :caption="cardTemperatureCaption"
          info="趋势、广度、活跃度、波动、收益五个因子按权重合成；样本不足时后端返回 insufficient"
        >
          <template #icon><el-icon><Histogram /></el-icon></template>
          <template #chart>
            <MiniSpark
              v-if="temperatureFactorPoints.length > 1"
              :points="temperatureFactorPoints"
              kind="bar"
              tone="accent"
              :width="92"
              :height="38"
            />
          </template>
        </StatCard>

        <StatCard
          title="热点主题数"
          :value="hotspots?.count === null || hotspots?.count === undefined ? null : hotspots.count"
          unit="个"
          :caption="cardHotspotCaption"
          info="只统计通过来源白名单校验的公告事件，没有合规来源时不展示情绪分数"
        >
          <template #icon><el-icon><Trophy /></el-icon></template>
        </StatCard>

        <StatCard
          title="数据更新状态"
          :value="cardReadyValue"
          :caption="cardReadyCaption"
          info="来自 /health/ready：数据库与后台 Worker 的就绪检查"
        >
          <template #icon><el-icon><Monitor /></el-icon></template>
        </StatCard>
      </div>

      <!-- 主区：左宽右窄 -->
      <div class="grid-2-1 section">
        <PanelCard title="A股主要指数走势" info="四个已入库指数的收盘价，缺失交易日不补值" more-text="指数行情" more-to="/market/indices">
          <template #icon><el-icon><DataAnalysis /></el-icon></template>
          <StateBlock
            v-if="indexLoading"
            state="loading"
            :rows="5"
          />
          <StateBlock
            v-else-if="indexRows.length && indexRows.every((row) => row.closes.length < 2)"
            state="empty"
            :message="indexRows.find((row) => row.problem)?.problem ?? '四个指数都还没有日线数据，请到数据管理里同步'"
          />
          <VChart v-else class="chart-main" :option="indexChartOption" autoresize />
          <div class="index-strip">
            <div v-for="row in indexRows" :key="row.symbol" class="index-cell">
              <span class="cell-name">{{ row.name }}</span>
              <DeltaValue :value="row.latestClose" :colored="false" size="lg" />
              <DeltaValue :value="row.changePercent" suffix="%" size="sm" />
              <span class="cell-date">{{ row.latestTradeDate ?? "—" }}</span>
            </div>
          </div>
        </PanelCard>

        <PanelCard title="全球宏观概览" info="增长、通胀、流动性三个维度的历史时点判定" more-text="全球宏观" more-to="/macro/us">
          <template #icon><el-icon><MagicStick /></el-icon></template>
          <StateBlock
            v-if="!macro && sectionErrors.macro"
            state="stale"
            :message="sectionErrors.macro"
            @retry="load"
          />
          <template v-else>
            <div class="macro-tiles">
              <MetricTile
                v-for="region in macro?.regions ?? []"
                :key="region.code"
                :label="region.label"
                :value="region.code"
              >
                <template #delta>
                  <StatusPill
                    :text="regionReason(region.reason)"
                    :tone="region.reason === 'insufficient_data' ? 'muted' : 'ok'"
                  />
                </template>
              </MetricTile>
            </div>
            <p v-if="macro?.notice" class="macro-notice">{{ macro.notice }}</p>
            <p class="macro-version">规则版本 {{ macro?.version || "—" }} · 时点 {{ macro?.pitDate || "—" }}</p>

            <h3 class="sub-title">中国宏观序列</h3>
            <ul class="series-list">
              <li v-for="item in china?.series ?? []" :key="item.code">
                <span class="series-name">{{ item.name }}</span>
                <StatusPill
                  :text="item.status === 'available' ? (item.latestValue ?? '已入库') : statusText(item.status)"
                  :tone="item.status === 'available' ? 'ok' : 'muted'"
                />
                <span class="series-date">{{ item.latestDate ?? "" }}</span>
              </li>
              <li v-if="!(china?.series ?? []).length" class="series-empty">
                {{ china?.notice || sectionErrors.china || "读取中" }}
              </li>
            </ul>
          </template>
        </PanelCard>
      </div>

      <!-- 底部三栏 -->
      <div class="grid-3 section">
        <PanelCard title="重点指数与入库情况" info="条数来自 /market/bars 实际返回的行数" flush>
          <template #icon><el-icon><DataAnalysis /></el-icon></template>
          <el-table :data="indexRows" size="small" :show-header="true">
            <el-table-column prop="symbol" label="代码" width="110" />
            <el-table-column prop="name" label="名称" min-width="110" show-overflow-tooltip />
            <el-table-column label="收盘" width="100" align="right">
              <template #default="{ row }">
                <DeltaValue :value="row.latestClose" :colored="false" />
              </template>
            </el-table-column>
            <el-table-column label="日涨跌" width="100" align="right">
              <template #default="{ row }">
                <DeltaValue :value="row.changePercent" suffix="%" />
              </template>
            </el-table-column>
            <el-table-column label="K线条数" width="100" align="right">
              <template #default="{ row }">
                <span class="tabular">{{ row.barCount }}</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <StatusPill
                  :text="row.problem ? '缺数据' : '已入库'"
                  :tone="row.problem ? 'warn' : 'ok'"
                  dot
                />
              </template>
            </el-table-column>
          </el-table>
        </PanelCard>

        <PanelCard title="市场热点与公告" info="只统计通过来源白名单校验的公告；没有合规来源时后端不展示情绪分数" more-text="市场热点" more-to="/hotspots">
          <template #icon><el-icon><Trophy /></el-icon></template>
          <StateBlock
            v-if="hotspots && (hotspots.count === null || hotspots.count === 0)"
            state="empty"
            :message="hotspots.label"
          />
          <div v-else-if="hotspots" class="hotspot-body">
            <p class="hotspot-count">
              <strong>{{ hotspots.count ?? "—" }}</strong>
              <small>条合规公告</small>
            </p>
            <p class="macro-notice">{{ hotspots.label }}</p>
          </div>
          <StateBlock v-else :state="sectionErrors.hotspots ? 'stale' : 'loading'" :message="sectionErrors.hotspots ?? ''" @retry="load" />

          <h3 class="sub-title">数据源状态</h3>
          <ul class="series-list">
            <li v-for="item in providers" :key="item.code">
              <span class="series-name">{{ item.code }}</span>
              <StatusPill
                :text="statusText(item.availability)"
                :tone="item.availability === 'configured' || item.availability === 'not_required' ? 'ok' : 'warn'"
              />
              <span class="series-date">{{ item.healthAt ? formatMoment(item.healthAt) : "" }}</span>
            </li>
            <li v-if="!providers.length" class="series-empty">
              {{ session.accessToken ? "暂未读取到数据源" : "登录管理员账号后可查看数据源状态" }}
            </li>
          </ul>
        </PanelCard>

        <PanelCard title="AI 每日研究摘要" info="来自 /me/digest，只汇总已生成的研究报告与合规公告" more-text="研究报告" more-to="/reports">
          <template #icon><el-icon><MagicStick /></el-icon></template>
          <StateBlock
            v-if="digest?.signedOut"
            state="denied"
            message="登录后可以查看每日研究摘要"
          />
          <StateBlock
            v-else-if="digest && digest.lines.length === 0"
            state="empty"
            message="今天还没有生成研究报告，摘要为空"
          />
          <ol v-else class="digest-list">
            <li v-for="(line, index) in digest?.lines ?? []" :key="index">
              <span class="cite">{{ index + 1 }}</span>
              <strong>{{ line.title }}</strong>
              <p>{{ line.summary }}</p>
            </li>
          </ol>
        </PanelCard>
      </div>

      <SourceFooter
        :as-of="asOfText"
        :sources="sourceList"
        :stale="meta.stale"
        :note="demoMode ? `当前数据模式：${meta.dataMode}` : meta.notice ?? ''"
      />
    </template>
  </div>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.chart-main {
  width: 100%;
  height: 320px;
}

.index-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--line-soft);
}

.index-cell {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.cell-name {
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cell-date {
  font-size: 11px;
  color: var(--muted);
}

.macro-tiles {
  display: grid;
  gap: 10px;
}

.macro-notice {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}

.macro-version {
  margin: 6px 0 0;
  font-size: 11px;
  color: var(--muted);
}

.sub-title {
  margin: 18px 0 8px;
  font-size: 13px;
  font-weight: 600;
}

.series-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.series-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.series-name {
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.series-date {
  color: var(--muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.series-empty {
  display: block !important;
  color: var(--muted);
  font-size: 12px;
}

.hotspot-body {
  display: grid;
  gap: 4px;
}

.hotspot-count {
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.hotspot-count strong {
  font-size: 26px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.hotspot-count small {
  font-size: 12px;
  color: var(--muted);
}

.digest-list {
  margin: 0;
  padding-left: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}

.digest-list strong {
  font-size: 13px;
}

.digest-list p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
</style>
