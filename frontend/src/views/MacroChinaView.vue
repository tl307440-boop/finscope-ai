<script setup lang="ts">
/**
 * 中国经济宏观（/macro/china，效果图 2 的「中国经济」页签内容）。
 *
 * 真实接口（与 backend/app/api/v1/research_board.py 的 read_china_macro 一致）：
 *  - GET /api/v1/macro/china
 *      data = { series: [{ code, name, unit, status, observation_count, latest_value,
 *                          latest_observation_date, source, published_at }], notice }
 *      · status：stored（已入库）/ stale（数据陈旧）/ unavailable（库里没有观测值）
 *      · 没有任何观测值时仍然返回 200，只是每条 status=unavailable
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *      · 与 FRED 共用同一张观测表；按观测日升序，limit 保留最近 N 条
 *
 * 不编造数据：中国宏观序列由后台任务从东方财富 / 商务部公开接口自动同步（也可由管理员 POST /api/v1/macro/china/observations 导入 CSV），
 * 导入前本页保持诚实空态；走势图只在真的取到 ≥2 个观测值时才画。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, formatAmount, listOf } from "../api/json"
import { cssColor, unitLabel } from "../api/macro"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"

type Phase = "loading" | "ready" | "error"

interface ChinaSeries {
  code: string
  name: string
  unit: string
  status: string
  count: number
  latestValue: string
  latestDate: string
}

interface Observation {
  date: string
  value: number
}

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const IMPORT_HINT = "后台每天从公开接口自动同步，首次同步完成前显示为暂无数据"

const phase = ref<Phase>("loading")
const failure = ref("")
const rows = ref<ChinaSeries[]>([])

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<Observation[]>([])
const selectedCode = ref("")

const today = new Date()
const pad = (part: number): string => String(part).padStart(2, "0")
const browserToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`
const queryAsOf = ref(browserToday)
const appliedAsOf = ref(browserToday)

const selectedRow = computed<ChinaSeries | null>(
  () => rows.value.find((row) => row.code === selectedCode.value) ?? null,
)

const availableRows = computed<ChinaSeries[]>(() =>
  rows.value.filter((row) => row.status === "stored" || row.status === "stale"),
)

const anyAvailable = computed<boolean>(() => availableRows.value.length > 0)

const latestDate = computed<string>(
  () => availableRows.value.map((row) => row.latestDate).filter(Boolean).sort().pop() ?? "",
)

/** 与后端 public_status 的三档一一对应。 */
function statusPill(row: ChinaSeries): { text: string; tone: "ok" | "warn" | "muted" } {
  if (row.status === "stored") {
    return { text: "已同步", tone: "ok" }
  }
  if (row.status === "stale") {
    return { text: "数据陈旧", tone: "warn" }
  }
  return { text: "暂无数据", tone: "muted" }
}

function statusNote(row: ChinaSeries): string {
  if (row.status === "stored") {
    return `已同步 ${row.count} 期，最新 ${row.latestDate || "—"}`
  }
  if (row.status === "stale") {
    return `已同步 ${row.count} 期，但最新一期 ${row.latestDate || "—"} 距今超过 60 天（上游更新滞后或同步失败）`
  }
  return `暂无数据，${IMPORT_HINT}`
}

function formatValue(value: string | number, unit: string): string {
  const parsed = typeof value === "number" ? value : Number(value)
  if (!Number.isFinite(parsed)) {
    return typeof value === "string" && value ? value : "—"
  }
  if (unit === "亿元") {
    return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
  }
  if (unit === "CNY") {
    return formatAmount(parsed, "元")
  }
  return parsed.toLocaleString("zh-CN", { minimumFractionDigits: 1, maximumFractionDigits: 2 })
}

function tileUnit(row: ChinaSeries): string {
  if (row.unit === "亿元") {
    return row.latestValue ? "亿元" : ""
  }
  if (!row.latestValue || row.unit === "CNY") {
    return ""
  }
  return unitLabel(row.unit) === "%" ? "%" : ""
}

const chartOption = computed<EChartsCoreOption | null>(() => {
  const points = observations.value
  if (points.length < 2) {
    return null
  }
  const accent = cssColor("--accent", "#3b82f6")
  const unit = selectedRow.value?.unit ?? ""
  const zoom = points.length > 60
  return {
    grid: { left: 8, right: 16, top: 16, bottom: zoom ? 44 : 4, containLabel: true },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value: unknown): string =>
        typeof value === "number" ? `${formatValue(value, unit)}${unit === "Percent" ? "%" : ""}` : "—",
    },
    xAxis: {
      type: "category",
      data: points.map((row) => row.date.slice(0, 7)),
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: "value", scale: true },
    series: [
      {
        name: selectedRow.value?.name ?? selectedCode.value,
        type: "line",
        showSymbol: points.length <= 36,
        symbolSize: 5,
        data: points.map((row) => row.value),
        lineStyle: { color: accent, width: 2 },
        itemStyle: { color: accent },
        areaStyle: { color: accent, opacity: 0.1 },
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

function parseSeries(value: unknown): ChinaSeries[] {
  return listOf(value).flatMap((item) => {
    const code = asString(item.code)
    const name = asString(item.name)
    if (!code || !name) {
      return []
    }
    return [
      {
        code,
        name,
        unit: asString(item.unit) ?? "",
        status: asString(item.status) ?? "",
        count: asNumber(item.observation_count) ?? 0,
        latestValue: asString(item.latest_value) ?? "",
        latestDate: asString(item.latest_observation_date) ?? "",
      },
    ]
  })
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

async function loadObservations(): Promise<void> {
  const code = selectedCode.value
  if (!code || !anyAvailable.value) {
    detailPhase.value = "idle"
    observations.value = []
    return
  }
  detailPhase.value = "loading"
  detailError.value = ""
  observations.value = []
  try {
    const params = new URLSearchParams({ limit: "240" })
    if (appliedAsOf.value) {
      params.set("as_of", appliedAsOf.value)
    }
    const response = await fetch(
      apiUrl(`/api/v1/macro/series/${encodeURIComponent(code)}/observations?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const parsed = Array.isArray(data?.observations) ? data.observations.flatMap(parseObservation) : []
    parsed.sort((left, right) => (left.date < right.date ? -1 : left.date > right.date ? 1 : 0))
    observations.value = parsed
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

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const response = await fetch(apiUrl("/api/v1/macro/china"))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    rows.value = parseSeries(data?.series)
    selectedCode.value = (availableRows.value[0] ?? rows.value[0])?.code ?? ""
    phase.value = "ready"
    await loadObservations()
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  }
}

async function applyAsOf(): Promise<void> {
  appliedAsOf.value = queryAsOf.value || browserToday
  await loadObservations()
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
          v-model="queryAsOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日"
          class="asof-select"
          :disabled="!anyAvailable"
          @change="applyAsOf"
        />
        <el-button @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="china" />
      </template>
      <template #filters>
        <span class="source-hint" :class="{ off: !anyAvailable }">
          <i aria-hidden="true" />数据来源：东方财富 / 商务部公开接口（{{ anyAvailable ? `已同步，最新 ${latestDate}` : "尚未同步" }}）
        </span>
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock v-if="phase === 'loading'" state="loading" message="正在读取中国宏观数据" />

      <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

      <template v-else>
        <StateBlock v-if="!rows.length" state="empty" message="暂无中国宏观指标" />

        <template v-else>
          <StateBlock
            v-if="!anyAvailable"
            state="empty"
            title="中国宏观数据尚未同步"
            :message="`CPI、PPI、PMI、M2、工业增加值、社融暂无数据：${IMPORT_HINT}。`"
          />

          <div class="grid-6">
            <MetricTile
              v-for="row in rows"
              :key="row.code"
              :label="row.name"
              :value="row.latestValue ? formatValue(row.latestValue, row.unit) : '—'"
              :unit="tileUnit(row)"
              :period="row.latestDate ? row.latestDate.slice(0, 7) : '暂无数据'"
              :info="statusNote(row)"
              delta-label="状态"
            >
              <template #delta>
                <StatusPill :text="statusPill(row).text" :tone="statusPill(row).tone" />
              </template>
            </MetricTile>
          </div>

          <div class="grid-2-1">
            <PanelCard title="指标走势" info="只显示所选观察日已经公布的观测值">
              <template #icon><span class="panel-icon">◠</span></template>
              <template v-if="anyAvailable" #actions>
                <el-select v-model="selectedCode" class="filter-select" size="small" @change="selectSeries">
                  <el-option
                    v-for="item in availableRows"
                    :key="item.code"
                    :value="item.code"
                    :label="item.name"
                  />
                </el-select>
              </template>

              <p v-if="!anyAvailable" class="empty-note">同步到数据后在此显示走势</p>
              <StateBlock v-else-if="detailPhase === 'loading'" state="loading" message="正在查询观测值" />
              <StateBlock
                v-else-if="detailPhase === 'error'"
                state="error"
                :message="detailError"
                @retry="loadObservations"
              />
              <template v-else-if="detailPhase === 'ready'">
                <VChart v-if="chartOption" class="chart chart-trend" :option="chartOption" autoresize />
                <StateBlock v-else state="empty" message="所选观察日已公布的数据不足两期，暂不画走势" />
                <p v-if="observations.length" class="chart-note">
                  {{ selectedRow?.name }}：共 {{ observations.length }} 期，单位 {{ unitLabel(selectedRow?.unit ?? "") }}
                </p>
              </template>
            </PanelCard>

            <PanelCard title="指标清单" info="已同步 / 数据陈旧（最新一期距今超过 60 天）/ 暂无数据">
              <template #icon><span class="panel-icon">▤</span></template>
              <el-table :data="rows" @row-click="(row: ChinaSeries) => anyAvailable && selectSeries(row.code)">
                <el-table-column prop="name" label="指标" min-width="170" />
                <el-table-column label="单位" width="70">
                  <template #default="scope">{{ unitLabel(scope.row.unit) }}</template>
                </el-table-column>
                <el-table-column label="状态" width="96">
                  <template #default="scope">
                    <StatusPill :text="statusPill(scope.row).text" :tone="statusPill(scope.row).tone" />
                  </template>
                </el-table-column>
                <el-table-column v-if="anyAvailable" label="最新" width="100">
                  <template #default="scope">
                    <span class="tabular">{{ scope.row.latestDate ? scope.row.latestDate.slice(0, 7) : "—" }}</span>
                  </template>
                </el-table-column>
              </el-table>
              <p class="chart-note">已同步 {{ availableRows.length }} / {{ rows.length }} 项。</p>
            </PanelCard>
          </div>
        </template>

        <SourceFooter
          :as-of="latestDate || appliedAsOf"
          :sources="['东方财富数据中心（国家统计局、中国人民银行）', '商务部数据中心（社会融资规模）']"
          :stale="!anyAvailable"
          :note="anyAvailable ? '公布日按保守上界计算（如 CPI 取次月 15 日），回看只会晚看到、不会提前看到。' : IMPORT_HINT"
        />
      </template>
    </div>
  </main>
</template>

<style scoped>
.asof-select {
  width: 180px;
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

.source-hint.off i {
  background: var(--faint);
}

.filter-select {
  width: 220px;
}

.chart {
  width: 100%;
}

.chart-trend {
  height: 300px;
}

.empty-note {
  display: grid;
  place-items: center;
  min-height: 200px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.chart-note {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.el-table {
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
