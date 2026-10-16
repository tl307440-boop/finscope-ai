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
import MetricTile from "../components/ui/MetricTile.vue"
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
  /** 规则分：70 偏强、50 持平、30 偏弱；样本不足为 null */
  value: number | null
  reason: string
  evidence: number
}

/** 驾驶舱展示的美国核心序列；CPI 用同比，其余直接取水平值。 */
interface UsSeriesRow {
  code: string
  label: string
  unit: string
  latest: number | null
  change: number | null
  date: string | null
  points: number[]
  problem: string | null
}

const US_SERIES: { code: string; label: string; unit: string; yoy?: boolean; tail: number }[] = [
  { code: "DGS10", label: "美债 10Y", unit: "%", tail: 60 },
  { code: "DGS2", label: "美债 2Y", unit: "%", tail: 60 },
  { code: "T10Y2Y", label: "10Y-2Y 利差", unit: "pp", tail: 60 },
  { code: "FEDFUNDS", label: "联邦基金利率", unit: "%", tail: 24 },
  { code: "CPIAUCSL", label: "CPI 同比", unit: "%", yoy: true, tail: 24 },
  { code: "UNRATE", label: "失业率", unit: "%", tail: 24 },
]

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
  start: string | null
  end: string | null
  topics: { type: string; label: string; count: number }[]
  recent: { day: string; title: string }[]
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
  /** 最近 120 个交易日的收盘价，只有真实返回的日期，不补零 */
  closes: number[]
  dates: string[]
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
const usSeries = ref<UsSeriesRow[]>([])
const sectors = ref<{ name: string; score: number | null; label: string; coverage: string }[] | null>(null)

/** 有分数的在前、按分数从高到低；样本不足的只列名字。 */
const rankedSectors = computed(() =>
  (sectors.value ?? [])
    .filter((item) => item.score !== null)
    .sort((left, right) => (right.score ?? 0) - (left.score ?? 0)),
)
const pendingSectors = computed(() => (sectors.value ?? []).filter((item) => item.score === null))

async function loadSectors(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/market/sectors", null)
    sectors.value = listOf(data?.sectors).map((item) => ({
      name: asString(item.name) ?? "未命名板块",
      score: asNumber(item.score),
      label: asString(item.label) ?? "",
      coverage: asString(item.coverage) ?? "",
    }))
  } catch (error) {
    recordError("sectors", error)
  }
}
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
      value: asNumber(record.value),
      reason: asString(record.reason) ?? "",
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

/** 同月比较：找 12 个月前同月的观测，算同比；找不到就不出数。 */
function yoySeries(items: { date: string; value: number }[]): { date: string; value: number }[] {
  const byMonth = new Map(items.map((item) => [item.date.slice(0, 7), item.value]))
  const result: { date: string; value: number }[] = []
  for (const item of items) {
    const year = Number(item.date.slice(0, 4))
    const base = byMonth.get(`${year - 1}${item.date.slice(4, 7)}`)
    if (base !== undefined && base !== 0) {
      result.push({ date: item.date, value: ((item.value - base) / base) * 100 })
    }
  }
  return result
}

async function loadUsSeries(): Promise<void> {
  const rows = await Promise.all(
    US_SERIES.map(async (spec): Promise<UsSeriesRow> => {
      try {
        const { data } = await getJson(`/api/v1/macro/series/${spec.code}/observations`, null)
        const raw = listOf(data?.observations)
          .map((item) => ({
            date: asString(item.observation_date) ?? "",
            value: asNumber(item.value),
          }))
          .filter((item): item is { date: string; value: number } => item.date !== "" && item.value !== null)
        const series = spec.yoy ? yoySeries(raw) : raw
        const tail = series.slice(-spec.tail)
        const last = tail.at(-1)
        const previous = tail.at(-2)
        return {
          code: spec.code,
          label: spec.label,
          unit: spec.unit,
          latest: last?.value ?? null,
          change: last && previous ? last.value - previous.value : null,
          date: last?.date ?? null,
          points: tail.map((item) => item.value),
          problem: last ? null : "尚无可见观测",
        }
      } catch (error) {
        return {
          code: spec.code,
          label: spec.label,
          unit: spec.unit,
          latest: null,
          change: null,
          date: null,
          points: [],
          problem: toApiError(error).message,
        }
      }
    }),
  )
  usSeries.value = rows
}

async function loadHotspots(): Promise<void> {
  try {
    const { data } = await getJson("/api/v1/market/hotspots", null)
    const recent: { day: string; title: string }[] = []
    for (const day of listOf(data?.timeline)) {
      const dayText = asString(day.day) ?? ""
      for (const title of asList(day.titles)) {
        if (typeof title === "string" && recent.length < 5) {
          recent.push({ day: dayText, title })
        }
      }
    }
    hotspots.value = {
      label: asString(data?.label) ?? "暂无热点数据",
      count: asNumber(data?.count),
      start: asString(data?.start),
      end: asString(data?.end),
      topics: listOf(data?.topics).map((item) => ({
        type: asString(item.event_type) ?? "other",
        label: asString(item.label) ?? "其他",
        count: asNumber(item.count) ?? 0,
      })),
      recent,
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
          dates: [],
          latestClose: null,
          changePercent: null,
          problem: "该指数未入库",
        })
        continue
      }
      const bars = await getJson(`/api/v1/market/bars/${instrumentId}?limit=3000`, null)
      const items = listOf(bars.data?.bars)
      const points = items
        .map((item) => ({ date: asString(item.trade_date) ?? "", close: asNumber(item.close) }))
        .filter((item): item is { date: string; close: number } => item.close !== null)
      const closes = points.map((item) => item.close)
      const tailPoints = points.slice(-120)
      const lastClose = closes.length ? closes[closes.length - 1] : null
      const previousClose = closes.length > 1 ? closes[closes.length - 2] : null
      rows.push({
        symbol,
        name: asString(hit.name_cn) ?? asString(hit.name_en) ?? "—",
        barCount: items.length,
        latestTradeDate: asString(items[items.length - 1]?.trade_date),
        closes: tailPoints.map((item) => item.close),
        dates: tailPoints.map((item) => item.date),
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
        dates: [],
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
      loadUsSeries(),
      loadSectors(),
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
  if (value === "public_csv") {
    return "公开 CSV"
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

const REGION_WORDS: Record<string, [string, string, string]> = {
  growth: ["回升", "持平", "放缓"],
  inflation: ["升温", "持平", "回落"],
  liquidity: ["宽松", "持平", "收紧"],
}

/** 规则分 70/50/30 → 文字；与后端 macro_rules 的打分口径一致。 */
function regionWord(region: MacroRegion): string {
  if (region.value === null) {
    return "样本不足"
  }
  const words = REGION_WORDS[region.code] ?? ["偏强", "持平", "偏弱"]
  return region.value > 50 ? words[0] : region.value < 50 ? words[2] : words[1]
}

function regionTone(region: MacroRegion): "rise" | "fall" | "muted" {
  if (region.value === null || region.value === 50) {
    return "muted"
  }
  return region.value > 50 ? "rise" : "fall"
}

/** 增长 × 通胀 两个方向映射到投资时钟四象限（规则映射，不是经济定论）。 */
const QUADRANTS: Record<string, string> = {
  "up-down": "复苏象限",
  "up-up": "过热象限",
  "down-up": "滞胀象限",
  "down-down": "衰退象限",
}

/** 驾驶舱的四张大卡：数值全部来自上面几个接口，取不到就渲染「—」。 */
const cardMacroValue = computed<string>(() => {
  const code = macro.value?.regimeCode ?? ""
  const [growth, inflation] = code.split("-")
  return QUADRANTS[`${growth}-${inflation}`] ?? "—"
})

const cardMacroCaption = computed<string>(() => {
  if (!macro.value) {
    return sectionErrors.value.macro ?? "读取中"
  }
  const regions = macro.value.regions
  if (!regions.length || regions.some((item) => item.value === null)) {
    return "增长 / 通胀 / 流动性样本不足，暂不判定"
  }
  return `${regions.map((item) => `${item.label}${regionWord(item)}`).join(" · ")} · 美国 · ${macro.value.pitDate}`
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

const INDEX_PALETTE = ["#3b82f6", "#22d3ee", "#a78bfa", "#fbbf24"]

/** 四个指数近 120 个交易日相对窗口首日的累计涨跌幅；点位量级不同，换算成百分比才能同轴比较。 */
const indexChartOption = computed<EChartsOption>(() => {
  const usable = indexRows.value.filter((row) => row.closes.length > 1)
  const axis = [...new Set(usable.flatMap((row) => row.dates))].sort()
  return {
    animation: false,
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) => (typeof value === "number" ? `${value >= 0 ? "+" : ""}${value.toFixed(2)}%` : "—"),
    },
    legend: {
      data: usable.map((row) => row.name),
      top: 0,
      right: 0,
    },
    grid: { left: 4, right: 22, top: 30, bottom: 2, containLabel: true },
    xAxis: {
      type: "category",
      data: axis,
      boundaryGap: false,
      axisLabel: { formatter: (value: string) => value.slice(5), hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: "{value}%" },
    },
    series: usable.map((row, index) => {
      const base = row.closes[0]
      const byDate = new Map(row.dates.map((date, offset) => [date, row.closes[offset]]))
      const color = INDEX_PALETTE[index % INDEX_PALETTE.length]
      return {
        name: row.name,
        type: "line" as const,
        data: axis.map((date) => {
          const close = byDate.get(date)
          return close === undefined ? null : ((close - base) / base) * 100
        }),
        connectNulls: true,
        symbol: "none",
        lineStyle: { color, width: index === 0 ? 2 : 1.5 },
        itemStyle: { color },
        areaStyle:
          index === 0
            ? {
                color: {
                  type: "linear" as const,
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops: [
                    { offset: 0, color: "rgba(59,130,246,0.28)" },
                    { offset: 1, color: "rgba(59,130,246,0)" },
                  ],
                },
              }
            : undefined,
        markLine:
          index === 0
            ? { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: "rgba(148,163,184,0.35)", type: "dashed" as const }, label: { show: false } }
            : undefined,
      }
    }),
  }
})

/** 热点主题条：按条数占比画横条，条数来自后端分类统计。 */
const topicMax = computed<number>(() => Math.max(1, ...(hotspots.value?.topics ?? []).map((item) => item.count)))

/** 数据来源条用的数据源标识。 */
const sourceList = computed<string[]>(() => {
  const found: string[] = []
  if (indexRows.value.some((row) => row.barCount > 0)) {
    found.push("A股日线")
  }
  if (usSeries.value.some((row) => row.latest !== null)) {
    found.push("FRED 美国宏观")
  }
  if ((hotspots.value?.count ?? 0) > 0) {
    found.push("巨潮资讯 / SEC EDGAR 公告")
  }
  if (!found.length && meta.value.source) {
    found.push(meta.value.source)
  }
  return found
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
          info="用美国 GDP、CPI、联邦基金利率最近两期的方向做规则打分，再映射到投资时钟象限；只用当时已公布的数据"
        >
          <template #icon><el-icon><TrendCharts /></el-icon></template>
        </StatCard>

        <StatCard
          title="A股市场温度"
          :value="temperature?.score === null || temperature?.score === undefined ? null : temperature.score.toFixed(1)"
          unit="/100"
          :caption="cardTemperatureCaption"
          info="趋势、广度、活跃度、波动、收益五个因子按权重合成；样本不足时不出分"
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
          info="数据库与后台任务服务的就绪检查"
        >
          <template #icon><el-icon><Monitor /></el-icon></template>
        </StatCard>
      </div>

      <!-- 主区：左宽右窄 -->
      <div class="grid-2-1 section">
        <PanelCard title="A股主要指数走势" info="近 120 个交易日相对窗口首日的累计涨跌幅，缺失交易日不补值" more-text="指数行情" more-to="/market/indices">
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

        <PanelCard title="全球宏观概览" info="增长、通胀、流动性三个维度的规则判定，以及 FRED 美国核心序列的最新可见值" more-text="全球宏观" more-to="/macro/us">
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
                :label="`美国${region.label}`"
                :value="regionWord(region)"
                delta-label=""
              >
                <template #delta>
                  <span class="region-score" :class="regionTone(region)">
                    规则分 {{ region.value ?? "—" }}
                  </span>
                </template>
              </MetricTile>
            </div>

            <ul class="us-list">
              <li v-for="row in usSeries" :key="row.code">
                <span class="us-name" :title="`FRED ${row.code}`">{{ row.label }}</span>
                <MiniSpark
                  v-if="row.points.length > 1"
                  :points="row.points"
                  tone="accent"
                  :width="72"
                  :height="22"
                />
                <span v-else class="us-spark-empty" />
                <span class="us-value num">
                  {{ row.latest === null ? "—" : row.latest.toFixed(2) }}<small>{{ row.latest === null ? "" : row.unit }}</small>
                </span>
                <DeltaValue :value="row.change" size="sm" :colored="false" signed />
                <span class="us-date num">{{ row.date?.slice(2) ?? row.problem ?? "" }}</span>
              </li>
            </ul>
            <p class="macro-notice">
              {{ macro?.notice }} 中国宏观序列
              {{ (china?.series ?? []).filter((item) => item.status === "available").length }}/{{ (china?.series ?? []).length }}
              已导入，<router-link to="/macro/china">去查看</router-link>
            </p>
          </template>
        </PanelCard>
      </div>

      <!-- 底部三栏 -->
      <div class="grid-3 section">
        <PanelCard
          title="板块强弱"
          info="申万一级行业，成分相对沪深300的区间表现打分；成分不足 3 只的板块不出分"
          more-text="板块行情"
          more-to="/market/sectors"
        >
          <template #icon><el-icon><DataAnalysis /></el-icon></template>
          <StateBlock
            v-if="!sectors"
            :state="sectionErrors.sectors ? 'stale' : 'loading'"
            :message="sectionErrors.sectors ?? ''"
            @retry="load"
          />
          <StateBlock
            v-else-if="!rankedSectors.length"
            state="empty"
            message="各板块成分都不足 3 只，暂不判断强弱"
          />
          <ul v-else class="sector-bars">
            <li v-for="item in rankedSectors" :key="item.name">
              <span class="sector-name">{{ item.name }}</span>
              <span class="sector-track">
                <i :style="{ width: `${Math.max(2, item.score ?? 0)}%` }" :class="{ strong: (item.score ?? 0) >= 50 }" />
              </span>
              <b class="num">{{ item.score?.toFixed(1) }}</b>
              <em>{{ item.label }}</em>
            </li>
          </ul>
          <p v-if="pendingSectors.length" class="macro-notice">
            样本不足：{{ pendingSectors.map((item) => item.name).join("、") }}
          </p>
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
              <strong class="num">{{ hotspots.count ?? "—" }}</strong>
              <small>条官方公告 · {{ hotspots.start?.slice(5) }} 至 {{ hotspots.end?.slice(5) }}</small>
            </p>
            <ul class="topic-bars">
              <li v-for="topic in hotspots.topics" :key="topic.type">
                <span>{{ topic.label }}</span>
                <i :style="{ width: `${(topic.count / topicMax) * 100}%` }" />
                <b class="num">{{ topic.count }}</b>
              </li>
            </ul>
            <ul class="recent-list">
              <li v-for="(item, index) in hotspots.recent" :key="index">
                <span class="num">{{ item.day.slice(5) }}</span>
                <p :title="item.title">{{ item.title }}</p>
              </li>
            </ul>
          </div>
          <StateBlock v-else :state="sectionErrors.hotspots ? 'stale' : 'loading'" :message="sectionErrors.hotspots ?? ''" @retry="load" />

          <template v-if="providers.length">
            <h3 class="sub-title">数据源状态</h3>
            <ul class="series-list">
              <li v-for="item in providers" :key="item.code">
                <span class="series-name">{{ item.code }}</span>
                <StatusPill
                  :text="statusText(item.availability)"
                  :tone="['configured', 'not_required', 'public_csv'].includes(item.availability) ? 'ok' : 'warn'"
                />
                <span class="series-date">{{ item.healthAt ? formatMoment(item.healthAt) : "" }}</span>
              </li>
            </ul>
          </template>
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
  height: 300px;
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
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.region-score {
  font-size: 12px;
  font-family: var(--font-num);
  color: var(--muted);
}

.region-score.rise {
  color: var(--rise);
}

.region-score.fall {
  color: var(--fall);
}

.us-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
}

.us-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px 66px 52px 58px;
  align-items: center;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--line-soft);
  font-size: 13px;
}

.us-list li:last-child {
  border-bottom: 0;
}

.us-name {
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.us-value {
  text-align: right;
  font-size: 15px;
  color: var(--text-strong);
}

.us-value small {
  margin-left: 1px;
  font-size: 11px;
  color: var(--muted);
}

.us-date {
  text-align: right;
  font-size: 12px;
  color: var(--faint);
}

.us-spark-empty {
  height: 22px;
}

.sector-bars {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}

.sector-bars li {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr) 40px 34px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.sector-name {
  color: var(--text);
}

.sector-track {
  height: 8px;
  border-radius: 4px;
  background: rgb(96 140 220 / 10%);
  overflow: hidden;
}

.sector-track i {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: linear-gradient(90deg, rgb(31 195 125 / 45%), var(--fall));
}

.sector-track i.strong {
  background: linear-gradient(90deg, rgb(246 70 93 / 45%), var(--rise));
}

.sector-bars b {
  text-align: right;
  font-size: 15px;
  font-weight: 500;
  color: var(--text-strong);
}

.sector-bars em {
  font-style: normal;
  font-size: 12px;
  color: var(--muted);
}

.topic-bars {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 7px;
}

.topic-bars li {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr) 30px;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
}

.topic-bars li > i {
  display: block;
  height: 6px;
  min-width: 4px;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--orange), rgb(251 146 60 / 35%));
  box-shadow: 0 0 10px -2px rgb(251 146 60 / 55%);
}

.topic-bars b {
  text-align: right;
  font-weight: 500;
  color: var(--text-strong);
}

.recent-list {
  margin: 14px 0 0;
  padding: 12px 0 0;
  border-top: 1px solid var(--line-soft);
  list-style: none;
  display: grid;
  gap: 8px;
}

.recent-list li {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  gap: 8px;
  font-size: 13px;
}

.recent-list span {
  color: var(--faint);
}

.recent-list p {
  margin: 0;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.macro-notice {
  margin: 12px 0 0;
  font-size: 13px;
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
  font-size: 28px;
  font-weight: 600;
  color: var(--text-strong);
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
