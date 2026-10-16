<script setup lang="ts">
/**
 * 市场热点追踪（/hotspots）。
 *
 * 数据来源（全部是后端真实接口，前端不做任何热度打分）：
 *  - GET /api/v1/market/hotspots?start=&end=&industry_id=  合规公告聚合：主题分布、按日时间线、事件明细
 *  - GET /api/v1/industries                                行业下拉的选项
 *
 * 热度只按公告条数计算；主题排行里的「代表公告」「最近日期」直接取自 events，
 * 公司统计只认标题里「简称：」前缀，标题没有简称的公告不计入，绝不猜公司名或涨幅。
 */
import { computed, onMounted, ref, watch } from "vue"
import { Calendar, Document, Histogram, Link, OfficeBuilding, TrendCharts } from "@element-plus/icons-vue"
import type { EChartsOption } from "echarts"
import { apiUrl, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"

interface TopicRow {
  eventType: string
  label: string
  count: number
}

interface EventRow {
  id: string
  title: string
  url: string
  sourceBadge: string
  eventType: string
  eventTypeLabel: string
  publishedAt: string
  /** 本地日期 yyyy-MM-dd，用于按天聚合 */
  day: string
  /** 本地时刻 HH:mm；巨潮按日披露的公告是 00:00，此时为空 */
  time: string
}

interface IndustryOption {
  id: string
  name: string
}

const DEFAULT_DAYS = 14
const RECENT_LIMIT = 12
const TOPIC_COLORS = ["#3b82f6", "#22d3ee", "#a78bfa", "#fbbf24", "#34d399", "#fb923c", "#f472b6", "#94a3b8"]

function pad(value: number): string {
  return String(value).padStart(2, "0")
}

function localDay(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function daysAgo(days: number): string {
  const date = new Date()
  date.setDate(date.getDate() - days)
  return localDay(date)
}

const phase = ref<Phase>("loading")
const failure = ref("")

const windowRange = ref<[string, string]>([daysAgo(DEFAULT_DAYS - 1), daysAgo(0)])
const topic = ref("")
const industryId = ref("")
const showAll = ref(false)

const count = ref(0)
const notice = ref("")
const sources = ref<string[]>([])
const pendingFeeds = ref<string[]>([])
const topics = ref<TopicRow[]>([])
const events = ref<EventRow[]>([])
const industries = ref<IndustryOption[]>([])

const start = computed<string>(() => windowRange.value[0] ?? daysAgo(DEFAULT_DAYS - 1))
const end = computed<string>(() => windowRange.value[1] ?? daysAgo(0))
const sourceDisabled = computed(() => /ENABLE_NEWS|没有开启/.test(notice.value))
const industryName = computed(() => industries.value.find((item) => item.id === industryId.value)?.name ?? "")

/** 主题下拉：选项就是接口真实返回的事件类型，不预设题材名。 */
const topicOptions = computed(() =>
  topics.value.map((item) => ({ value: item.eventType, label: `${item.label}（${item.count}）` })),
)

const visibleEvents = computed<EventRow[]>(() =>
  topic.value ? events.value.filter((item) => item.eventType === topic.value) : events.value,
)
const visibleTopics = computed<TopicRow[]>(() =>
  topic.value ? topics.value.filter((item) => item.eventType === topic.value) : topics.value,
)
const visibleTotal = computed<number>(() => (topic.value ? visibleEvents.value.length : count.value))

/** 横轴：请求区间内的每一天，再并上公告实际落在的日期；没有公告的日子计 0 条（真实的 0）。 */
const axisDays = computed<string[]>(() => {
  const set = new Set<string>()
  const cursor = new Date(`${start.value}T00:00:00`)
  const last = new Date(`${end.value}T00:00:00`)
  if (!Number.isNaN(cursor.getTime()) && !Number.isNaN(last.getTime())) {
    for (let guard = 0; cursor <= last && guard < 400; guard += 1) {
      set.add(localDay(cursor))
      cursor.setDate(cursor.getDate() + 1)
    }
  }
  for (const item of events.value) {
    if (item.day) {
      set.add(item.day)
    }
  }
  return [...set].sort()
})

function dailyCounts(list: EventRow[]): number[] {
  const tally = new Map<string, number>()
  for (const item of list) {
    tally.set(item.day, (tally.get(item.day) ?? 0) + 1)
  }
  return axisDays.value.map((day) => tally.get(day) ?? 0)
}

const visibleDaily = computed<number[]>(() => dailyCounts(visibleEvents.value))

const peak = computed(() => {
  let best: { day: string; value: number } | null = null
  visibleDaily.value.forEach((value, index) => {
    if (value > 0 && (best === null || value > best.value)) {
      best = { day: axisDays.value[index] ?? "", value }
    }
  })
  return best as { day: string; value: number } | null
})

const activeDays = computed<number>(() => visibleDaily.value.filter((value) => value > 0).length)

/** 主题排行：条数来自 topics，代表公告与最近日期来自同类 events（已按发布时间倒序）。 */
const topicRows = computed(() => {
  const total = count.value || 1
  return visibleTopics.value.map((item, index) => {
    const related = events.value.filter((row) => row.eventType === item.eventType)
    const latestEvent = related[0] ?? null
    return {
      ...item,
      rank: index + 1,
      share: (item.count / total) * 100,
      latestDay: latestEvent?.day ?? "",
      latestEvent,
      spark: dailyCounts(related),
      color: TOPIC_COLORS[topics.value.findIndex((row) => row.eventType === item.eventType) % TOPIC_COLORS.length],
    }
  })
})

const recentEvents = computed<EventRow[]>(() =>
  showAll.value ? visibleEvents.value : visibleEvents.value.slice(0, RECENT_LIMIT),
)

/** 公告最多的公司：只认「简称：」开头的标题（巨潮 / SEC 的标准写法）。 */
const companyRows = computed(() => {
  const tally = new Map<string, { name: string; count: number; latest: EventRow }>()
  for (const item of visibleEvents.value) {
    const matched = /^([\u4e00-\u9fa5A-Za-z0-9 ]{2,12})[：:]/.exec(item.title)
    const name = matched?.[1]?.replace(/\s+/g, "")
    if (!name) {
      continue
    }
    const hit = tally.get(name)
    if (hit) {
      hit.count += 1
    } else {
      tally.set(name, { name, count: 1, latest: item })
    }
  }
  return [...tally.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN")).slice(0, 8)
})
const companyMax = computed<number>(() => companyRows.value[0]?.count ?? 1)
const attributedCount = computed<number>(() =>
  visibleEvents.value.filter((item) => /^[\u4e00-\u9fa5A-Za-z0-9 ]{2,12}[：:]/.test(item.title)).length,
)

const sourceRows = computed(() => {
  const tally = new Map<string, number>()
  for (const item of events.value) {
    const key = item.sourceBadge || "未标注来源"
    tally.set(key, (tally.get(key) ?? 0) + 1)
  }
  const listed = sources.value.map((name) => ({ name, count: tally.get(name) ?? 0 }))
  for (const [name, value] of tally) {
    if (!sources.value.includes(name)) {
      listed.push({ name, count: value })
    }
  }
  return listed.sort((a, b) => b.count - a.count)
})

function shortDay(day: string): string {
  return day.length >= 10 ? day.slice(5) : day
}

const trendOption = computed<EChartsOption>(() => {
  const list = topic.value ? visibleTopics.value : topics.value
  return {
    animation: false,
    color: TOPIC_COLORS,
    legend: { top: 0, left: 0 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 36, right: 12, top: 36, bottom: 28 },
    xAxis: {
      type: "category",
      data: axisDays.value.map(shortDay),
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: "value", minInterval: 1 },
    series: list.map((item) => ({
      name: item.label,
      type: "bar" as const,
      stack: "total",
      barMaxWidth: 22,
      itemStyle: {
        color: TOPIC_COLORS[topics.value.findIndex((row) => row.eventType === item.eventType) % TOPIC_COLORS.length],
      },
      emphasis: { focus: "series" as const },
      data: dailyCounts(events.value.filter((row) => row.eventType === item.eventType)),
    })),
  }
})

function parseTopic(value: unknown): TopicRow[] {
  const row = asRecord(value)
  const eventType = asString(row?.event_type)
  if (!eventType) {
    return []
  }
  return [
    {
      eventType,
      label: asString(row?.label) ?? "其他公告",
      count: typeof row?.count === "number" ? row.count : 0,
    },
  ]
}

function parseEvent(value: unknown): EventRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  const title = asString(row?.title)
  if (!id || !title) {
    return []
  }
  const publishedAt = asString(row?.published_at) ?? ""
  const moment = new Date(publishedAt)
  const valid = !Number.isNaN(moment.getTime())
  const midnight = valid && moment.getHours() === 0 && moment.getMinutes() === 0
  return [
    {
      id,
      title: title.trim(),
      url: asString(row?.canonical_url) ?? "",
      sourceBadge: asString(row?.source_badge) ?? asString(row?.source_name) ?? "",
      eventType: asString(row?.event_type) ?? "",
      eventTypeLabel: asString(row?.event_type_label) ?? "公告",
      publishedAt,
      day: valid ? localDay(moment) : "",
      time: valid && !midnight ? `${pad(moment.getHours())}:${pad(moment.getMinutes())}` : "",
    },
  ]
}

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "热点数据加载失败"
}

async function loadHotspots(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  showAll.value = false
  const params = new URLSearchParams({ start: start.value, end: end.value })
  if (industryId.value) {
    params.set("industry_id", industryId.value)
  }
  try {
    const response = await fetch(apiUrl(`/api/v1/market/hotspots?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      failure.value = serverMessage(body)
      phase.value = "error"
      return
    }
    const data = asRecord(asRecord(body)?.data)
    count.value = typeof data?.count === "number" ? data.count : 0
    notice.value = asString(data?.notice) ?? ""
    sources.value = asList(data?.sources).flatMap((item) => {
      const text = asString(item)
      return text ? [text] : []
    })
    pendingFeeds.value = asList(data?.pending_feeds).flatMap((item) => {
      const text = asString(item)
      return text ? [text] : []
    })
    events.value = asList(data?.events).flatMap(parseEvent)
    topics.value = asList(data?.topics).flatMap(parseTopic)
    if (topic.value && !topics.value.some((item) => item.eventType === topic.value)) {
      topic.value = ""
    }
    phase.value = "ready"
  } catch {
    failure.value = "无法连接热点服务，请稍后重试"
    phase.value = "error"
  }
}

async function loadIndustries(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/industries?q="))
    const body = await readJson(response)
    if (!response.ok) {
      return
    }
    industries.value = asList(asRecord(asRecord(body)?.data)?.industries).flatMap((item) => {
      const row = asRecord(item)
      const id = asString(row?.id)
      const name = asString(row?.name)
      return id && name ? [{ id, name }] : []
    })
  } catch {
    industries.value = []
  }
}

const emptyMessage = computed(() => {
  if (sourceDisabled.value) {
    return "公告数据源尚未开启，需管理员在数据中心开启并导入公告"
  }
  if (industryId.value) {
    return `${start.value} 至 ${end.value} 暂无关联「${industryName.value || "该行业"}」的公告，可切换行业或放宽日期`
  }
  return `${start.value} 至 ${end.value} 暂无合规公告，可放宽日期范围后重试`
})

function refresh(): void {
  void loadHotspots()
}

watch([windowRange, industryId], () => {
  void loadHotspots()
})

onMounted(() => {
  void loadIndustries()
  void loadHotspots()
})
</script>

<template>
  <main class="page">
    <PageHeader title="市场热点追踪" subtitle="按交易所与 SEC 合规公告聚合事件热度，每条都可追溯原文">
      <template #actions>
        <el-date-picker
          v-model="windowRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="~"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          :clearable="false"
          style="width: 250px"
        />
        <el-select v-model="topic" class="filter-select" placeholder="全部主题" clearable>
          <el-option label="全部主题" value="" />
          <el-option v-for="item in topicOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="industryId" class="filter-select" placeholder="全部行业" clearable>
          <el-option label="全部行业" value="" />
          <el-option v-for="item in industries" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-button :loading="phase === 'loading'" @click="refresh">刷新</el-button>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="8" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="loadHotspots" />
    <StateBlock v-else-if="!count" state="empty" title="暂无热点公告" :message="emptyMessage" />

    <template v-else>
      <div class="grid-4">
        <StatCard
          title="公告总数"
          :value="visibleTotal"
          unit="条"
          :caption="`${start} ~ ${end}`"
          tone="accent"
        >
          <template #icon><el-icon><Document /></el-icon></template>
          <template #chart>
            <MiniSpark :points="visibleDaily" kind="area" :width="120" :height="42" />
          </template>
        </StatCard>
        <StatCard
          title="最热事件类型"
          :value="visibleTopics[0]?.label ?? null"
          :caption="topicRows[0] ? `${topicRows[0].count} 条 · 占全部 ${topicRows[0].share.toFixed(0)}%` : '暂无'"
          tone="orange"
        >
          <template #icon><el-icon><TrendCharts /></el-icon></template>
          <template #chart>
            <MiniSpark v-if="topics.length > 1" :points="topics.map((item) => item.count)" kind="bar" tone="warn" :width="100" :height="42" />
          </template>
        </StatCard>
        <StatCard
          title="公告最多的公司"
          :value="companyRows[0]?.name ?? null"
          :caption="companyRows[0] ? `${companyRows[0].count} 条公告` : '标题中未识别到公司简称'"
          tone="violet"
        >
          <template #icon><el-icon><OfficeBuilding /></el-icon></template>
        </StatCard>
        <StatCard
          title="单日峰值"
          :value="peak?.value ?? null"
          unit="条"
          :caption="peak ? `${peak.day} · 区间内 ${activeDays} 天有公告` : '区间内无公告'"
          tone="cyan"
        >
          <template #icon><el-icon><Calendar /></el-icon></template>
          <template #chart>
            <MiniSpark :points="visibleDaily" kind="bar" tone="accent" :width="110" :height="42" />
          </template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <div class="stack">
          <PanelCard title="热点主题排行" info="按公告条数排序，热度不使用媒体情绪；代表公告取该类最新一条" flush>
            <el-table :data="topicRows" size="small" row-key="eventType">
              <el-table-column label="排名" width="64" align="center">
                <template #default="{ row }">
                  <span class="rank" :class="`rank-${Math.min(row.rank, 4)}`">{{ row.rank }}</span>
                </template>
              </el-table-column>
              <el-table-column label="主题" width="120">
                <template #default="{ row }">
                  <span class="topic-name"><i :style="{ background: row.color }" />{{ row.label }}</span>
                </template>
              </el-table-column>
              <el-table-column label="公告数" width="150">
                <template #default="{ row }">
                  <div class="share-cell">
                    <strong class="tabular">{{ row.count }}</strong>
                    <span class="share-track"><i :style="{ width: `${row.share}%`, background: row.color }" /></span>
                    <span class="tabular muted-num">{{ row.share.toFixed(0) }}%</span>
                  </div>
                </template>
              </el-table-column>
              <el-table-column label="最近日期" width="100">
                <template #default="{ row }">
                  <span class="tabular">{{ row.latestDay ? shortDay(row.latestDay) : "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="代表公告" min-width="260">
                <template #default="{ row }">
                  <a
                    v-if="row.latestEvent?.url"
                    class="title-link"
                    :href="row.latestEvent.url"
                    target="_blank"
                    rel="noopener noreferrer"
                    :title="row.latestEvent.title"
                  >{{ row.latestEvent.title }}</a>
                  <span v-else-if="row.latestEvent" class="title-text">{{ row.latestEvent.title }}</span>
                  <span v-else class="muted-num">—</span>
                </template>
              </el-table-column>
              <el-table-column label="每日分布" width="130" align="center">
                <template #default="{ row }">
                  <MiniSpark class="inline-spark" :points="row.spark" kind="bar" :width="104" :height="26" />
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>

          <PanelCard title="主题热度趋势" info="按公告发布日（北京时间）统计，各类事件堆叠显示">
            <template #icon><el-icon><Histogram /></el-icon></template>
            <VChart class="chart" :option="trendOption" autoresize />
          </PanelCard>

          <PanelCard
            title="公告最多的公司"
            :info="`按标题中的公司简称统计（${attributedCount}/${visibleEvents.length} 条可识别），未带简称的公告不计入`"
            tone="violet"
          >
            <template #icon><el-icon><OfficeBuilding /></el-icon></template>
            <p v-if="!companyRows.length" class="muted">公告标题中未识别到公司简称。</p>
            <ul v-else class="company-list">
              <li v-for="item in companyRows" :key="item.name">
                <span class="company-name">{{ item.name }}</span>
                <span class="company-bar"><i :style="{ width: `${(item.count / companyMax) * 100}%` }" /></span>
                <span class="tabular company-count">{{ item.count }} 条</span>
                <a
                  v-if="item.latest.url"
                  class="company-latest"
                  :href="item.latest.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="item.latest.title"
                >最新：{{ item.latest.title.replace(/^[^：:]+[：:]/, "") }}</a>
              </li>
            </ul>
          </PanelCard>
        </div>

        <div class="stack">
          <PanelCard
            title="最近公告"
            :info="`共 ${visibleEvents.length} 条，点击标题打开原文`"
            tone="cyan"
          >
            <template #icon><el-icon><Link /></el-icon></template>
            <template #actions>
              <button
                v-if="visibleEvents.length > RECENT_LIMIT"
                type="button"
                class="text-toggle"
                @click="showAll = !showAll"
              >
                {{ showAll ? "收起" : `全部 ${visibleEvents.length} 条` }}
              </button>
            </template>
            <ol class="timeline" :class="{ expanded: showAll }">
              <li v-for="item in recentEvents" :key="item.id">
                <span class="tl-when tabular">
                  <span>{{ shortDay(item.day) }}</span>
                  <small v-if="item.time">{{ item.time }}</small>
                </span>
                <div class="tl-body">
                  <a
                    v-if="item.url"
                    class="tl-title"
                    :href="item.url"
                    target="_blank"
                    rel="noopener noreferrer"
                  >{{ item.title }}</a>
                  <span v-else class="tl-title">{{ item.title }}</span>
                  <p class="tl-meta">
                    <StatusPill :text="item.eventTypeLabel" tone="accent" />
                    <span>{{ item.sourceBadge }}</span>
                  </p>
                </div>
              </li>
            </ol>
          </PanelCard>

          <PanelCard title="数据来源" tone="orange">
            <ul class="source-list">
              <li v-for="item in sourceRows" :key="item.name">
                <StatusPill :text="item.name" tone="ok" dot />
                <span class="tabular">{{ item.count }} 条</span>
              </li>
              <li v-for="item in pendingFeeds" :key="item">
                <StatusPill :text="item" tone="muted" dot />
                <span class="muted-num">未接入</span>
              </li>
            </ul>
            <p class="source-note">
              热度只按公告条数计算<template v-if="pendingFeeds.length">，暂不含新闻快讯与市场舆情</template>。
            </p>
          </PanelCard>
        </div>
      </div>

      <SourceFooter
        :as-of="`${start} ~ ${end}`"
        :sources="sources.length ? sources : ['合规公告']"
        note="热度只按公告条数计算，不做情绪打分"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.filter-select {
  width: 150px;
}

.rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-family: var(--font-num);
  font-size: 12px;
  font-weight: 600;
  background: var(--panel-soft);
  color: var(--muted);
}

.rank-1 {
  background: rgb(246 70 93 / 18%);
  color: var(--rise);
}

.rank-2 {
  background: rgb(251 146 60 / 18%);
  color: var(--orange);
}

.rank-3 {
  background: var(--accent-soft);
  color: var(--accent);
}

.topic-name {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-strong);
}

.topic-name i {
  width: 8px;
  height: 8px;
  border-radius: 2px;
}

.share-cell {
  display: grid;
  grid-template-columns: 26px 1fr 34px;
  align-items: center;
  gap: 8px;
}

.share-cell strong {
  color: var(--text-strong);
}

.share-track {
  display: block;
  height: 6px;
  border-radius: 999px;
  background: rgb(96 140 220 / 12%);
  overflow: hidden;
}

.share-track i {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.muted-num {
  color: var(--muted);
  font-size: 12px;
}

.title-link,
.title-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
  text-decoration: none;
}

.title-link:hover {
  color: var(--accent);
  text-decoration: underline;
}

.inline-spark {
  display: inline-block;
}

.chart {
  width: 100%;
  height: 300px;
}

.text-toggle {
  appearance: none;
  border: 0;
  background: none;
  padding: 0;
  color: var(--accent);
  font-size: 13px;
  cursor: pointer;
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  max-height: 640px;
  overflow: auto;
}

.timeline.expanded {
  max-height: 960px;
}

.timeline li {
  position: relative;
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  gap: 14px;
  padding: 0 4px 14px 0;
}

.timeline li::before {
  content: "";
  position: absolute;
  left: 61px;
  top: 6px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.timeline li::after {
  content: "";
  position: absolute;
  left: 64px;
  top: 16px;
  bottom: 0;
  width: 1px;
  background: var(--line);
}

.timeline li:last-child::after {
  display: none;
}

.tl-when {
  display: grid;
  font-size: 13px;
  color: var(--text);
  line-height: 1.4;
}

.tl-when small {
  font-size: 12px;
  color: var(--muted);
}

.tl-body {
  padding-left: 14px;
  min-width: 0;
}

.tl-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-strong);
  text-decoration: none;
}

a.tl-title:hover {
  color: var(--accent);
}

.tl-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
}

.company-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 32px;
}

.company-list li {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr) 44px;
  align-items: center;
  gap: 4px 10px;
  font-size: 13px;
}

.company-name {
  font-weight: 600;
  color: var(--text-strong);
}

.company-bar {
  display: block;
  height: 6px;
  border-radius: 999px;
  background: rgb(96 140 220 / 10%);
  overflow: hidden;
}

.company-bar i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, rgb(167 139 250 / 40%), var(--violet));
}

.company-count {
  text-align: right;
  color: var(--text);
}

.company-latest {
  grid-column: 2 / 4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
}

.company-latest:hover {
  color: var(--accent);
}

.source-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.source-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.source-note {
  margin: 12px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
  font-size: 13px;
  color: var(--muted);
}
</style>
