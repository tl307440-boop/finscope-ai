<script setup lang="ts">
/**
 * 货币与流动性（/macro/liquidity，效果图 2 的「货币与流动性」页签内容）。
 *
 * 真实接口（与 backend/app/api/v1/macro.py、backend/app/schemas/macro.py 一致）：
 *  - GET /api/v1/macro/series
 *      data = { provider_code, availability, notice, series[] }
 *      series[] = { series_code, name_cn, name_en, category, frequency, base_unit, seasonal_adjustment }
 *      category 实测取值：rates / output / labor / inflation
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *      observations 按观测日升序，limit 保留最近 N 条（truncated=true 表示更早的被截掉）。
 *  - GET /api/v1/macro/assessments?as_of=
 *      data = { pit_date, regime_code, growth|inflation|liquidity: { code, value, reason, evidence[] },
 *               notice, version }
 *
 * 口径：后端目录里没有「货币供应量」分类，流动性只用 category === "rates" 的利率类序列观测；
 * 不把利率冒充货币供应量。本页只读展示，分数与依据原样转发。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf } from "../api/json"
import {
  adjustmentLabel,
  cssColor,
  frequencyLabel,
  macroSourceLabel,
  parseEvidence,
  regimeText,
  unitLabel,
} from "../api/macro"
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
type Tone = "ok" | "warn" | "danger" | "muted" | "accent"

interface SeriesRow {
  code: string
  name: string
  nameEn: string
  category: string
  frequency: string
  unit: string
  seasonalAdjustment: string
}

interface Observation {
  date: string
  value: number
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
}

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const CATEGORY_LABELS: Record<string, string> = {
  rates: "利率",
  output: "产出",
  labor: "就业",
  inflation: "通胀",
}

/** limit 保留最近 N 条：日频约两年交易日，月/季频 10 年。 */
function limitFor(row: SeriesRow | null): number {
  return row?.frequency === "Daily" ? 520 : 120
}

const phase = ref<Phase>("loading")
const failure = ref("")
const availability = ref("")
const series = ref<SeriesRow[]>([])
const assessment = ref<Assessment | null>(null)
const assessmentFailed = ref(false)
const selectedCode = ref("")
const asOf = ref("")

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<Observation[]>([])
const pitDate = ref("")
const truncated = ref(false)
const rateObservations = ref<Record<string, Observation[]>>({})

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`

const sourceLabel = computed<string>(() => macroSourceLabel(availability.value))

/** 后端自己给出的分类 "rates"：与资金价格直接相关的序列。 */
const rateSeries = computed<SeriesRow[]>(() => series.value.filter((row) => row.category === "rates"))

const selectedRow = computed<SeriesRow | null>(
  () => series.value.find((row) => row.code === selectedCode.value) ?? null,
)

const pastDateUnsupported = computed<boolean>(
  () => availability.value === "public_csv" && Boolean(asOf.value) && asOf.value < browserToday,
)

const emptyText = computed<string>(() =>
  pastDateUnsupported.value
    ? "公开数据只有最新修订值，无法回看所选观察日当时已公布的数据，请清空观察日。"
    : "该观察日还没有已公布的数据",
)

function formatValue(value: number | null): string {
  if (value === null) {
    return "—"
  }
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function periodText(date: string, frequency: string): string {
  return frequency === "Monthly" ? date.slice(0, 7) : date
}

/** 利率类指标带：每条序列一格，值/较上期/迷你走势都来自真实观测值。 */
const rateTiles = computed(() =>
  rateSeries.value.map((row) => {
    const rows = rateObservations.value[row.code] ?? []
    const latest = rows.length ? (rows[rows.length - 1] ?? null) : null
    const previous = rows.length > 1 ? (rows[rows.length - 2] ?? null) : null
    return {
      row,
      latest,
      delta: latest && previous ? latest.value - previous.value : null,
      spark: rows.slice(-60).map((item) => item.value),
    }
  }),
)

const observationSummary = computed<string>(() => {
  const rows = observations.value
  const first = rows[0]
  const last = rows[rows.length - 1]
  if (!first || !last || !selectedRow.value) {
    return ""
  }
  const unit = unitLabel(selectedRow.value.unit)
  const recent = truncated.value ? `最近 ${rows.length} 期` : `共 ${rows.length} 期`
  return `${recent}，${first.date} 至 ${last.date}；最新 ${formatValue(last.value)}${unit === "%" ? "%" : ` ${unit}`}`
})

const chartOption = computed<EChartsCoreOption | null>(() => {
  const points = observations.value
  if (points.length < 2) {
    return null
  }
  const accent = cssColor("--accent", "#3b82f6")
  const unit = selectedRow.value?.unit ?? ""
  const suffix = unit === "Percent" ? "%" : ""
  const zoom = points.length > 120
  return {
    grid: { left: 8, right: 16, top: 16, bottom: zoom ? 44 : 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown): string =>
        typeof value === "number" ? `${formatValue(value)}${suffix}` : "—",
    },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { formatter: suffix ? `{value}${suffix}` : "{value}" },
    },
    series: [
      {
        type: "line",
        name: selectedRow.value?.name ?? selectedCode.value,
        data: points.map((point) => point.value),
        showSymbol: false,
        step: selectedRow.value?.code === "FEDFUNDS" ? "end" : undefined,
        lineStyle: { color: accent, width: 2 },
        itemStyle: { color: accent },
        areaStyle: { opacity: 0.1, color: accent },
      },
    ],
    ...(zoom
      ? {
          dataZoom: [
            { type: "inside", start: 50, end: 100 },
            { type: "slider", start: 50, end: 100, height: 18, bottom: 6 },
          ],
        }
      : {}),
  }
})

/**
 * 分数含义由 backend/app/services/macro_rules.py 决定：
 * 流动性 70=利率下行（偏松），30=利率上行（偏紧），50=持平；value 缺省=数据不足。
 * 宏观指标不是涨跌，配色用中性色。
 */
function scorePill(score: DimensionScore | null): { text: string; tone: Tone } {
  if (!score || score.value === null) {
    return { text: "数据不足", tone: "muted" }
  }
  if (score.code === "liquidity") {
    return score.value >= 70
      ? { text: "偏松", tone: "accent" }
      : score.value <= 30
        ? { text: "偏紧", tone: "warn" }
        : { text: "持平", tone: "muted" }
  }
  return score.value >= 70
    ? { text: "上行", tone: "accent" }
    : score.value <= 30
      ? { text: "下行", tone: "warn" }
      : { text: "持平", tone: "muted" }
}

function seriesName(code: string): string {
  return series.value.find((row) => row.code === code)?.name ?? code
}

const liquidityEvidence = computed<{ date: string; value: string; published: string }[]>(() =>
  (assessment.value?.liquidity?.evidence ?? []).map((line) => {
    const point = parseEvidence(line)
    return {
      date: point.date ? point.date.slice(0, 7) : line,
      value: point.value === null ? "" : `${point.value.toFixed(2)}%`,
      published: point.published,
    }
  }),
)

const liquiditySeriesName = computed<string>(() => {
  const line = assessment.value?.liquidity?.evidence[0]
  return line ? seriesName(parseEvidence(line).code || "FEDFUNDS") : seriesName("FEDFUNDS")
})

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
        category: asString(item.category) ?? "",
        frequency: asString(item.frequency) ?? "",
        unit: asString(item.base_unit) ?? "",
        seasonalAdjustment: asString(item.seasonal_adjustment) ?? "",
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

function parseObservation(value: unknown): Observation[] {
  const record = asRecord(value)
  const date = asString(record?.observation_date)
  const amount = asNumber(record?.value)
  if (!date || amount === null) {
    return []
  }
  return [{ date, value: amount }]
}

async function fetchObservations(
  row: SeriesRow | null,
  code: string,
): Promise<{ rows: Observation[]; pitDate: string; truncated: boolean }> {
  const params = new URLSearchParams({ limit: String(limitFor(row)) })
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
  const rows = Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : []
  rows.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
  return { rows, pitDate: asString(data?.pit_date) ?? "", truncated: data?.truncated === true }
}

/** 指标带：并发取利率类序列的观测值；失败的序列按「暂无数据」如实呈现。 */
async function loadRateObservations(): Promise<void> {
  const results = await Promise.all(
    rateSeries.value.map(async (row) => {
      try {
        const result = await fetchObservations(row, row.code)
        return { code: row.code, rows: result.rows }
      } catch {
        return { code: row.code, rows: [] as Observation[] }
      }
    }),
  )
  const next: Record<string, Observation[]> = {}
  for (const item of results) {
    next[item.code] = item.rows
  }
  rateObservations.value = next
}

async function loadAssessment(): Promise<void> {
  assessmentFailed.value = false
  try {
    const params = new URLSearchParams()
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const query = params.toString()
    const response = await fetch(apiUrl(`/api/v1/macro/assessments${query ? `?${query}` : ""}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    assessment.value = {
      pitDate: asString(data?.pit_date) ?? "",
      regimeCode: asString(data?.regime_code) ?? "",
      growth: parseScore(data?.growth),
      inflation: parseScore(data?.inflation),
      liquidity: parseScore(data?.liquidity),
      notice: asString(data?.notice) ?? "",
    }
  } catch {
    // 规则判断失败不让整页失败，但要如实标注「未取到」，不编造结论。
    assessment.value = null
    assessmentFailed.value = true
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const seriesResponse = await fetch(apiUrl("/api/v1/macro/series"))
    const seriesBody = await readJson(seriesResponse)
    if (!seriesResponse.ok) {
      throw errorFrom(seriesBody, seriesResponse.headers.get("X-Request-ID"))
    }
    const seriesData = asRecord(asRecord(seriesBody)?.data)
    availability.value = asString(seriesData?.availability) ?? ""
    series.value = parseSeries(seriesData?.series)
    if (!selectedCode.value) {
      selectedCode.value = (rateSeries.value.find((row) => row.code === "FEDFUNDS") ?? rateSeries.value[0] ?? series.value[0])?.code ?? ""
    }
    phase.value = "ready"
    await Promise.all([loadObservations(), loadRateObservations(), loadAssessment()])
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
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
  truncated.value = false
  try {
    const result = await fetchObservations(selectedRow.value, code)
    observations.value = result.rows
    pitDate.value = result.pitDate
    truncated.value = result.truncated
    detailPhase.value = "ready"
  } catch (caught) {
    detailError.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    detailPhase.value = "error"
  }
}

function selectSeries(code: string): void {
  selectedCode.value = code
  void loadObservations()
}

/** 应用观察日：指标带、走势图与规则判断一起刷新，保持同一时点。 */
async function applyAsOf(): Promise<void> {
  await Promise.all([loadObservations(), loadRateObservations(), loadAssessment()])
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader title="全球宏观分析" subtitle="把握全球经济周期，洞察宏观变量，发现大类资产配置机会">
      <template #actions>
        <el-date-picker
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日（留空为今天）"
          class="asof-select"
          @change="applyAsOf"
        />
        <el-button @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="liquidity" />
      </template>
      <template #filters>
        <span class="source-hint"><i aria-hidden="true" />数据来源：{{ sourceLabel }}</span>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock v-if="phase === 'loading'" state="loading" message="正在读取货币与流动性数据" />

      <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

      <template v-else>
        <StateBlock v-if="!rateSeries.length" state="empty" message="暂无利率类数据" />

        <div v-else class="grid-4">
          <MetricTile
            v-for="tile in rateTiles"
            :key="tile.row.code"
            :label="tile.row.name"
            :value="tile.latest ? formatValue(tile.latest.value) : '—'"
            :unit="tile.latest ? unitLabel(tile.row.unit) : ''"
            :period="
              tile.latest
                ? `${periodText(tile.latest.date, tile.row.frequency)} · ${frequencyLabel(tile.row.frequency)}`
                : '暂无数据'
            "
            :info="`${tile.row.nameEn || tile.row.name} · FRED 代码 ${tile.row.code} · ${adjustmentLabel(tile.row.seasonalAdjustment)}`"
            delta-label="较上期"
          >
            <template #chart>
              <MiniSpark
                v-if="tile.spark.length > 1"
                :points="tile.spark"
                kind="area"
                tone="accent"
                :width="104"
                :height="34"
              />
            </template>
            <template #delta>
              <DeltaValue :value="tile.delta" suffix=" pct" :colored="false" signed size="sm" />
            </template>
          </MetricTile>
        </div>

        <div class="grid-2-1">
          <PanelCard title="利率走势" info="只显示所选观察日已经公布的观测值；观察日留空表示截至今天">
            <template #icon><span class="panel-icon">◠</span></template>
            <template #actions>
              <el-select v-model="selectedCode" class="filter-select" size="small" @change="selectSeries">
                <el-option v-for="item in series" :key="item.code" :value="item.code" :label="item.name" />
              </el-select>
            </template>

            <StateBlock v-if="detailPhase === 'loading'" state="loading" message="正在查询观测值" />
            <StateBlock
              v-else-if="detailPhase === 'error'"
              state="error"
              :message="detailError"
              @retry="loadObservations"
            />
            <template v-else-if="detailPhase === 'ready'">
              <VChart v-if="chartOption" class="chart chart-trend" :option="chartOption" autoresize />
              <StateBlock v-else state="empty" :message="emptyText" />
              <p v-if="observationSummary" class="chart-note">
                {{ selectedRow?.name }}：{{ observationSummary }}
              </p>
            </template>
            <StateBlock v-else state="empty" message="请选择一条序列查看走势" />
          </PanelCard>

          <PanelCard title="流动性判断" info="按规则比较联邦基金利率最近两期：上行记 30（偏紧）、下行记 70（偏松）、持平记 50">
            <template #icon><span class="panel-icon">◎</span></template>

            <StateBlock
              v-if="assessmentFailed"
              state="error"
              message="规则判断暂时不可用"
              @retry="loadAssessment"
            />
            <StateBlock v-else-if="!assessment" state="loading" :rows="2" />
            <template v-else>
              <div class="verdict">
                <StatusPill :text="scorePill(assessment.liquidity).text" :tone="scorePill(assessment.liquidity).tone" dot />
                <span class="verdict-score tabular">{{ assessment.liquidity?.value ?? "—" }}</span>
                <span class="muted">/ 100 · 判断日 {{ assessment.pitDate || "—" }}</span>
              </div>

              <template v-if="liquidityEvidence.length">
                <h3 class="doc-section">判断依据：{{ liquiditySeriesName }}</h3>
                <ul class="evidence-list">
                  <li v-for="item in liquidityEvidence" :key="item.date + item.value">
                    <span class="tabular">{{ item.date }}</span>
                    <strong class="tabular">{{ item.value }}</strong>
                    <span v-if="item.published" class="muted">公布于 {{ item.published }}</span>
                  </li>
                </ul>
              </template>
              <p v-else class="doc-text muted">
                {{ pastDateUnsupported ? "公开数据无法回看历史观察日。" : "已公布数据不足两期，暂不判断。" }}
              </p>

              <h3 class="doc-section">其他维度</h3>
              <div class="dims">
                <div class="dim">
                  <span class="muted">增长</span>
                  <StatusPill :text="scorePill(assessment.growth).text" :tone="scorePill(assessment.growth).tone" />
                </div>
                <div class="dim">
                  <span class="muted">通胀</span>
                  <StatusPill :text="scorePill(assessment.inflation).text" :tone="scorePill(assessment.inflation).tone" />
                </div>
                <div class="dim">
                  <span class="muted">组合</span>
                  <span class="dim-text">{{ regimeText(assessment.regimeCode) || "—" }}</span>
                </div>
              </div>
              <p class="note">
                {{ assessment.notice || "这是规则计算，不是经济定论。" }}
                目前流动性只用利率类指标衡量，暂无货币供应量数据。
              </p>
            </template>
          </PanelCard>
        </div>

        <PanelCard title="宏观序列一览" :info="`共 ${series.length} 条，点击行查看走势`">
          <template #icon><span class="panel-icon">▤</span></template>
          <el-table :data="series" @row-click="(row: SeriesRow) => selectSeries(row.code)">
            <el-table-column label="名称" min-width="200">
              <template #default="scope">
                <span :class="{ 'row-active': scope.row.code === selectedCode }">{{ scope.row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="code" label="FRED 代码" width="130" />
            <el-table-column label="分类" width="90">
              <template #default="scope">{{ CATEGORY_LABELS[scope.row.category] ?? scope.row.category }}</template>
            </el-table-column>
            <el-table-column label="频率" width="90">
              <template #default="scope">{{ frequencyLabel(scope.row.frequency) }}</template>
            </el-table-column>
            <el-table-column label="单位" min-width="150">
              <template #default="scope">{{ unitLabel(scope.row.unit) }}</template>
            </el-table-column>
            <el-table-column label="调整" width="100">
              <template #default="scope">{{ adjustmentLabel(scope.row.seasonalAdjustment) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="scope">
                <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                  查看走势
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </PanelCard>

        <SourceFooter
          :as-of="pitDate || asOf || browserToday"
          :sources="[sourceLabel]"
          :stale="availability === 'unavailable'"
          note="观察日留空表示截至今天；利率「较上期」单位为百分点（pct）。"
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

.source-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}

.source-hint i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ok);
}

.filter-select {
  width: 200px;
}

.chart {
  width: 100%;
}

.chart-trend {
  height: 320px;
}

.chart-note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.verdict {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.verdict-score {
  font-family: var(--font-num);
  font-size: 30px;
  font-weight: 600;
  color: var(--text-strong);
}

.evidence-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.evidence-list li {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.evidence-list strong {
  color: var(--text-strong);
}

.evidence-list .muted {
  margin-left: auto;
}

.dims {
  display: grid;
  gap: 8px;
}

.dim {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.dim-text {
  color: var(--text);
}

.note {
  margin: 14px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.row-active {
  color: var(--accent);
  font-weight: 600;
}

.el-table {
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
