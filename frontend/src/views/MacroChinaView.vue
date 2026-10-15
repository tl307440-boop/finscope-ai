<script setup lang="ts">
/**
 * 中国经济宏观（/macro/china）。
 *
 * 真实接口（已用 admin 令牌逐一打过；字段以 backend/app/api/v1/research_board.py 的
 * ChinaSeriesItem 与 backend/app/schemas/macro.py 的 ObservationPage 为准）：
 *  - GET /api/v1/macro/china
 *      data.series[] = { code, name, unit, observation_count, status,
 *                        latest_value, latest_observation_date, source, published_at }
 *      status ∈ unavailable | stale | stored（stored 后超过 60 天记为 stale）
 *      data.notice  = 后端对中国宏观数据来源的说明
 *  - GET /api/v1/macro/series/{series_code}/observations?start=&end=&as_of=&limit=
 *      data = { series_code, pit_date, time_precision, observations[],
 *               empty_message, truncated }
 *      observations[] = { observation_date, value, unit, realtime_start,
 *                         realtime_end, revision_no, time_precision }
 *
 * 中国宏观需要人工导入正式 CSV（管理员 POST /api/v1/macro/china/observations）
 * 后才会从 unavailable 变为有数据，因此「不可用」是真实状态：本页如实展示后端
 * 状态、条数与来源，不补任何数字。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asList, asNumber, asRecord, asString, formatMoment, listOf } from "../api/json"
import { describeObservations, type MacroObservation } from "../api/macro"

type Phase = "loading" | "ready" | "error"

interface ChinaSeriesRow {
  code: string
  name: string
  unit: string | null
  status: string
  observationCount: number | null
  latestValue: string | null
  latestDate: string | null
  source: string | null
  publishedAt: string | null
}

const phase = ref<Phase>("loading")
const failure = ref("")
const notice = ref("")
const series = ref<ChinaSeriesRow[]>([])
const selectedCode = ref("")
const asOf = ref("")
const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<MacroObservation[]>([])
const pitDate = ref("")
const emptyMessage = ref("")
const truncated = ref(false)

const hasSeries = computed<boolean>(() => series.value.length > 0)
const storedCount = computed<number>(
  () => series.value.filter((item) => item.status === "stored" || item.status === "stale").length,
)
const unavailableCount = computed<number>(() => series.value.length - storedCount.value)
const selectedRow = computed<ChinaSeriesRow | null>(
  () => series.value.find((item) => item.code === selectedCode.value) ?? null,
)

/** 状态文案完全按后端 status 映射，未知取值原样显示，不猜测含义。 */
function statusView(row: ChinaSeriesRow): { label: string; tag: "success" | "warning" | "danger" | "info" } {
  if (row.status === "stored") {
    return { label: "已入库", tag: "success" }
  }
  if (row.status === "stale") {
    return { label: "数据陈旧", tag: "warning" }
  }
  if (row.status === "unavailable") {
    return { label: "不可用", tag: "danger" }
  }
  return { label: row.status, tag: "info" }
}

const statusLegend = computed<string>(
  () => `共 ${series.value.length} 条序列：已入库或数据陈旧 ${storedCount.value} 条，不可用 ${unavailableCount.value} 条`,
)

const sourceLabel = computed<string>(() => {
  const row = selectedRow.value
  if (!row) {
    return "—"
  }
  const parts: string[] = []
  if (row.source) {
    parts.push(row.source)
  }
  if (row.publishedAt) {
    parts.push(`公布于 ${formatMoment(row.publishedAt)}`)
  }
  return parts.length ? parts.join(" · ") : "—"
})

function parseSeries(value: unknown): ChinaSeriesRow[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.code)
    const name = asString(item.name)
    const status = asString(item.status)
    if (!code || !name || !status) {
      return []
    }
    return [
      {
        code,
        name,
        status,
        unit: asString(item.unit),
        observationCount: asNumber(item.observation_count),
        latestValue: asString(item.latest_value),
        latestDate: asString(item.latest_observation_date),
        source: asString(item.source),
        publishedAt: asString(item.published_at),
      },
    ]
  })
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

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  detailError.value = ""
  detailPhase.value = "idle"
  observations.value = []
  try {
    const response = await fetch(apiUrl("/api/v1/macro/china"))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    notice.value = asString(data?.notice) ?? ""
    series.value = parseSeries(data?.series)
    const preferred = series.value.find((item) => item.status === "stored" || item.status === "stale")
    selectedCode.value = (preferred ?? series.value[0])?.code ?? ""
    phase.value = "ready"
    if (selectedCode.value) {
      await loadObservations()
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
    observations.value = asList(data?.observations).flatMap(parseObservation)
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

const observationLines = computed<string[]>(() =>
  describeObservations({ emptyMessage: emptyMessage.value || null, observations: observations.value }),
)

const chartOption = computed<EChartsCoreOption | null>(() => buildTrendOption(observations.value))

/** 尾部去零：后端 value 是字符串形式的 Decimal，保留原值优先。 */
function plainText(value: string): string {
  return value.includes(".") ? value.replace(/0+$/, "").replace(/\.$/, "") : value
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
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
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
        return `${point.date} · ${plainText(point.raw)}`
      },
    },
    xAxis: {
      type: "category",
      data: points.map((point) => point.date),
      axisLabel: { color: "var(--muted)", hideOverlap: true },
      axisLine: { lineStyle: { color: "var(--line)" } },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "var(--muted)" },
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
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取中国宏观序列</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取中国宏观序列</h1>
      <p class="muted">{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <section class="panel">
        <h1>中国经济宏观</h1>
        <p class="muted">{{ statusLegend }}</p>
        <p class="muted">
          状态口径：<strong>已入库</strong>＝库里已有观测值且最新观测日距今不超过 60 天；
          <strong>数据陈旧</strong>＝有观测值但已超过 60 天；<strong>不可用</strong>＝库里没有这条序列的观测值。
        </p>
        <el-alert v-if="notice" type="info" :closable="false" title="数据来源说明">
          <template #default>{{ notice }}</template>
        </el-alert>
      </section>

      <section class="panel">
        <h2>序列列表</h2>
        <el-empty v-if="!hasSeries" description="后端没有返回任何中国宏观序列" />
        <el-table v-else :data="series" stripe @row-click="(row: ChinaSeriesRow) => selectSeries(row.code)">
          <el-table-column prop="code" label="代码" width="170" />
          <el-table-column prop="name" label="名称" min-width="220" />
          <el-table-column label="状态" width="120">
            <template #default="scope">
              <el-tag :type="statusView(scope.row).tag" effect="dark" size="small">
                {{ statusView(scope.row).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="单位" width="150">
            <template #default="scope">{{ scope.row.unit ?? "—" }}</template>
          </el-table-column>
          <el-table-column label="最新观测日" width="140">
            <template #default="scope">{{ scope.row.latestDate ?? "—" }}</template>
          </el-table-column>
          <el-table-column label="最新值" width="120">
            <template #default="scope">{{ scope.row.latestValue ?? "—" }}</template>
          </el-table-column>
          <el-table-column label="观测条数" width="110">
            <template #default="scope">{{ scope.row.observationCount ?? "—" }}</template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="scope">
              <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                查看走势
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="panel">
        <h2>序列走势</h2>
        <div class="filter-row">
          <el-select v-model="selectedCode" class="filter-select" @change="selectSeries">
            <el-option
              v-for="item in series"
              :key="item.code"
              :value="item.code"
              :label="`${item.code} ${item.name}`"
            />
          </el-select>
          <el-date-picker
            v-model="asOf"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="观察日（留空＝今天）"
            class="filter-date"
          />
          <el-button type="primary" :loading="detailPhase === 'loading'" @click="loadObservations">
            查询
          </el-button>
        </div>
        <p class="muted">
          观察日按后端时点过滤：只显示当天已经公布的观测值（realtime_start 早于观察日）。留空表示截止今天。
        </p>

        <div v-if="detailPhase === 'loading'" class="skeleton" aria-busy="true">正在查询观测值</div>

        <template v-else-if="detailPhase === 'error'">
          <p role="alert" class="macro-error">{{ detailError }}</p>
          <button class="retry" type="button" @click="loadObservations">重试</button>
        </template>

        <template v-else-if="detailPhase === 'ready'">
          <el-descriptions v-if="selectedRow" :column="2" border class="macro-descriptions">
            <el-descriptions-item label="序列代码">{{ selectedRow.code }}</el-descriptions-item>
            <el-descriptions-item label="序列名称">{{ selectedRow.name }}</el-descriptions-item>
            <el-descriptions-item label="序列状态">
              {{ statusView(selectedRow).label }}
            </el-descriptions-item>
            <el-descriptions-item label="后端时点">{{ pitDate || "—" }}</el-descriptions-item>
            <el-descriptions-item label="单位">{{ selectedRow.unit ?? "—" }}</el-descriptions-item>
            <el-descriptions-item label="最新观测">
              {{ selectedRow.latestDate ?? "—" }}
              <template v-if="selectedRow.latestValue"> · {{ selectedRow.latestValue }}</template>
            </el-descriptions-item>
            <el-descriptions-item label="来源与公布时间">{{ sourceLabel }}</el-descriptions-item>
            <el-descriptions-item label="观测条数">
              {{ selectedRow.observationCount ?? "—" }}
            </el-descriptions-item>
          </el-descriptions>

          <el-alert
            v-if="selectedRow && statusView(selectedRow).tag === 'danger'"
            type="warning"
            :closable="false"
            class="macro-alert"
            :title="`${selectedRow.code} 当前为「不可用」：后端库里没有这条序列的观测值，需要管理员用 POST /api/v1/macro/china/observations 导入正式 CSV 才会有数据。`"
          />

          <el-alert
            v-if="truncated"
            type="info"
            :closable="false"
            class="macro-alert"
            title="观测值多于本次上限，图表只画出了返回的部分。"
          />

          <VChart v-if="chartOption" class="macro-chart" :option="chartOption" autoresize />
          <p v-else class="muted">少于两个可解析的观测值，暂不画走势图。</p>

          <el-empty v-if="!observations.length" :description="emptyMessage || '该观察日没有可见的观测值'" />

          <template v-else>
            <p class="muted">共 {{ observations.length }} 条观测值（按后端返回顺序）。</p>
            <ul class="macro-lines">
              <li v-for="line in observationLines" :key="line">{{ line }}</li>
            </ul>
          </template>
        </template>
      </section>
    </template>
  </main>
</template>

<style scoped>
.el-alert {
  margin-top: 12px;
}

.macro-alert {
  margin-top: 12px;
}

.macro-error {
  color: var(--danger);
}

.macro-descriptions {
  margin-bottom: 12px;
  --el-descriptions-table-border: 1px solid var(--line);
  --el-text-color-regular: var(--text);
  --el-fill-color-light: color-mix(in srgb, var(--text) 6%, transparent);
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

.filter-date {
  width: 220px;
}

.macro-chart {
  height: 300px;
  margin-top: 12px;
}

.macro-lines {
  margin: 8px 0 0;
  padding-left: 20px;
  color: var(--muted);
  font-size: 13px;
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
