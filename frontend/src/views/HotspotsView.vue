<script setup lang="ts">
/**
 * 市场热点追踪（/hotspots）。
 *
 * 数据来源（全部是后端真实接口，前端不做任何热度打分）：
 *  - GET /api/v1/market/hotspots?start=&end=&industry_id=  合规公告聚合：主题分布、按日时间线、事件明细
 *  - GET /api/v1/industries                                行业下拉的选项
 *  - GET /api/v1/market/sectors?as_of=                     板块强弱（后端算好的分数与覆盖度）
 *
 * 重要现实：在没有导入合规公告之前，/market/hotspots 的 count 恒为 0，
 * 返回的 topics/timeline/events/sources 都是空数组，notice 会写明原因。
 * 页面必须把这个原因原样显示出来，绝不用编造的题材名、公司名或涨幅填表。
 */
import { computed, onMounted, ref, watch } from "vue"
import { Histogram, Star, TrendCharts } from "@element-plus/icons-vue"
import { apiUrl, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
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
  sourceName: string
  sourceBadge: string
  eventType: string
  eventTypeLabel: string
  publishedAt: string
  industryId: string
  qualityFlag: string
}

interface IndustryOption {
  id: string
  name: string
}

interface SectorRow {
  name: string
  status: string
  label: string
  score: string
  coverage: string
  notice: string
}

const DEFAULT_DAYS = 14

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
}

const phase = ref<Phase>("loading")
const failure = ref("")

const windowRange = ref<[string, string]>([isoDaysAgo(DEFAULT_DAYS - 1), isoDaysAgo(0)])
const topic = ref("")
const industryId = ref("")
const tab = ref("排名")

const label = ref("")
const count = ref(0)
const notice = ref("")
const stream = ref("")
const sources = ref<string[]>([])
const pendingFeeds = ref<string[]>([])
const topics = ref<TopicRow[]>([])
const events = ref<EventRow[]>([])
const days = ref<string[]>([])

const industries = ref<IndustryOption[]>([])
const sectors = ref<SectorRow[]>([])
const sectorsError = ref("")

const start = computed<string>(() => windowRange.value[0] ?? isoDaysAgo(DEFAULT_DAYS - 1))
const end = computed<string>(() => windowRange.value[1] ?? isoDaysAgo(0))
const asOf = computed<string>(() => `${start.value} ~ ${end.value}`)

const tabs = computed(() => [
  { key: "排名", label: `热点主题排行${topics.value.length ? ` ${topics.value.length}` : ""}` },
  { key: "趋势", label: "主题热度趋势" },
  { key: "事件", label: `相关新闻/事件${events.value.length ? ` ${events.value.length}` : ""}` },
  { key: "板块", label: "板块强弱" },
])

/** 主题下拉：选项就是接口真实返回的事件类型，不预设题材名。 */
const topicOptions = computed(() =>
  topics.value.map((item) => ({ value: item.eventType, label: `${item.label}（${item.count} 条）` })),
)

const visibleTopics = computed<TopicRow[]>(() =>
  topic.value ? topics.value.filter((item) => item.eventType === topic.value) : topics.value,
)

const visibleEvents = computed<EventRow[]>(() =>
  topic.value ? events.value.filter((item) => item.eventType === topic.value) : events.value,
)

/** 事件按天聚合；日期直接取接口的 published_at。 */
const eventDays = computed(() => {
  const buckets = new Map<string, EventRow[]>()
  for (const row of visibleEvents.value) {
    const day = row.publishedAt.slice(0, 10) || "—"
    const list = buckets.get(day)
    if (list) {
      list.push(row)
    } else {
      buckets.set(day, [row])
    }
  }
  return [...buckets.entries()]
    .sort((left, right) => right[0].localeCompare(left[0]))
    .map(([day, items]) => ({ day, items }))
})

/** 时间线的横轴：取接口返回的每日计数，缺失的日期不补值。 */
const dayCounts = computed<number[]>(() =>
  days.value.map((day) => events.value.filter((item) => item.publishedAt.slice(0, 10) === day).length),
)

/**
 * 「热度分数」在效果图里是一个 0–100 的评分。后端明确只用条数排序、
 * 不使用媒体情绪（见 disclosure_events.heat_label），因此这里展示条数而不是分数。
 */
const totalEvents = computed<number>(() => events.value.length)

/** 主题占比：按条数归一化，用来画持续性评估里的真实分段条。 */
const shares = computed(() =>
  topics.value.slice(0, 5).map((item, index) => ({
    label: item.label,
    count: item.count,
    percent: totalEvents.value ? Math.round((item.count / totalEvents.value) * 100) : 0,
    tone: (["accent", "ok", "warn", "danger", "muted"] as const)[index] ?? "muted",
  })),
)

/** 持续性：只描述数据分布，不给出投资结论。 */
const persistence = computed<{ text: string; tone: "ok" | "warn" | "danger" | "muted"; gauge: number }>(() => {
  if (!totalEvents.value) {
    return { text: "暂不评估", tone: "muted", gauge: 0 }
  }
  const top = shares.value[0]
  const percent = top?.percent ?? 0
  if (percent >= 60) {
    return { text: "偏强", tone: "ok", gauge: percent }
  }
  if (percent >= 30) {
    return { text: "中性", tone: "warn", gauge: percent }
  }
  return { text: "偏弱", tone: "danger", gauge: percent }
})

/** 风险提示：口径是「配置的外部源里还有多少没有接入」，这是可核对的事实。 */
const feedRisk = computed(() => {
  const pending = pendingFeeds.value.length
  const ready = sources.value.length
  const total = pending + ready
  const ratio = total ? Math.round((pending / total) * 100) : 0
  const tone: "ok" | "warn" | "danger" | "muted" = !total
    ? "muted"
    : ratio >= 70
      ? "danger"
      : ratio >= 40
        ? "warn"
        : "ok"
  const text = !total ? "暂无信息" : ratio >= 70 ? "高" : ratio >= 40 ? "中高" : "中"
  return { pending, ready, ratio, tone, text }
})

const trendOption = computed(() => ({
  grid: { left: 48, right: 24, top: 24, bottom: 34 },
  tooltip: { trigger: "axis" },
  xAxis: {
    type: "category",
    data: days.value,
    axisLabel: { color: "#95a6be", fontSize: 11 },
    axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
  },
  yAxis: {
    type: "value",
    minInterval: 1,
    name: "公告条数",
    nameTextStyle: { color: "#95a6be", fontSize: 11 },
    axisLabel: { color: "#95a6be", fontSize: 11 },
    splitLine: { lineStyle: { color: "rgba(149,166,190,0.12)" } },
  },
  series: [
    {
      name: "合规公告条数",
      type: "line",
      smooth: true,
      showSymbol: true,
      symbolSize: 5,
      areaStyle: { opacity: 0.18 },
      data: dayCounts.value,
    },
  ],
}))

function parseTopic(value: unknown): TopicRow[] {
  const row = asRecord(value)
  const eventType = asString(row?.event_type)
  if (!eventType) {
    return []
  }
  return [
    {
      eventType,
      label: asString(row?.label) ?? eventType,
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
  return [
    {
      id,
      title,
      url: asString(row?.canonical_url) ?? "",
      sourceName: asString(row?.source_name) ?? "",
      sourceBadge: asString(row?.source_badge) ?? "",
      eventType: asString(row?.event_type) ?? "",
      eventTypeLabel: asString(row?.event_type_label) ?? asString(row?.event_type) ?? "",
      publishedAt: asString(row?.published_at) ?? "",
      industryId: asString(row?.industry_id) ?? "",
      qualityFlag: asString(row?.quality_flag) ?? "",
    },
  ]
}

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "请求未被接受"
}

/** 事件的「热度趋势」迷你图：按天统计该事件类型的条数，只有真实计数。 */
function sparkFor(row: TopicRow): number[] {
  return days.value.map(
    (day) =>
      events.value.filter(
        (item) => item.eventType === row.eventType && item.publishedAt.slice(0, 10) === day,
      ).length,
  )
}

async function loadHotspots(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
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
    label.value = asString(data?.label) ?? ""
    count.value = typeof data?.count === "number" ? data.count : 0
    notice.value = asString(data?.notice) ?? ""
    stream.value = asString(data?.stream) ?? ""
    sources.value = asList(data?.sources).flatMap((item) => {
      const text = asString(item)
      return text ? [text] : []
    })
    pendingFeeds.value = asList(data?.pending_feeds).flatMap((item) => {
      const text = asString(item)
      return text ? [text] : []
    })
    events.value = asList(data?.events).flatMap(parseEvent)
    days.value = asList(data?.timeline).flatMap((item) => {
      const day = asString(asRecord(item)?.day)
      return day ? [day] : []
    })
    topics.value = asList(data?.topics).flatMap(parseTopic)
    if (!days.value.length) {
      days.value = daysBetween(start.value, end.value)
    }
    phase.value = "ready"
  } catch {
    failure.value = "无法连接接口"
    phase.value = "error"
  }
}

/** 时间线为空时，横轴用请求区间生成日期刻度：只是坐标，不代表有数据。 */
function daysBetween(from: string, to: string): string[] {
  const result: string[] = []
  const cursor = new Date(`${from}T00:00:00Z`).getTime()
  const last = new Date(`${to}T00:00:00Z`).getTime()
  if (!Number.isFinite(cursor) || !Number.isFinite(last) || cursor > last) {
    return result
  }
  for (let time = cursor; time <= last; time += 24 * 60 * 60 * 1000) {
    result.push(new Date(time).toISOString().slice(0, 10))
  }
  return result
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

async function loadSectors(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/market/sectors"))
    const body = await readJson(response)
    if (!response.ok) {
      sectors.value = []
      sectorsError.value = serverMessage(body)
      return
    }
    sectors.value = asList(asRecord(asRecord(body)?.data)?.sectors).flatMap((item) => {
      const row = asRecord(item)
      const name = asString(row?.name)
      if (!name) {
        return []
      }
      return [
        {
          name,
          status: asString(row?.status) ?? "",
          label: asString(row?.label) ?? "",
          score: asString(row?.score) ?? "",
          coverage: asString(row?.coverage) ?? "",
          notice: asString(row?.notice) ?? "",
        },
      ]
    })
    sectorsError.value = ""
  } catch {
    sectors.value = []
    sectorsError.value = "无法连接接口"
  }
}

function statusTone(status: string): "ok" | "warn" | "danger" | "muted" | "accent" {
  if (status === "ok" || status === "ready") {
    return "ok"
  }
  if (status === "partial" || status === "stale") {
    return "warn"
  }
  if (status === "unavailable" || status === "missing") {
    return "muted"
  }
  return status ? "accent" : "muted"
}

function refresh(): void {
  void loadHotspots()
}

watch([windowRange, industryId], () => {
  void loadHotspots()
})

onMounted(() => {
  void loadIndustries()
  void loadSectors()
  void loadHotspots()
})
</script>

<template>
  <main class="page">
    <PageHeader title="市场热点追踪" subtitle="实时追踪市场热点主题，洞察资金动向，把握投资机会">
      <template #actions>
        <el-date-picker
          v-model="windowRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="~"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          :clearable="false"
        />
        <el-select v-model="topic" class="filter-select" placeholder="全部主题" clearable>
          <el-option label="全部主题" value="" />
          <el-option
            v-for="item in topicOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
        <el-select v-model="industryId" class="filter-select" placeholder="全部行业" clearable>
          <el-option label="全部行业" value="" />
          <el-option
            v-for="item in industries"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-button :loading="phase === 'loading'" @click="refresh">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs v-model="tab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="loadHotspots" />

    <template v-else>
      <div class="grid-3 section">
        <PanelCard
          title="驱动因素"
          info="GET /api/v1/market/hotspots 的 topics：只按公告条数聚合，后端不使用媒体情绪打分"
        >
          <p class="lead">
            <template v-if="count">区间内共 {{ count }} 条合规公告，涉及 {{ topics.length }} 类事件。</template>
            <template v-else>区间内没有合规公告。</template>
          </p>
          <StateBlock v-if="!topics.length" state="empty" :message="notice || '后端未返回任何事件类型'" />
          <ol v-else class="point-list">
            <li v-for="item in topics.slice(0, 4)" :key="item.eventType">
              <StatusPill :text="item.label" tone="accent" />
              <span class="tabular">{{ item.count }} 条</span>
            </li>
          </ol>
          <p class="muted">
            效果图里的「受政策催化、景气度提升与资金流入共振」需要新闻情绪与资金流接口，
            后端没有这两类接口，因此这里只列真实事件类型。
          </p>
        </PanelCard>

        <PanelCard
          title="持续性评估"
          info="按各类事件的条数占比衡量，只用后端返回的计数，不做投资结论"
        >
          <div v-if="!count" class="gauge-block">
            <p class="gauge-label">暂不评估</p>
            <div class="gauge"><i style="width: 0%" /></div>
            <p class="muted">{{ notice || "没有合规公告，无法评估持续性" }}</p>
          </div>
          <div v-else class="gauge-block">
            <p class="gauge-label">
              {{ persistence.text }}
              <span class="tabular muted">{{ totalEvents }} 条</span>
            </p>
            <div class="gauge">
              <i :style="{ width: `${persistence.gauge}%` }" />
            </div>
            <ul class="share-list">
              <li v-for="item in shares" :key="item.label">
                <i class="share-dot" :class="`tone-${item.tone}`" aria-hidden="true" />
                <span class="share-label">{{ item.label }}</span>
                <span class="tabular muted">{{ item.count }} 条 · {{ item.percent }}%</span>
              </li>
            </ul>
          </div>
          <p class="muted">
            口径：条数最多的那类事件占全部公告的比例。比例越高说明事件越集中，不代表收益越高。
          </p>
        </PanelCard>

        <PanelCard
          title="风险提示"
          info="口径：已配置的外部源里还有多少没有接入，来自接口的 pending_feeds 与 sources"
        >
          <div class="gauge-block">
            <p class="gauge-label">
              {{ feedRisk.text }}
              <span class="tabular muted">{{ feedRisk.pending }} / {{ feedRisk.pending + feedRisk.ready }}</span>
            </p>
            <div class="gauge" :class="`tone-${feedRisk.tone}`">
              <i :style="{ width: `${feedRisk.ratio}%` }" />
            </div>
          </div>
          <p class="muted">仍未接入的来源（{{ feedRisk.pending }} 个）：</p>
          <ul class="feed-list">
            <li v-for="item in pendingFeeds" :key="item">
              <StatusPill :text="item" tone="muted" />
            </li>
            <li v-if="!pendingFeeds.length" class="muted">接口没有返回待接入来源</li>
          </ul>
          <p class="muted">
            已接入：{{ sources.length ? sources.join("、") : "暂无" }}。
            效果图里的「交易拥挤度、短期回调风险」需要成交与估值接口，后端没有，因此不填数字。
          </p>
        </PanelCard>
      </div>

      <div class="grid-2-1 section">
        <div class="stack">
          <PanelCard
            title="热点主题排行"
            info="排名按公告条数由多到少；「热度分数」按后端口径换成条数展示，不使用媒体情绪打分"
          >
            <StateBlock
              v-if="!visibleTopics.length"
              state="empty"
              :message="notice || '区间内没有可排行的合规公告事件'"
            />
            <template v-else>
              <el-table :data="visibleTopics" stripe>
                <el-table-column label="排名" width="72">
                  <template #default="{ $index }">
                    <span class="rank" :class="`rank-${Math.min($index + 1, 4)}`">
                      {{ $index + 1 }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="主题名称" min-width="160">
                  <template #default="{ row }">
                    <p class="cell-title">{{ row.label }}</p>
                    <p class="muted">{{ row.eventType }}</p>
                  </template>
                </el-table-column>
                <el-table-column label="热度分数" width="112">
                  <template #default="{ row }">
                    <StatusPill :text="`${row.count} 条`" tone="accent" />
                  </template>
                </el-table-column>
                <el-table-column label="1日涨幅" width="92">
                  <template #default>
                    <span class="muted">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="5日涨幅" width="92">
                  <template #default>
                    <span class="muted">—</span>
                  </template>
                </el-table-column>
                <el-table-column label="相关行业" min-width="150">
                  <template #default>
                    <span class="muted">接口未关联行业</span>
                  </template>
                </el-table-column>
                <el-table-column label="代表个股" min-width="150">
                  <template #default>
                    <span class="muted">接口未关联个股</span>
                  </template>
                </el-table-column>
                <el-table-column label="热度趋势" width="128">
                  <template #default="{ row }">
                    <MiniSpark :points="sparkFor(row)" kind="bar" :width="100" :height="30" />
                  </template>
                </el-table-column>
              </el-table>
              <p class="muted">
                「1日涨幅 / 5日涨幅」需要主题指数或成分股行情接口，当前热点接口不返回这两个字段，
                因此保留列位并显示「—」，不用其他数字顶替。
              </p>
            </template>
          </PanelCard>

          <PanelCard
            title="主题热度趋势"
            info="横轴为公告发布日，纵轴为当日条数；没有数据的日期不补值"
          >
            <div v-if="dayCounts.some((item) => item > 0)" class="chart-wrap">
              <VChart class="chart" :option="trendOption" autoresize />
            </div>
            <StateBlock
              v-else
              state="empty"
              :message="`${start} 至 ${end} 没有合规公告，因此没有可画的趋势。原因：${notice || '接口返回 0 条事件'}`"
            />
            <p class="muted">
              时间线刻度来自接口的 timeline；事件热度 {{ label || "—" }}；数据流 {{ stream || "—" }}。
            </p>
          </PanelCard>

          <PanelCard
            title="相关新闻 / 事件"
            info="GET /api/v1/market/hotspots 的 events：标题、来源徽标、公布时间与原文链接"
          >
            <StateBlock
              v-if="!eventDays.length"
              state="empty"
              :message="notice || '区间内没有合规公告事件'"
            />
            <ol v-else class="timeline">
              <li v-for="node in eventDays" :key="node.day">
                <span class="timeline-day tabular">{{ node.day }}</span>
                <div class="timeline-body">
                  <p v-for="item in node.items" :key="item.id" class="timeline-item">
                    <StatusPill :text="item.eventTypeLabel || '事件'" tone="accent" />
                    <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer">
                      {{ item.title }}
                    </a>
                    <span v-else>{{ item.title }}</span>
                    <em class="muted">
                      {{ item.sourceBadge || item.sourceName }}
                      · {{ item.publishedAt.slice(11, 16) }}
                      <template v-if="item.qualityFlag"> · 质量标记 {{ item.qualityFlag }}</template>
                    </em>
                  </p>
                </div>
              </li>
            </ol>
          </PanelCard>

          <PanelCard
            title="板块强弱"
            info="GET /api/v1/market/sectors：分数与覆盖度都由后端计算，前端不再二次计算"
          >
            <StateBlock v-if="sectorsError" state="error" :message="sectorsError" @retry="loadSectors" />
            <StateBlock v-else-if="!sectors.length" state="empty" message="后端未返回任何板块" />
            <el-table v-else :data="sectors" stripe>
              <el-table-column prop="name" label="板块" min-width="160" />
              <el-table-column label="标签" width="140">
                <template #default="{ row }">
                  <StatusPill :text="row.label || '未标注'" :tone="statusTone(row.status)" />
                </template>
              </el-table-column>
              <el-table-column label="分数" width="100">
                <template #default="{ row }">
                  <DeltaValue v-if="row.score" :value="row.score" :colored="false" size="sm" />
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="覆盖度" min-width="220">
                <template #default="{ row }">
                  <span class="tabular">{{ row.coverage || "—" }}</span>
                  <p v-if="row.notice" class="muted">{{ row.notice }}</p>
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>
        </div>

        <div class="stack">
          <PanelCard
            title="相关公司"
            info="热点接口不返回主题到个股的映射，因此这里如实说明，而不是列出无法追溯的公司名"
          >
            <StateBlock
              state="empty"
              message="接口没有主题成分股数据：/api/v1/market/hotspots 返回事件与来源，不返回个股；后端也没有主题成分股表。因此本卡不列代码、最新价与关联度。"
            />
            <p class="doc-text">
              可以核对的替代路径：用「板块强弱」里的板块名称，到自选股或公司研究页按标的读取
              <code>/api/v1/market/bars/{instrument_id}</code> 的真实日线。
            </p>
            <div class="link-row">
              <RouterLink to="/market/sectors">板块强弱</RouterLink>
              <RouterLink to="/watchlist">自选股</RouterLink>
              <RouterLink to="/industry">产业研究</RouterLink>
            </div>
          </PanelCard>

          <PanelCard
            title="AI 解读"
            info="规格书要求 AI 输出必须带可校验的 citations；热点接口目前没有解读字段"
          >
            <StateBlock
              state="empty"
              message="没有可解读的合规公告：解读文本必须由带 citations 校验的模型生成，当前接口不返回该字段，因此不生成占位结论。"
            />
          <p class="doc-text">
            <template v-if="count">当前可用的事实：区间内 {{ count }} 条公告，涉及 {{ topics.length }} 类事件。</template>
            <template v-else>当前可用的事实：区间内 0 条公告。</template>
          </p>
            <p class="muted">接入公告并生成解读后，这里会显示带原文链接的结论。</p>
            <el-button text disabled>重新生成</el-button>
            <p class="muted">按钮在解读接口就绪前保持不可用，避免出现空壳按钮。</p>
          </PanelCard>

          <PanelCard title="数据来源与状态" info="直接来自接口的 sources / pending_feeds / notice">
            <dl class="kv">
              <dt>统计区间</dt>
              <dd class="tabular">{{ start }} ~ {{ end }}</dd>
              <dt>公告条数</dt>
              <dd class="tabular">{{ count }}</dd>
              <dt>事件热度</dt>
              <dd>{{ label || "—" }}</dd>
              <dt>数据流</dt>
              <dd>{{ stream || "—" }}</dd>
              <dt>已接入来源</dt>
              <dd>{{ sources.length ? sources.join("、") : "暂无" }}</dd>
            </dl>
            <p v-if="notice" class="muted">{{ notice }}</p>
          </PanelCard>

          <PanelCard title="相关页面" info="热点页只覆盖公告聚合">
            <div class="link-row">
              <RouterLink to="/market/temperature">市场温度</RouterLink>
              <RouterLink to="/market/sectors">板块强弱</RouterLink>
              <RouterLink to="/reports">研究报告</RouterLink>
            </div>
          </PanelCard>
        </div>
      </div>

      <div class="grid-4 section">
        <StatCard
          title="公告条数"
          :value="count"
          unit="条"
          :caption="count ? `${start} ~ ${end}` : '无数据'"
          info="GET /api/v1/market/hotspots 的 count 字段，未导入合规公告时为 0"
        >
          <template #icon><Histogram /></template>
        </StatCard>
        <StatCard
          title="事件类型"
          :value="topics.length"
          unit="类"
          :caption="topics.length ? topics[0]?.label ?? '' : '无可聚合事件'"
          info="按 event_type 聚合的类别数，排序只用条数"
        >
          <template #icon><TrendCharts /></template>
          <template #chart>
            <MiniSpark
              :points="topics.map((item) => item.count)"
              kind="bar"
              :width="110"
              :height="38"
            />
          </template>
        </StatCard>
        <StatCard
          title="待接入来源"
          :value="pendingFeeds.length"
          unit="个"
          :caption="`已接入 ${sources.length} 个`"
          info="接口 pending_feeds 的长度，代表还没有接入的外部数据源数量"
        >
          <template #icon><Star /></template>
        </StatCard>
        <StatCard
          title="每日峰值"
          :value="dayCounts.length ? Math.max(...dayCounts) : null"
          unit="条"
          :caption="dayCounts.length ? `${days.length} 天窗口` : '无时间线'"
          info="时间线里的单日最大条数，空窗口时为「—」"
        >
          <template #icon><Histogram /></template>
          <template #chart>
            <MiniSpark :points="dayCounts" kind="area" :width="110" :height="38" />
          </template>
        </StatCard>
      </div>

      <SourceFooter
        :as-of="asOf"
        :sources="sources.length ? sources : ['尚无合规来源接入']"
        note="公告条数与事件分类都由后端聚合，前端不做热度或情绪打分"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.filter-select {
  width: 160px;
}

.lead {
  margin: 0 0 10px;
  font-size: 13px;
  line-height: 1.6;
}

.point-list {
  margin: 0 0 10px;
  padding-left: 18px;
  display: grid;
  gap: 8px;
}

.point-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.gauge-block {
  display: grid;
  gap: 8px;
}

.gauge-label {
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}

.gauge {
  display: block;
  height: 10px;
  border-radius: 999px;
  background: var(--panel-soft);
  overflow: hidden;
}

.gauge i {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.gauge.tone-ok i {
  background: var(--ok);
}

.gauge.tone-warn i {
  background: var(--warn);
}

.gauge.tone-danger i {
  background: var(--danger);
}

.gauge.tone-muted i {
  background: var(--muted);
}

.share-list,
.feed-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
}

.share-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.share-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}

.share-dot.tone-accent {
  background: var(--accent);
}

.share-dot.tone-ok {
  background: var(--ok);
}

.share-dot.tone-warn {
  background: var(--warn);
}

.share-dot.tone-danger {
  background: var(--danger);
}

.share-dot.tone-muted {
  background: var(--muted);
}

.share-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feed-list {
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  margin-bottom: 10px;
}

.rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: var(--panel-soft);
  color: var(--muted);
}

.rank-1 {
  background: rgb(248 113 113 / 16%);
  color: var(--rise);
}

.rank-2 {
  background: rgb(251 191 36 / 16%);
  color: var(--warn);
}

.rank-3 {
  background: var(--accent-soft);
  color: var(--accent);
}

.chart {
  width: 100%;
  height: 260px;
}

.chart-wrap {
  margin-top: 8px;
}

.cell-title {
  margin: 0 0 2px;
  font-weight: 600;
}

.cell-title + .muted {
  margin: 0;
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 14px;
  max-height: 380px;
  overflow: auto;
}

.timeline li {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.timeline-day {
  font-size: 12px;
  color: var(--muted);
}

.timeline-body {
  border-left: 1px solid var(--line);
  padding-left: 12px;
  display: grid;
  gap: 8px;
}

.timeline-item {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.timeline-item em {
  font-style: normal;
  font-size: 12px;
}

.kv {
  margin: 0 0 10px;
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 6px 10px;
  font-size: 13px;
}

.kv dt {
  color: var(--muted);
}

.kv dd {
  margin: 0;
}

.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
}

.doc-text code {
  font-size: 12px;
}
</style>
