<script setup lang="ts">
/**
 * 热点主题。
 *
 * 数据全部来自 GET /api/v1/market/hotspots（合规公告聚合：主题分布、按日时间线、事件明细）。
 * 板块联动与外部新闻源如果后端没有给出，就按「暂无/未接入」如实展示，不用假数据填充。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asList, asNumber, asRecord, asString, dataOf, formatMoment, listOf } from "../api/json"

type Phase = "loading" | "ready" | "error"

interface HotspotTopic {
  eventType: string
  label: string
  count: number
}

interface HotspotTimeline {
  day: string
  count: number
  titles: string[]
}

interface HotspotEvent {
  id: string
  title: string
  canonicalUrl: string | null
  sourceName: string | null
  sourceBadge: string | null
  eventTypeLabel: string | null
  publishedAt: string | null
  usageRights: string | null
  qualityFlag: string | null
}

interface HotspotData {
  label: string
  count: number
  start: string | null
  end: string | null
  stream: string | null
  topics: HotspotTopic[]
  timeline: HotspotTimeline[]
  events: HotspotEvent[]
  sources: string[]
  pendingFeeds: string[]
  notice: string
}

interface MetaInfo {
  asOf: string | null
  source: string | null
  stale: boolean
  dataMode: string | null
  notice: string | null
  requestId: string | null
}

const DEFAULT_DAYS = 14

const phase = ref<Phase>("loading")
const errorMessage = ref("")
const errorCode = ref("")
const requestId = ref("")
const data = ref<HotspotData | null>(null)
const meta = ref<MetaInfo>({
  asOf: null,
  source: null,
  stale: false,
  dataMode: null,
  notice: null,
  requestId: null,
})
const range = ref<string[]>(defaultRange())
const activeTab = ref("topics")

function pad(value: number): string {
  return String(value).padStart(2, "0")
}

function isoDay(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 默认取最近 14 天，与后端 DEFAULT_HOTSPOT_DAYS 对齐。 */
function defaultRange(): string[] {
  const end = new Date()
  const start = new Date(end.getTime() - (DEFAULT_DAYS - 1) * 24 * 60 * 60 * 1000)
  return [isoDay(start), isoDay(end)]
}

const asOfText = computed(() => formatMoment(meta.value.asOf))
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")
const rangeText = computed(() => {
  if (data.value?.start && data.value.end) {
    return `${data.value.start} 至 ${data.value.end}`
  }
  return `${range.value[0] ?? "—"} 至 ${range.value[1] ?? "—"}`
})
const hasStreamData = computed(() => (data.value?.count ?? 0) > 0)
const chartDays = computed(() => (data.value?.timeline ?? []).map((item) => item.day))

/** 时间线柱状图：直接使用后端按日聚合的条数，不做插值补齐。 */
const timelineOption = computed(() => ({
  grid: { left: 48, right: 16, top: 24, bottom: 40 },
  tooltip: { trigger: "axis" },
  xAxis: {
    type: "category",
    data: chartDays.value,
    axisLabel: { color: "#95a6be", rotate: 45 },
    axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
  },
  yAxis: {
    type: "value",
    minInterval: 1,
    axisLabel: { color: "#95a6be" },
    splitLine: { lineStyle: { color: "rgba(149,166,190,0.15)" } },
  },
  series: [
    {
      name: "公告条数",
      type: "bar",
      data: (data.value?.timeline ?? []).map((item) => item.count),
      itemStyle: { color: "#3b82f6" },
      barMaxWidth: 22,
    },
  ],
}))

const topicOption = computed(() => ({
  tooltip: { trigger: "item" },
  series: [
    {
      name: "主题条数",
      type: "pie",
      radius: ["40%", "68%"],
      data: (data.value?.topics ?? []).map((item) => ({ name: item.label, value: item.count })),
      label: { color: "#95a6be" },
    },
  ],
}))

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

async function load(): Promise<void> {
  phase.value = "loading"
  errorMessage.value = ""
  errorCode.value = ""
  data.value = null

  const start = range.value[0] ?? ""
  const end = range.value[1] ?? ""
  const query = new URLSearchParams()
  if (start) {
    query.set("start", start)
  }
  if (end) {
    query.set("end", end)
  }
  const path = `/api/v1/market/hotspots${query.size ? `?${query.toString()}` : ""}`

  try {
    const response = await fetch(apiUrl(path), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const payload = dataOf(body)
    meta.value = readMeta(body)
    requestId.value = meta.value.requestId ?? ""
    data.value = {
      label: asString(payload?.label) ?? "暂无合规公告，不展示情绪分数",
      count: asNumber(payload?.count) ?? 0,
      start: asString(payload?.start),
      end: asString(payload?.end),
      stream: asString(payload?.stream),
      topics: listOf(payload?.topics).map((item) => ({
        eventType: asString(item.event_type) ?? "",
        label: asString(item.label) ?? "未命名主题",
        count: asNumber(item.count) ?? 0,
      })),
      timeline: listOf(payload?.timeline).map((item) => ({
        day: asString(item.day) ?? "—",
        count: asNumber(item.count) ?? 0,
        titles: asList(item.titles).flatMap((title) => {
          const text = asString(title)
          return text ? [text] : []
        }),
      })),
      events: listOf(payload?.events).map((item) => ({
        id: asString(item.id) ?? "",
        title: asString(item.title) ?? "未命名公告",
        canonicalUrl: asString(item.canonical_url),
        sourceName: asString(item.source_name),
        sourceBadge: asString(item.source_badge),
        eventTypeLabel: asString(item.event_type_label),
        publishedAt: asString(item.published_at),
        usageRights: asString(item.usage_rights),
        qualityFlag: asString(item.quality_flag),
      })),
      sources: asList(payload?.sources).flatMap((item) => {
        const text = asString(item)
        return text ? [text] : []
      }),
      pendingFeeds: asList(payload?.pending_feeds).flatMap((item) => {
        const text = asString(item)
        return text ? [text] : []
      }),
      notice: asString(payload?.notice) ?? "",
    }
    phase.value = "ready"
  } catch (error) {
    const apiError = toApiError(error)
    errorMessage.value = apiError.message
    errorCode.value = apiError.code
    requestId.value = apiError.requestId ?? ""
    phase.value = "error"
  }
}

function resetRange(): void {
  range.value = defaultRange()
  void load()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <header class="page-head">
      <div>
        <h1>热点主题</h1>
        <p class="muted">
          主题分布、热度日线、公告明细都来自合规公告流；板块联动与外部新闻源未接入时如实标注。
        </p>
      </div>
      <div class="range-bar">
        <el-date-picker
          v-model="range"
          type="daterange"
          value-format="YYYY-MM-DD"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          range-separator="至"
          :clearable="false"
        />
        <el-button :loading="phase === 'loading'" @click="load">查询</el-button>
        <el-button text :loading="phase === 'loading'" @click="resetRange">最近 14 天</el-button>
      </div>
    </header>

    <p v-if="phase === 'ready'" class="meta-line muted">
      窗口 {{ rangeText }} · 数据时间 {{ asOfText }} · 来源 {{ meta.source ?? "—" }} · 请求号
      {{ requestId || "—" }}
    </p>

    <div v-if="demoMode" class="banner" role="status">
      当前返回的是{{ meta.dataMode }}数据，不是实时数据。
    </div>
    <div v-if="phase === 'ready' && meta.stale" class="banner" role="status">
      数据已过期（stale），展示的是上次成功抓取的结果。
    </div>

    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在汇总热点主题</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法加载热点数据</h1>
      <p>{{ errorMessage }}</p>
      <p v-if="errorCode" class="muted">错误码 {{ errorCode }} · 请求号 {{ requestId || "—" }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else-if="data">
      <div class="stat-row">
        <div class="card">
          <h2>热度标签</h2>
          <p class="state">{{ data.label }}</p>
          <p class="muted">收录 {{ data.count }} 条合规公告</p>
        </div>
        <div class="card">
          <h2>数据流</h2>
          <p class="state">{{ data.stream ?? "公告流" }}</p>
          <p class="muted">
            来源：{{ data.sources.length ? data.sources.join("、") : "本次窗口没有可用来源" }}
          </p>
        </div>
        <div class="card">
          <h2>主题数</h2>
          <p class="state">{{ data.topics.length }}</p>
          <p class="muted">按公告事件类型聚合</p>
        </div>
        <div class="card">
          <h2>有公告的交易日</h2>
          <p class="state">{{ data.timeline.length }}</p>
          <p class="muted">窗口内按日统计，缺失日期不补零</p>
        </div>
      </div>

      <section v-if="!hasStreamData" class="panel">
        <h2>暂无可展示的数据</h2>
        <p class="muted">{{ data.notice || "所选窗口内后端没有收录任何合规公告。" }}</p>
        <el-empty :description="data.notice || '暂无可展示的数据'" />
      </section>

      <template v-else>
        <section class="panel">
          <div class="panel-head">
            <h2>主题热度</h2>
            <span class="muted">共 {{ data.count }} 条公告</span>
          </div>
          <el-tabs v-model="activeTab">
            <el-tab-pane label="主题分布" name="topics">
              <el-table :data="data.topics" size="small" stripe>
                <el-table-column prop="label" label="主题" min-width="160" />
                <el-table-column prop="eventType" label="事件类型" min-width="140" />
                <el-table-column label="条数" width="100">
                  <template #default="{ row }">{{ row.count }}</template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
            <el-tab-pane label="主题占比图" name="topicChart">
              <component :is="'VChart'" class="chart" :option="topicOption" autoresize />
            </el-tab-pane>
            <el-tab-pane label="热度变化" name="timeline">
              <component :is="'VChart'" class="chart" :option="timelineOption" autoresize />
              <el-table :data="data.timeline" size="small" stripe>
                <el-table-column prop="day" label="日期" width="130" />
                <el-table-column label="条数" width="90">
                  <template #default="{ row }">{{ row.count }}</template>
                </el-table-column>
                <el-table-column label="当日标题" min-width="320">
                  <template #default="{ row }">
                    <span v-if="!row.titles.length" class="muted">—</span>
                    <ul v-else class="title-list">
                      <li v-for="title in row.titles" :key="title">{{ title }}</li>
                    </ul>
                  </template>
                </el-table-column>
              </el-table>
            </el-tab-pane>
          </el-tabs>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>新闻事件明细</h2>
            <span class="muted">{{ data.events.length }} 条</span>
          </div>
          <el-table :data="data.events" size="small" stripe>
            <el-table-column label="公布时间" width="160">
              <template #default="{ row }">{{ formatMoment(row.publishedAt) }}</template>
            </el-table-column>
            <el-table-column prop="title" label="标题" min-width="280" />
            <el-table-column label="类型" width="130">
              <template #default="{ row }">{{ row.eventTypeLabel ?? "—" }}</template>
            </el-table-column>
            <el-table-column label="来源" width="150">
              <template #default="{ row }">
                {{ row.sourceBadge ?? row.sourceName ?? "—" }}
              </template>
            </el-table-column>
            <el-table-column label="使用权限" width="120">
              <template #default="{ row }">{{ row.usageRights ?? "—" }}</template>
            </el-table-column>
            <el-table-column label="质量标记" width="120">
              <template #default="{ row }">{{ row.qualityFlag ?? "—" }}</template>
            </el-table-column>
            <el-table-column label="原文" width="90">
              <template #default="{ row }">
                <a v-if="row.canonicalUrl" :href="row.canonicalUrl" target="_blank" rel="noreferrer">
                  打开
                </a>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-if="!data.events.length" description="后端只返回了聚合计数，没有事件明细" />
        </section>
      </template>

      <section class="panel">
        <div class="panel-head">
          <h2>板块联动</h2>
        </div>
        <el-empty description="暂无可展示的数据" />
        <p class="muted">
          公告与行业成分目前没有可用的关联结果，因此不做联动推断。板块强弱请见
          <RouterLink to="/market/sectors">板块排行</RouterLink>。
        </p>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>尚未接入的来源</h2>
        </div>
        <ul v-if="data.pendingFeeds.length" class="title-list">
          <li v-for="feed in data.pendingFeeds" :key="feed">{{ feed }}</li>
        </ul>
        <p v-else class="muted">后端没有返回待接入来源清单。</p>
      </section>

      <p v-if="data.notice" class="muted">{{ data.notice }}</p>
    </template>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 22px;
}

.range-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.meta-line {
  margin: 0 0 16px;
  word-break: break-all;
}

.stat-row {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
  margin-bottom: 16px;
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

.state {
  margin: 4px 0;
  font-size: 20px;
  font-weight: 700;
}

.chart {
  width: 100%;
  height: 300px;
}

.title-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 4px;
}

@media (min-width: 768px) {
  .stat-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 1280px) {
  .stat-row {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
</style>
