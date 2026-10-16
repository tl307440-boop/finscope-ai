<script setup lang="ts">
/**
 * 中国经济宏观（/macro/china，效果图 2 的「中国经济」页签内容）。
 *
 * 真实接口（与 backend/app/api/v1/research_board.py 的 read_china_macro 一致）：
 *  - GET /api/v1/macro/china
 *      data = { series: [{ code, name, unit, status, observation_count, latest_value,
 *                          latest_observation_date, source, published_at }], notice }
 *      · status 由 backend/app/services/china_macro.py 的 public_status 给出：
 *        stored（已入库）/ stale（数据陈旧）/ unavailable（库里没有观测值）
 *      · 后端 china_macro 目录里没有任何观测值时，本接口**仍然返回 200**，只是每条 status=unavailable
 *  - GET /api/v1/macro/series/{series_code}/observations?limit=&as_of=
 *      data = { series_code, pit_date, time_precision, observations[], empty_message, truncated }
 *      · FRED 的观测表与 China CSV 导入共用同一张表，所以中国序列一旦导入即可用这个接口读
 *
 * 不编造数据（AGENTS.md + docs/UI-设计规范.md §八）：
 *  - 中国宏观序列在导入 CSV 之前是真正不可用的，本页因此以 AvailabilityNotice 式空态收尾，
 *    并用 StateBlock state="empty" 写明原因（管理员需 POST /api/v1/macro/china/observations 导入）。
 *  - 走势图只在真的取到 ≥2 个观测值时才画；status=unavailable 的序列一律不画、不补点。
 */
import { computed, onMounted, ref } from "vue"
import type { EChartsCoreOption } from "echarts/core"
import { VChart } from "../charts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, listOf } from "../api/json"
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
  source: string
  publishedAt: string
}

interface Observation {
  date: string
  value: number
  raw: string
  realtimeStart: string
  revision: number
}

const TABS = [
  { key: "us", label: "美国经济", to: "/macro/us" },
  { key: "liquidity", label: "货币与流动性", to: "/macro/liquidity" },
  { key: "china", label: "中国经济", to: "/macro/china" },
  { key: "scenarios", label: "宏观综合判断", to: "/macro/scenarios" },
]

const phase = ref<Phase>("loading")
const failure = ref("")
const notice = ref("")
const rows = ref<ChinaSeries[]>([])

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const observations = ref<Observation[]>([])
const pitDate = ref("")
const emptyMessage = ref("")
const truncated = ref(false)
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

const unavailableRows = computed<ChinaSeries[]>(() =>
  rows.value.filter((row) => row.status !== "stored" && row.status !== "stale"),
)

const anyAvailable = computed<boolean>(() => availableRows.value.length > 0)

const stale = computed<boolean>(() => phase.value === "ready" && !anyAvailable.value)

/** 每行状态的口径提示，与后端 public_status 的三档一一对应。 */
function statusPill(row: ChinaSeries): { text: string; tone: "ok" | "warn" | "muted" | "danger" } {
  if (row.status === "stored") {
    return { text: "已入库", tone: "ok" }
  }
  if (row.status === "stale") {
    return { text: "数据陈旧", tone: "warn" }
  }
  if (row.status === "unavailable") {
    return { text: "不可用", tone: "muted" }
  }
  return { text: row.status || "—", tone: "danger" }
}

function statusNote(row: ChinaSeries): string {
  if (row.status === "stored") {
    return `库里已有 ${row.count} 条观测值，最新观测日 ${row.latestDate || "—"}`
  }
  if (row.status === "stale") {
    return `有 ${row.count} 条观测值但最新观测日 ${row.latestDate || "—"} 距今超过 60 天`
  }
  if (row.status === "unavailable") {
    return "库里没有这条序列的观测值，需管理员导入 CSV"
  }
  return "后端返回了未收录的状态值，原样显示、不做解释"
}

function formatValue(value: string): string {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return value || "—"
  }
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 })
}

const chartOption = computed<EChartsCoreOption | null>(() => {
  if (observations.value.length < 2) {
    return null
  }
  const points = observations.value
  const values = points.map((row) => row.value)
  const last = values[values.length - 1]
  const previous = values[values.length - 2]
  const color =
    last === undefined || previous === undefined || last === previous
      ? "var(--accent)"
      : last > previous
        ? "var(--rise)"
        : "var(--fall)"
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
        return point ? `${point.date} · ${point.raw}` : ""
      },
    },
    xAxis: {
      type: "category",
      data: points.map((row) => row.date),
      axisLine: { lineStyle: { color: "var(--line)" } },
      axisLabel: { color: "var(--muted)", fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { show: false },
      axisLabel: { color: "var(--muted)", fontSize: 11 },
      splitLine: { lineStyle: { color: "var(--line)" } },
    },
    series: [
      {
        name: selectedCode.value,
        type: "line",
        symbol: points.length <= 60 ? "circle" : "none",
        symbolSize: 5,
        data: values,
        lineStyle: { color, width: 2 },
        itemStyle: { color },
        areaStyle: { color, opacity: 0.12 },
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
})

const observationLines = computed<string[]>(() =>
  observations.value.map(
    (row) =>
      `${row.date} · ${row.raw} ${selectedRow.value?.unit ?? ""} · 发布日次日可见 ${row.realtimeStart || "—"} · 第 ${row.revision} 次`,
  ),
)

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
        unit: asString(item.unit) ?? "—",
        status: asString(item.status) ?? "",
        count: asNumber(item.observation_count) ?? 0,
        latestValue: asString(item.latest_value) ?? "",
        latestDate: asString(item.latest_observation_date) ?? "",
        source: asString(item.source) ?? "",
        publishedAt: asString(item.published_at) ?? "",
      },
    ]
  })
}

function parseObservation(value: unknown): Observation[] {
  const record = asRecord(value)
  const date = asString(record?.observation_date)
  const raw = asString(record?.value)
  const amount = asNumber(record?.value)
  if (!date || amount === null || raw === null) {
    return []
  }
  return [
    {
      date,
      value: amount,
      raw,
      realtimeStart: asString(record?.realtime_start) ?? "",
      revision: asNumber(record?.revision_no) ?? 0,
    },
  ]
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
    notice.value = asString(data?.notice) ?? ""
    rows.value = parseSeries(data?.series)
    // 原行为：优先选已入库/陈旧的序列，没有就落到第一条。
    selectedCode.value =
      (availableRows.value[0] ?? rows.value[0])?.code ?? ""
    phase.value = "ready"
    if (selectedCode.value) {
      await loadObservations()
    }
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function applyAsOf(): Promise<void> {
  appliedAsOf.value = queryAsOf.value
  await loadObservations()
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
          v-model="queryAsOf"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日"
          class="asof-select"
          @change="applyAsOf"
        />
        <el-button @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="TABS" model-value="china" />
      </template>
    </PageHeader>

    <div class="stack">
      <StateBlock
        v-if="phase === 'loading'"
        state="loading"
        message="正在读取中国宏观序列状态"
      />

      <StateBlock
        v-else-if="phase === 'error'"
        state="error"
        :message="failure"
        @retry="load"
      />

      <template v-else>
        <StateBlock
          v-if="!anyAvailable"
          state="stale"
          :as-of="appliedAsOf"
          message="中国宏观序列全部未入库：本页只如实展示目录与状态，不画任何走势曲线。"
          @retry="load"
        />

        <div class="grid-6">
          <MetricTile
            v-for="row in rows"
            :key="row.code"
            :label="row.name"
            :value="row.latestValue ? formatValue(row.latestValue) : '—'"
            :period="row.latestDate ? `${row.latestDate} · 最新观测日` : '暂无观测值'"
            :unit="row.unit"
            :info="statusNote(row)"
          >
            <template #delta>
              <StatusPill :text="statusPill(row).text" :tone="statusPill(row).tone" />
            </template>
          </MetricTile>
        </div>
        <p v-if="!rows.length" class="muted">后端没有返回任何中国宏观序列。</p>

        <div class="grid-2-1">
          <PanelCard
            title="序列走势"
            info="观测值与 FRED 共用同一张表；中国序列需管理员导入 CSV 后才有数据"
          >
            <template #icon><span class="panel-icon">◠</span></template>

            <div v-if="detailPhase === 'loading'">
              <StateBlock state="loading" message="正在查询观测值" />
            </div>

            <div v-else-if="detailPhase === 'error'">
              <StateBlock state="error" :message="detailError" @retry="loadObservations" />
            </div>

            <template v-else-if="detailPhase === 'ready'">
              <VChart v-if="chartOption" class="chart chart-trend" :option="chartOption" autoresize />
              <StateBlock
                v-else
                state="empty"
                :message="
                  emptyMessage ||
                  '该序列在当前观察日没有可见观测值（少于两个观测值时不画曲线）'
                "
                :as-of="pitDate"
              />

              <StateBlock
                v-if="truncated"
                state="stale"
                :as-of="pitDate"
                message="观测值多于本次上限，图表只画出了返回的部分。"
              />

              <template v-if="observations.length">
                <p class="muted">
                  共 {{ observations.length }} 条观测值（按后端返回顺序）·
                  后端时点 {{ pitDate || "—" }} ·
                  序列 {{ selectedRow?.code || "—" }} · 单位 {{ selectedRow?.unit || "—" }}。
                </p>
                <ul class="line-list">
                  <li v-for="line in observationLines" :key="line">{{ line }}</li>
                </ul>
              </template>
            </template>

            <StateBlock v-else state="empty" message="请选择一条序列查看走势" />
          </PanelCard>

          <PanelCard title="序列目录与状态" info="状态口径：已入库 / 数据陈旧（>60 天）/ 不可用">
            <template #icon><span class="panel-icon">▤</span></template>
            <el-table :data="rows" stripe @row-click="(row: ChinaSeries) => selectSeries(row.code)">
              <el-table-column prop="code" label="代码" width="118" />
              <el-table-column prop="name" label="名称" min-width="150" />
              <el-table-column label="状态" width="104">
                <template #default="scope">
                  <StatusPill
                    :text="statusPill(scope.row).text"
                    :tone="statusPill(scope.row).tone"
                  />
                </template>
              </el-table-column>
              <el-table-column label="观测值" width="86">
                <template #default="scope">
                  <span class="tabular">{{ scope.row.count }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="unit" label="单位" min-width="110" />
              <el-table-column label="操作" width="92">
                <template #default="scope">
                  <el-button link type="primary" size="small" @click.stop="selectSeries(scope.row.code)">
                    查看走势
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <p class="muted">
              已入库 {{ availableRows.length }} 条 · 不可用 {{ unavailableRows.length }} 条 ·
              共 {{ rows.length }} 条。
            </p>
          </PanelCard>
        </div>

        <PanelCard title="数据可用性说明" info="结尾空态：本页不补齐缺失数据，也不做替代估计">
          <template #icon><span class="panel-icon">ⓘ</span></template>
          <StateBlock
            v-if="!anyAvailable"
            state="empty"
            :as-of="appliedAsOf"
            :message="
              notice ||
              '中国宏观序列尚未导入：后端 catalog 定义了序列代码与单位，但库里没有观测值，因此所有序列的 status 都是 unavailable。'
            "
          />
          <template v-else>
            <p class="doc-text">
              以下序列仍不可用，页面上不显示数值、不画曲线：{{
                unavailableRows.map((row) => row.code).join(" / ") || "无"
              }}。
            </p>
            <p class="muted">{{ notice || "后端未提供额外说明。" }}</p>
          </template>
          <ul class="doc-list">
            <li>
              导入方式：管理员 POST <code>/api/v1/macro/china/observations</code> 上传国家统计局
              （或同源口径）CSV，导入后本页会按同一张观测表读到真实数值。
            </li>
            <li>
              CSV 未导入前，后端不返回任何观测值，因此这里保持空态与原因，不用随机数或估计值填版面。
            </li>
            <li>
              页脚时点说明：观察日 {{ appliedAsOf || "今天" }}，观测值可见性由后端按
              realtime_start 过滤（发布日当天不视为已可见）。
            </li>
          </ul>
        </PanelCard>

        <SourceFooter
          :as-of="appliedAsOf"
          :sources="['国家统计局（CSV 导入）', 'FRED（同一观测表）']"
          :stale="stale"
          :note="notice || '中国宏观序列需管理员导入 CSV 后才有观测值。'"
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

.chart {
  width: 100%;
  background: var(--bg);
  border-radius: 12px;
}

.chart-trend {
  height: 300px;
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
