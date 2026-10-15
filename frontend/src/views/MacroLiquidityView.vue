<script setup lang="ts">
/**
 * 货币与流动性（/macro/liquidity，效果图 2 的「货币与流动性」页签内容）。
 *
 * 真实接口（与 backend/app/api/v1/macro.py、backend/app/schemas/macro.py 一致；原有取数逻辑保持不变）：
 *  - GET /api/v1/macro/series
 *      data = { provider_code, availability, notice, series[] }
 *      series[] = { series_code, name_cn, name_en, category, frequency, base_unit, seasonal_adjustment }
 *      category 实测取值：rates / output / labor / inflation
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *  - GET /api/v1/macro/assessments（**这条不传 as_of**，与原实现一致：分数永远是今天时点的快照）
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value, reason, evidence[] },
 *               notice, version }
 *
 * 口径说明（如实反映后端，不硬凑概念）：
 *  - 后端序列目录里**没有**「货币供应量 / 流动性总量」这一分类，唯一与资金价格直接相关的
 *    是 category === "rates"（实测 4 条）。本页用它作为流动性的观测对象，并明确标注这只是
 *    利率类，不代表货币供应量；货币供应量缺口在「口径说明」里如实列出。
 *  - **后端没有 POST /api/v1/macro/assessments**（唯一的 POST 是仅管理员的
 *    /macro/china/observations 导入），本页只读展示，分数与理由原样转发。
 *
 * 不编造数据：指标带只显示目录里真实存在的利率类序列；取不到观测值时显示 —— 与真实空态。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import { describeObservations, type MacroObservation } from "../api/macro"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"

interface SeriesRow {
  code: string
  name: string
  nameEn: string
  category: string
  frequency: string
  unit: string
  seasonalAdjustment: string
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

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const phase = ref<Phase>("loading")
const failure = ref("")
const providerCode = ref("")
const availability = ref("")
const catalogNotice = ref("")
const series = ref<SeriesRow[]>([])
const assessment = ref<Assessment | null>(null)
const assessmentFailed = ref(false)
const selectedCode = ref("")
const asOf = ref("")

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<MacroObservation[]>([])
const pitDate = ref("")
const emptyMessage = ref("")
const truncated = ref(false)
const rateObservations = ref<Record<string, MacroObservation[]>>({})
const rateEmpty = ref<Record<string, string>>({})

/** 后端自己给出的分类 "rates"：与资金价格直接相关的序列。 */
const rateSeries = computed<SeriesRow[]>(() => series.value.filter((row) => row.category === "rates"))

const otherSeries = computed<SeriesRow[]>(() => series.value.filter((row) => row.category !== "rates"))

const categoryLegend = computed<string>(() => {
  const counted = new Map<string, number>()
  for (const row of series.value) {
    counted.set(row.category, (counted.get(row.category) ?? 0) + 1)
  }
  return [...counted.entries()].map(([category, count]) => `${category} ${count}`).join(" · ")
})

const availabilityLabel = computed<string>(() => {
  if (availability.value === "configured") {
    return "外部数据源已配置"
  }
  if (availability.value === "unavailable") {
    return "外部数据源未配置（序列定义仍在，观测值需同步或导入）"
  }
  return availability.value || "—"
})

const selectedRow = computed<SeriesRow | null>(
  () => series.value.find((row) => row.code === selectedCode.value) ?? null,
)

const detailBusy = computed<boolean>(() => detailPhase.value === "loading")

/** 利率类指标带：每条序列一格，值/较上期/迷你走势都来自真实观测值。 */
const rateTiles = computed(() =>
  rateSeries.value.map((row) => {
    const rows = rateObservations.value[row.code] ?? []
    const latest = rows.length ? rows[rows.length - 1] ?? null : null
    const previous = rows.length > 1 ? rows[rows.length - 2] ?? null : null
    const latestValue = latest ? asNumber(latest.value) : null
    const previousValue = previous ? asNumber(previous.value) : null
    return {
      row,
      latest,
      delta: latestValue !== null && previousValue !== null ? latestValue - previousValue : null,
      spark: rows.flatMap((item) => {
        const parsed = asNumber(item.value)
        return parsed === null ? [] : [parsed]
      }),
    }
  }),
)

const chartOption = computed<EChartsCoreOption | null>(() => buildTrendOption(observations.value))

const observationLines = computed<string[]>(() =>
  describeObservations({ emptyMessage: emptyMessage.value || null, observations: observations.value }),
)

function formatValue(value: number | null, unit: string): string {
  if (value === null) {
    return "—"
  }
  if (unit === "Percent") {
    return value.toFixed(2)
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
}

/**
 * 后端只回分数不回文案；分数含义由 backend/app/services/macro_rules.py 决定：
 * 70=该序列下行（流动性维度 tighter_when_rising，故为偏松），30=上行（偏紧），
 * 50=持平，value 缺省=不足两个已公布观测值。
 * 分数不是价格，所以状态标签用 tonal 底色（scorePill），涨红跌绿只用在下面走势图的价格方向上。
 */
function scorePill(score: DimensionScore | null): { text: string; tone: "ok" | "warn" | "danger" | "muted" } {
  if (!score || score.value === null) {
    return { text: "数据不足", tone: "muted" }
  }
  if (score.code === "liquidity") {
    return score.value >= 70
      ? { text: "偏松", tone: "ok" }
      : score.value <= 30
        ? { text: "偏紧", tone: "danger" }
        : { text: "持平", tone: "warn" }
  }
  return score.value >= 70
    ? { text: "上行", tone: "ok" }
    : score.value <= 30
      ? { text: "下行", tone: "danger" }
      : { text: "持平", tone: "warn" }
}

/** 后端 reason 是枚举值，这里只做展示翻译；未收录的取值原样显示。 */
function reasonView(reason: string | null): string {
  if (!reason) {
    return ""
  }
  if (reason === "insufficient_data") {
    return "不足两个已公布观测值，规则无法给出方向"
  }
  return reason
}

function parseSeries(value: unknown): SeriesRow[] {
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
        seasonalAdjustment: asString(item.seasonal_adjustment) ?? "—",
      },
    ]
  })
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

function parseObservation(value: unknown): MacroObservation[] {
  const record = asRecord(value)
  const observationDate = asString(record?.observation_date)
  const amount = asString(record?.value)
  const unit = asString(record?.unit)
  const realtimeStart = asString(record?.realtime_start)
  const revision = asNumber(record?.revision_no)
  if (!observationDate || amount === null || !unit || !realtimeStart || revision === null) {
    return []
  }
  return [
    {
      observation_date: observationDate,
      value: amount,
      unit,
      realtime_start: realtimeStart,
      revision_no: revision,
    },
  ]
}

async function fetchObservations(code: string): Promise<{ rows: MacroObservation[]; empty: string }> {
  const params = new URLSearchParams({ limit: "500" })
  if (asOf.value) {
    params.set("as_of", asOf.value)
  }
  const response = await fetch(
    apiUrl(`/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`),
  )
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  const data = asRecord(asRecord(body)?.data)
  return {
    rows: Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : [],
    empty: asString(data?.empty_message) ?? "",
  }
}

/** 指标带：并发取利率类序列的观测值；失败的序列按「无可见观测值」如实呈现。 */
async function loadRateObservations(): Promise<void> {
  const rows = rateSeries.value
  if (!rows.length) {
    rateObservations.value = {}
    rateEmpty.value = {}
    return
  }
  const results = await Promise.all(
    rows.map(async (row) => {
      try {
        const result = await fetchObservations(row.code)
        return { code: row.code, rows: result.rows, empty: result.empty }
      } catch {
        return { code: row.code, rows: [] as MacroObservation[], empty: "本次未能取到该序列的观测值" }
      }
    }),
  )
  const nextRows: Record<string, MacroObservation[]> = {}
  const nextEmpty: Record<string, string> = {}
  for (const item of results) {
    nextRows[item.code] = item.rows
    if (item.empty) {
      nextEmpty[item.code] = item.empty
    }
  }
  rateObservations.value = nextRows
  rateEmpty.value = nextEmpty
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  assessmentFailed.value = false
  try {
    const [seriesResponse, assessmentResponse] = await Promise.all([
      fetch(apiUrl("/api/v1/macro/series")),
      fetch(apiUrl("/api/v1/macro/assessments")),
    ])
    const seriesBody = await readJson(seriesResponse)
    if (!seriesResponse.ok) {
      throw errorFrom(seriesBody, seriesResponse.headers.get("X-Request-ID"))
    }
    const seriesData = asRecord(asRecord(seriesBody)?.data)
    providerCode.value = asString(seriesData?.provider_code) ?? ""
    availability.value = asString(seriesData?.availability) ?? ""
    catalogNotice.value = asString(seriesData?.notice) ?? ""
    series.value = parseSeries(seriesData?.series)

    // 评估接口失败不让整页失败，但要如实标注「未取到」，不编造结论。
    assessment.value = null
    if (assessmentResponse.ok) {
      const assessmentBody = await readJson(assessmentResponse)
      const data = asRecord(asRecord(assessmentBody)?.data)
      assessment.value = {
        pitDate: asString(data?.pit_date) ?? "",
        regimeCode: asString(data?.regime_code) ?? "",
        growth: parseScore(data?.growth),
        inflation: parseScore(data?.inflation),
        liquidity: parseScore(data?.liquidity),
        notice: asString(data?.notice) ?? "",
        version: asString(data?.version) ?? "",
      }
    } else {
      assessmentFailed.value = true
    }

    if (!selectedCode.value) {
      selectedCode.value = (rateSeries.value[0] ?? series.value[0])?.code ?? ""
    }
    phase.value = "ready"
    if (selectedCode.value) {
      await Promise.all([loadObservations(), loadRateObservations()])
    }
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadObservations(): Promise<void> {
  const code = selectedCode.value
  if (!code) {
    detailPhase.value = "idle"
    observations.value = []
    return
  }
  detailPhase.value = "loading"
  detailError.value = ""
  observations.value = []
  pitDate.value = ""
  emptyMessage.value = ""
  truncated.value = false
  try {
    const params = new URLSearchParams({ limit: "500" })
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const response = await fetch(
      apiUrl(`/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    observations.value = Array.isArray(data?.observations)
      ? data.observations.flatMap(parseObservation)
      : []
    pitDate.value = asString(data?.pit_date) ?? ""
    emptyMessage.value = asString(data?.empty_message) ?? ""
    truncated.value = data?.truncated === true
    detailPhase.value = "ready"
  } catch (caught) {
    detailError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    detailPhase.value = "error"
  }
}

function selectSeries(code: string): void {
  selectedCode.value = code
  void loadObservations()
}

/** 应用观察日：指标带与走势图一起刷新，保持同一时点。 */
async function applyAsOf(): Promise<void> {
  await Promise.all([loadObservations(), loadRateObservations()])
}

function trendColor(values: number[]): string {
  if (values.length < 2) {
    return "var(--accent)"
  }
  const last = values[values.length - 1]
  const previous = values[values.length - 2]
  if (last === undefined || previous === undefined || last === previous) {
    return "var(--accent)"
  }
  return last > previous ? "var(--rise)" : "var(--fall)"
}

function buildTrendOption(rows: MacroObservation[]): EChartsCoreOption | null {
  const points = rows.flatMap((row) => {
    const parsed = asNumber(row.value)
    return parsed === null ? [] : [{ date: row.observation_date, value: parsed, raw: row.value }]
  })
  if (points.length < 2) {
    return null
  }
  const values = points.map((point) => point.value)
  const color = trendColor(values)
  const zoom = points.length > 40
  return {
    grid: { left: 8, right: 16, top: 24, bottom: zoom ? 48 : 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: "var(--panel)",
      borderColor: "var(--line)",
      textStyle: { color: "var(--text)" },
      formatter: (params: unknown): string => {
        const first = Array.isArray(params) ? asRecord(params[0]) : asRecord(params)
        const index = asNumber(first?.dataIndex)
        const point = index === null ? undefined : points[index]
        if (!point) {
          return ""
        }
        return `${point.date} · ${point.raw}`
      },
    },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      axisLabel: { color: "var(--muted)", fontSize: 11, hideOverlap: true },
      axisLine: { lineStyle: { color: "var(--line)" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "var(--muted)", fontSize: 11 },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        type: "line",
        name: selectedCode.value,
        data: values,
        showSymbol: points.length <= 60,
        smooth: false,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        areaStyle: { opacity: 0.08, color },
      },
    ],
    ...(zoom
      ? {
          dataZoom: [
            { type: "inside", start: 60, end: 100 },
            { type: "slider", start: 60, end: 100, height: 18, bottom: 8 },
          ],
        }
      : {}),
  }
}

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
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日（留空＝今天）"
          class="asof-select"
          @change="applyAsOf"
        />
        <el-button @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="liquidity" />
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        message="正在读取货币流动性数据"
      />

      <StateBlock
        v-else-if="phase === 'error'"
        state="error"
        :message="failure"
        @retry="load"
      />

      <template v-else>
        <StateBlock
          v-if="!rateSeries.length"
          state="empty"
          message="后端序列目录里没有 category = rates 的序列，指标带无内容可展示。"
        />

        <div v-else class="grid-8">
          <MetricTile
            v-for="tile in rateTiles"
            :key="tile.row.code"
            :label="tile.row.name"
            :value="tile.latest ? formatValue(asNumber(tile.latest.value), tile.row.unit) : '—'"
            :period="tile.latest ? `${tile.latest.observation_date} · ${tile.row.frequency}` : '暂无可见观测值'"
            :info="`${tile.row.code} · ${tile.row.nameEn || tile.row.name} · 单位 ${tile.row.unit} · ${tile.row.seasonalAdjustment}${rateEmpty[tile.row.code] ? ` · ${rateEmpty[tile.row.code]}` : ''}`"
            delta-label="较上期"
          >
            <template #chart>
              <MiniSpark
                v-if="tile.spark.length > 1"
                :points="tile.spark"
                kind="line"
                tone="muted"
                :width="104"
                :height="34"
              />
              <span v-else class="tile-empty">无可见观测值</span>
            </template>
            <template #delta>
              <DeltaValue v-if="tile.delta !== null" :value="tile.delta" suffix=" pct" />
              <DeltaValue v-else :value="null" />
            </template>
          </MetricTile>
        </div>

        <div class="grid-2-1">
          <PanelCard
            title="利率类序列走势"
            :info="`观察日按后端时点过滤：只显示当天已经公布的观测值（realtime_start 早于观察日）。留空表示截止今天。`"
          >
            <template #icon><span class="panel-icon">◠</span></template>
            <div class="filter-row">
              <el-select v-model="selectedCode" class="filter-select" @change="selectSeries">
                <el-option
                  v-for="item in series"
                  :key="item.code"
                  :value="item.code"
                  :label="`${item.code} ${item.name}`"
                />
              </el-select>
              <el-button type="primary" :loading="detailBusy" @click="loadObservations">
                查询
              </el-button>
            </div>

            <div v-if="detailPhase === 'loading'">
              <StateBlock state="loading" message="正在查询观测值" />
            </div>

            <div v-else-if="detailPhase === 'error'">
              <StateBlock state="error" :message="detailError" @retry="loadObservations" />
            </div>

            <template v-else-if="detailPhase === 'ready'">
              <StateBlock
                v-if="truncated"
                state="stale"
                :as-of="pitDate"
                message="观测值多于本次上限，图表只画出了返回的部分。"
              />
              <VChart v-if="chartOption" class="chart chart-trend" :option="chartOption" autoresize />
              <StateBlock
                v-else
                state="empty"
                :message="emptyMessage || '该观察日没有可见的观测值，或少于两个可解析观测值'"
                :as-of="pitDate"
              />

              <template v-if="observations.length">
                <p class="muted">
                  序列 {{ selectedRow?.code || "—" }} · {{ selectedRow?.name || "—" }} ·
                  后端时点 {{ pitDate || "—" }} · 共 {{ observations.length }} 条观测值。
                </p>
                <ul class="line-list">
                  <li v-for="line in observationLines" :key="line">{{ line }}</li>
                </ul>
              </template>
            </template>

            <StateBlock v-else state="empty" message="请选择一条序列查看走势" />
          </PanelCard>

          <PanelCard
            title="规则口径的流动性判断"
            info="分数与理由全部来自后端 macro-rules-v1，本页只做文案翻译，不改口也不外推"
          >
            <template #icon><span class="panel-icon">◎</span></template>

            <StateBlock
              v-if="assessmentFailed"
              state="error"
              message="评估接口本次未返回可用结果，本页不编造结论。"
              @retry="load"
            />
            <StateBlock
              v-else-if="!assessment"
              state="empty"
              message="未取到评估结果，本页不编造结论。"
            />
            <template v-else>
              <div class="readout-head">
                <StatusPill :text="scorePill(assessment.liquidity).text" :tone="scorePill(assessment.liquidity).tone" dot />
                <span class="muted">
                  后端时点 {{ assessment.pitDate || "—" }} · 组合状态
                  <code>{{ assessment.regimeCode || "—" }}</code>
                  <template v-if="assessment.version"> · 规则版本 {{ assessment.version }}</template>
                </span>
              </div>

              <div class="grid-3">
                <MetricTile
                  label="流动性维度"
                  :value="assessment.liquidity?.value ?? '—'"
                  period="70 偏松 / 30 偏紧 / 50 持平"
                >
                  <template #delta>
                    <span class="muted">{{ reasonView(assessment.liquidity?.reason ?? null) || "已取到分数" }}</span>
                  </template>
                </MetricTile>
                <MetricTile
                  label="增长维度"
                  :value="assessment.growth?.value ?? '—'"
                  period="70 上行 / 30 下行 / 50 持平"
                >
                  <template #delta>
                    <span class="muted">{{ reasonView(assessment.growth?.reason ?? null) || "已取到分数" }}</span>
                  </template>
                </MetricTile>
                <MetricTile
                  label="通胀维度"
                  :value="assessment.inflation?.value ?? '—'"
                  period="70 上行 / 30 下行 / 50 持平"
                >
                  <template #delta>
                    <span class="muted">{{ reasonView(assessment.inflation?.reason ?? null) || "已取到分数" }}</span>
                  </template>
                </MetricTile>
              </div>

              <h3 class="doc-section">流动性判定依据</h3>
              <p v-if="!assessment.liquidity?.evidence.length" class="muted">
                本次没有返回可用依据行。
              </p>
              <ul v-else class="line-list">
                <li v-for="line in assessment.liquidity.evidence" :key="line">{{ line }}</li>
              </ul>
              <p class="muted">依据行格式为「序列代码 观测日 数值 公布日」，用于核对时点可见性。</p>
              <p v-if="assessment.notice" class="muted">{{ assessment.notice }}</p>
              <p class="muted">
                分数口径：联邦基金利率（FEDFUNDS）上行＝30 偏紧、下行＝70 偏松、持平＝50；
                以上分数与理由全部来自后端，本页不改口也不外推。
              </p>
            </template>
          </PanelCard>
        </div>

        <PanelCard title="口径说明（不硬凑概念）" info="流动性观测对象的选取依据">
          <template #icon><span class="panel-icon">ⓘ</span></template>
          <p class="doc-text">
            序列目录共 {{ series.length }} 条（数据源 {{ providerCode || "—" }}，{{ availabilityLabel }}），
            按后端分类：{{ categoryLegend || "—" }}。
          </p>
          <p class="doc-text">
            后端序列目录里没有「货币供应量 / 流动性总量」这一独立分类，唯一与资金价格直接相关的分类是
            <strong>rates（利率类，共 {{ rateSeries.length }} 条）</strong>。本页把利率类序列作为流动性的
            观测对象，只展示后端真实存在的分类与序列，不把利率冒充货币供应量；货币供应量缺口需先在
            <code>catalog/macro_series.py</code> 登记序列并同步后才可能展示。
          </p>
          <p v-if="catalogNotice" class="muted">目录说明：{{ catalogNotice }}</p>
        </PanelCard>

        <div class="grid-2">
          <PanelCard title="利率类序列（category = rates）" :info="`共 ${rateSeries.length} 条，点击行可查看走势`">
            <template #icon><span class="panel-icon">▤</span></template>
            <StateBlock
              v-if="!rateSeries.length"
              state="empty"
              message="后端没有返回任何利率类序列"
            />
            <el-table v-else :data="rateSeries" stripe @row-click="(row: SeriesRow) => selectSeries(row.code)">
              <el-table-column prop="code" label="代码" width="130" />
              <el-table-column prop="name" label="名称" min-width="170" />
              <el-table-column prop="unit" label="单位" min-width="140" />
              <el-table-column prop="frequency" label="频率" width="100" />
              <el-table-column label="操作" width="100">
                <template #default="scope">
                  <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                    查看走势
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>

          <PanelCard
            title="其余序列（非利率类）"
            :info="`共 ${otherSeries.length} 条；不属于流动性口径，不参与本页判断`"
          >
            <template #icon><span class="panel-icon">▤</span></template>
            <StateBlock
              v-if="!otherSeries.length"
              state="empty"
              message="没有其他分类的序列"
            />
            <el-table v-else :data="otherSeries" stripe @row-click="(row: SeriesRow) => selectSeries(row.code)">
              <el-table-column prop="code" label="代码" width="130" />
              <el-table-column prop="name" label="名称" min-width="170" />
              <el-table-column prop="category" label="分类" width="100" />
              <el-table-column prop="frequency" label="频率" width="100" />
              <el-table-column label="操作" width="100">
                <template #default="scope">
                  <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                    查看走势
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>
        </div>

        <SourceFooter
          :as-of="pitDate || '—'"
          :sources="['FRED（圣路易斯联储）']"
          :stale="availability === 'unavailable'"
          note="观察日留空表示截止今天；分数永远取后端今天时点的规则快照，不随观察日回放。"
        />
      </template>
    </div>
  </main>
</template>

<style scoped>
.asof-select {
  width: 200px;
}

.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.tile-empty {
  color: var(--muted);
  font-size: 11px;
}

.chart {
  width: 100%;
  background: var(--bg);
  border-radius: 12px;
}

.chart-trend {
  height: 300px;
  margin-top: 12px;
}

.readout-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
}

.filter-select {
  width: min(360px, 100%);
}

.line-list {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.7;
}

.el-table {
  margin-top: 8px;
  cursor: pointer;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
