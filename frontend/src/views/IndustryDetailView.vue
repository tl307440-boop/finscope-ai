<script setup lang="ts">
/**
 * 产业研究 · 逻辑追踪详情（/industry/:id）。
 *
 * 数据来源（与原实现一致的接口，未新增后端依赖）：
 *  - GET  /api/v1/industries?q=                     行业名称与分类说明
 *  - GET  /api/v1/industries/{id}/facts?as_of=      该产业的已入库事实（含申报日与原文链接）
 *  - GET  /api/v1/industries/{id}/theses?as_of=     当前账号的研究假设与已附依据（需登录）
 *  - POST /api/v1/industries/{id}/theses            新增假设
 *  - POST /api/v1/theses/{id}/evidence              把事实附到假设上
 *  - POST /api/v1/theses/{id}/assessments           按截止日核对假设状态
 *
 * 不编造数据：需求端/供给端指标、景气度分数、机构关注度、时间线里的利空利好分类，
 * 后端都没有对应接口。这些位置如实显示空态与原因，只渲染接口真实返回的行。
 */
import { computed, onMounted, ref, watch } from "vue"
import { Histogram, Star, TrendCharts } from "@element-plus/icons-vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniRing from "../components/ui/MiniRing.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"

interface FactRow {
  id: string
  quote: string
  filed: string
  sourceUrl: string
}

interface EvidenceRow {
  id: string
  stance: string
  quote: string
  publishedAt: string
  sourceUrl: string
}

interface ThesisRow {
  id: string
  title: string
  hypothesis: string
  supportCondition: string
  challengeCondition: string
  status: string
  rationale: string
  evidence: EvidenceRow[]
}

const props = defineProps<{ id: string }>()

const session = useSessionStore()
const asOf = ref(new Date().toISOString().slice(0, 10))
const phase = ref<Phase>("loading")
const failure = ref("")
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)

const industryName = ref("")
const industryCode = ref("")
const industryDescription = ref("")
const facts = ref<FactRow[]>([])
const factsError = ref("")
const theses = ref<ThesisRow[]>([])
const thesesLoaded = ref(false)
const thesesDenied = ref(false)

const tab = ref("逻辑追踪")
const range = ref("1y")
const RANGES: { key: string; label: string; days: number }[] = [
  { key: "1y", label: "近1年", days: 365 },
  { key: "3y", label: "近3年", days: 1095 },
  { key: "5y", label: "近5年", days: 1825 },
]

const signedIn = computed<boolean>(() => session.authenticated)
const loginHref = computed<string>(
  () => `/login?redirect=${encodeURIComponent(`/industry/${props.id}`)}`,
)
const tabs = computed(() => [
  { key: "总览", label: "总览", to: "/industry" },
  { key: "逻辑追踪", label: "逻辑追踪" },
  { key: "景气跟踪", label: "景气跟踪" },
  { key: "相关主题", label: "相关主题" },
])

/** 事实按申报月份聚合，月份直接取接口返回的 filed，不做插值补齐。 */
const factsByMonth = computed(() => {
  const buckets = new Map<string, number>()
  for (const fact of facts.value) {
    const key = fact.filed.slice(0, 7)
    if (key.length === 7) {
      buckets.set(key, (buckets.get(key) ?? 0) + 1)
    }
  }
  return [...buckets.entries()].sort((left, right) => left[0].localeCompare(right[0]))
})

const rangeStart = computed<string>(() => {
  const option = RANGES.find((item) => item.key === range.value) ?? RANGES[0]
  const days = option?.days ?? 365
  const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000)
  return start.toISOString().slice(0, 10)
})

const rangedFacts = computed<FactRow[]>(() =>
  facts.value.filter((fact) => fact.filed >= rangeStart.value),
)

const visibleMonths = computed(() => {
  const buckets = new Map<string, number>()
  for (const fact of rangedFacts.value) {
    const key = fact.filed.slice(0, 7)
    if (key.length === 7) {
      buckets.set(key, (buckets.get(key) ?? 0) + 1)
    }
  }
  return [...buckets.entries()].sort((left, right) => left[0].localeCompare(right[0]))
})

const monthLabels = computed<string[]>(() => visibleMonths.value.map((item) => item[0]))

const monthlyCounts = computed<number[]>(() => visibleMonths.value.map((item) => item[1]))

/** 累计条数：只累加接口返回的行数，不构造任何指数或估值。 */
const cumulativeCounts = computed<number[]>(() => {
  let running = 0
  return monthlyCounts.value.map((count) => {
    running += count
    return running
  })
})

const firstFiled = computed<string>(() => sortedFiled.value[0] ?? "—")
const lastFiled = computed<string>(() => sortedFiled.value[sortedFiled.value.length - 1] ?? "—")

const sortedFiled = computed<string[]>(() =>
  facts.value
    .map((fact) => fact.filed)
    .filter((item) => item !== "—")
    .sort(),
)

/** 事实条数的迷你走势，给 StatCard 用。 */
const sparkPoints = computed<number[]>(() => monthlyCounts.value)

/** 来源域名：只做 URL 解析，不推算任何金融数字。 */
const domains = computed<string[]>(() => {
  const found = new Set<string>()
  for (const fact of facts.value) {
    const host = hostOf(fact.sourceUrl)
    if (host) {
      found.add(host)
    }
  }
  return [...found].sort()
})

const sourceSegments = computed(() =>
  domains.value.slice(0, 6).map((host) => ({
    label: host,
    ratio: facts.value.filter((fact) => hostOf(fact.sourceUrl) === host).length,
    tone: "accent" as const,
  })),
)

const evidenceRows = computed(() =>
  theses.value.flatMap((thesis) =>
    thesis.evidence.map((item, index) => ({
      key: `${thesis.id}-${index}`,
      thesisTitle: thesis.title,
      stance: item.stance,
      quote: item.quote,
      publishedAt: item.publishedAt,
      sourceUrl: item.sourceUrl,
    })),
  ),
)

/** 事件时间线：同一天合并成一个节点，日期取接口的 published_at。 */
const eventDays = computed(() => {
  const buckets = new Map<string, typeof evidenceRows.value>()
  for (const row of evidenceRows.value) {
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

const statusSegments = computed(() => {
  const known: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
    has_support: { label: "有支持证据", tone: "ok" },
    disputed: { label: "正反并存", tone: "warn" },
    has_challenge: { label: "有反证", tone: "danger" },
    open: { label: "证据不足", tone: "muted" },
  }
  const counts = new Map<string, number>()
  for (const thesis of theses.value) {
    counts.set(thesis.status, (counts.get(thesis.status) ?? 0) + 1)
  }
  return [...counts.entries()].map(([status, count]) => {
    const meta = known[status] ?? { label: status || "未标注", tone: "muted" as const }
    return { label: meta.label, ratio: count, tone: meta.tone }
  })
})

const trendOption = computed(() => ({
  grid: { left: 48, right: 24, top: 38, bottom: 34 },
  tooltip: { trigger: "axis" },
  legend: { top: 0, textStyle: { color: "#95a6be", fontSize: 11 }, icon: "circle" },
  xAxis: {
    type: "category",
    data: monthLabels.value,
    axisLabel: { color: "#95a6be", fontSize: 11 },
    axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
  },
  yAxis: {
    type: "value",
    minInterval: 1,
    name: "累计事实",
    nameTextStyle: { color: "#95a6be", fontSize: 11 },
    axisLabel: { color: "#95a6be", fontSize: 11 },
    splitLine: { lineStyle: { color: "rgba(149,166,190,0.12)" } },
  },
  series: [
    {
      name: `${industryName.value || "该产业"} 累计已入库事实`,
      type: "line",
      smooth: true,
      showSymbol: true,
      symbolSize: 5,
      areaStyle: { opacity: 0.18 },
      data: cumulativeCounts.value,
    },
  ],
}))

const statusLabel: Record<string, string> = {
  has_support: "得到支持",
  has_challenge: "受到挑战",
  disputed: "正反并存",
  open: "证据不足",
}

const statusTone: Record<string, "ok" | "warn" | "danger" | "muted"> = {
  has_support: "ok",
  has_challenge: "danger",
  disputed: "warn",
  open: "muted",
}

/** 设计规范只在有真实依据时使用「得到支持 / 尚待观察 / 受到挑战 / 证据不足」四种措辞。 */
function verdictText(status: string): string {
  return statusLabel[status] ?? (status || "未标注")
}

function verdictTone(status: string): "ok" | "warn" | "danger" | "muted" {
  return statusTone[status] ?? "muted"
}

function stanceText(stance: string): string {
  return stance === "support" ? "支撑" : stance === "challenge" ? "反驳" : stance || "未标注"
}

function stanceTone(stance: string): "ok" | "danger" | "muted" {
  return stance === "support" ? "ok" : stance === "challenge" ? "danger" : "muted"
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ""
  }
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "请求未被接受"
}

function parseFact(value: unknown): FactRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      quote: asString(row?.quote) ?? "—",
      filed: asString(row?.filed) ?? "—",
      sourceUrl: asString(row?.source_url) ?? "",
    },
  ]
}

function parseThesis(value: unknown): ThesisRow[] {
  const row = asRecord(value)
  const id = asString(row?.id) ?? asString(row?.thesis_id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      title: asString(row?.title) ?? "未命名假设",
      hypothesis: asString(row?.hypothesis) ?? "",
      supportCondition: asString(row?.support_condition) ?? "",
      challengeCondition: asString(row?.challenge_condition) ?? "",
      status: asString(row?.status) ?? "",
      rationale: asString(row?.rationale) ?? "",
      evidence: asList(row?.evidence).flatMap((item) => {
        const entry = asRecord(item)
        const quote = asString(entry?.quote)
        if (!quote) {
          return []
        }
        return [
          {
            id: asString(entry?.id) ?? quote.slice(0, 12),
            stance: asString(entry?.stance) ?? "",
            quote,
            publishedAt: asString(entry?.published_at) ?? "",
            sourceUrl: asString(entry?.source_url) ?? "",
          },
        ]
      }),
    },
  ]
}

async function loadIndustryName(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/industries?q="))
    const body = await readJson(response)
    if (!response.ok) {
      return
    }
    const list = asList(asRecord(asRecord(body)?.data)?.industries)
    for (const item of list) {
      const row = asRecord(item)
      if (asString(row?.id) === props.id) {
        industryName.value = asString(row?.name) ?? ""
        industryCode.value = asString(row?.code) ?? ""
        industryDescription.value = asString(row?.description) ?? ""
      }
    }
  } catch {
    industryName.value = ""
  }
}

async function loadFacts(): Promise<void> {
  const params = new URLSearchParams({ as_of: asOf.value })
  try {
    const response = await fetch(
      apiUrl(`/api/v1/industries/${encodeURIComponent(props.id)}/facts?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      facts.value = []
      factsError.value = serverMessage(body)
      return
    }
    facts.value = asList(asRecord(asRecord(body)?.data)?.facts).flatMap(parseFact)
    factsError.value = ""
  } catch {
    facts.value = []
    factsError.value = "无法连接接口"
  }
}

async function loadTheses(): Promise<void> {
  if (!signedIn.value) {
    theses.value = []
    thesesLoaded.value = false
    thesesDenied.value = true
    return
  }
  const params = new URLSearchParams({ as_of: asOf.value })
  const response = await authorizedFetch(
    `/api/v1/industries/${encodeURIComponent(props.id)}/theses?${params.toString()}`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const body = await readJson(response)
  if (response.status === 401) {
    theses.value = []
    thesesLoaded.value = false
    thesesDenied.value = true
    return
  }
  if (!response.ok) {
    theses.value = []
    thesesLoaded.value = false
    actionError.value = serverMessage(body)
    return
  }
  theses.value = asList(asRecord(asRecord(body)?.data)?.theses).flatMap(parseThesis)
  thesesLoaded.value = true
  thesesDenied.value = false
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  actionError.value = ""
  await Promise.all([loadIndustryName(), loadFacts(), loadTheses()])
  phase.value = "ready"
}

async function attach(factId: string, thesisId: string, stance: "support" | "challenge"): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!signedIn.value) {
    actionError.value = "请先登录"
    return
  }
  busy.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/theses/${encodeURIComponent(thesisId)}/evidence`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fact_id: factId, stance }),
      },
      session.accessToken,
      refreshToken,
    )
    const body = await readJson(response)
    if (!response.ok) {
      actionError.value = serverMessage(body)
      return
    }
    actionOk.value = "依据已附到假设上"
    await loadTheses()
  } catch {
    actionError.value = "无法连接接口"
  } finally {
    busy.value = false
  }
}

async function assess(thesisId: string): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!signedIn.value) {
    actionError.value = "请先登录"
    return
  }
  busy.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/theses/${encodeURIComponent(thesisId)}/assessments`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ as_of: asOf.value }),
      },
      session.accessToken,
      refreshToken,
    )
    const body = await readJson(response)
    if (!response.ok) {
      actionError.value = serverMessage(body)
      return
    }
    actionOk.value = "假设状态已按截止日更新"
    await loadTheses()
  } catch {
    actionError.value = "无法连接接口"
  } finally {
    busy.value = false
  }
}

watch(
  () => props.id,
  () => {
    void load()
  },
)

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="产业研究 / 逻辑追踪"
      :subtitle="industryDescription || '该产业还没有分类说明'"
    >
      <template #actions>
        <el-date-picker v-model="asOf" type="date" value-format="YYYY-MM-DD" :clearable="false" />
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs v-model="tab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="5" />
    <StateBlock v-else-if="failure" state="error" :message="failure" @retry="load" />

    <template v-else>
      <p v-if="actionOk" class="notice ok" role="status">{{ actionOk }}</p>
      <p v-if="actionError" class="notice danger" role="alert">{{ actionError }}</p>

      <div class="grid-4 section">
        <StatCard
          title="产业"
          :value="industryName || '未命名产业'"
          :caption="industryCode ? `分类代码 ${industryCode}` : '分类代码未提供'"
          info="名称与说明来自 GET /api/v1/industries，未提供景气度分数"
        >
          <template #icon><Star /></template>
        </StatCard>
        <StatCard
          title="研究假设"
          :value="theses.length"
          unit="条"
          :caption="thesesDenied ? '登录后可读自己的假设' : `${statusSegments.length} 种状态`"
          info="GET /api/v1/industries/{id}/theses，属于当前登录账号自己的研究记录"
        >
          <template #icon><TrendCharts /></template>
          <template #chart>
            <MiniRing
              v-if="statusSegments.length"
              :segments="statusSegments"
              :size="76"
              :thickness="9"
              show-legend
            />
            <span v-else class="muted">—</span>
          </template>
        </StatCard>
        <StatCard
          title="已入库事实"
          :value="facts.length"
          unit="条"
          :caption="`${firstFiled} 起 · 来源 ${domains.length} 个域名`"
          info="GET /api/v1/industries/{id}/facts?as_of=，只统计截止日可见的事实"
        >
          <template #icon><Histogram /></template>
          <template #chart>
            <MiniSpark :points="sparkPoints" kind="area" :width="110" :height="38" />
          </template>
        </StatCard>
        <StatCard
          title="最近申报日"
          :value="lastFiled"
          :caption="`覆盖 ${factsByMonth.length} 个申报月份`"
          info="后端没有产业景气度与机构关注度接口，这里不用替代性数字填充"
        >
          <template #icon><Histogram /></template>
          <template #chart>
            <MiniSpark :points="monthlyCounts" kind="bar" :width="110" :height="38" />
          </template>
        </StatCard>
      </div>

      <div v-if="tab === '逻辑追踪' || tab === '总览'" class="grid-2-1 section">
        <div class="stack">
          <PanelCard
            title="核心逻辑与研究假设"
            info="每行来自 GET /api/v1/industries/{id}/theses：假设原文、后端核对状态、核心依据、依据日期"
          >
            <StateBlock
              v-if="!signedIn"
              state="denied"
              message="研究假设属于登录用户自己的记录，登录后可以写下假设并附上依据"
            />
            <StateBlock
              v-else-if="thesesDenied"
              state="denied"
              :message="`无法读取研究假设，登录可能已失效：${loginHref}`"
            />
            <StateBlock v-else-if="!thesesLoaded" state="loading" :rows="3" />
            <StateBlock
              v-else-if="!theses.length"
              state="empty"
              message="这个产业下还没有研究假设。用 POST /api/v1/industries/{id}/theses 写入后，这里会出现假设、状态、依据与日期四列。"
            />
            <el-table v-else :data="theses" stripe>
              <el-table-column type="index" label="#" width="52" />
              <el-table-column label="研究假设" min-width="250">
                <template #default="{ row }">
                  <p class="cell-title">{{ row.title }}</p>
                  <p class="muted">{{ row.hypothesis || "—" }}</p>
                  <p class="muted">
                    支撑条件：{{ row.supportCondition || "—" }} · 反驳条件：{{
                      row.challengeCondition || "—"
                    }}
                  </p>
                </template>
              </el-table-column>
              <el-table-column label="当前状态" width="118">
                <template #default="{ row }">
                  <StatusPill :text="verdictText(row.status)" :tone="verdictTone(row.status)" />
                </template>
              </el-table-column>
              <el-table-column label="核心依据" min-width="260">
                <template #default="{ row }">
                  <ul v-if="row.evidence.length" class="bare-list">
                    <li v-for="(item, index) in row.evidence" :key="index">
                      <StatusPill :text="stanceText(item.stance)" :tone="stanceTone(item.stance)" />
                      <span>{{ item.quote }}</span>
                    </li>
                  </ul>
                  <span v-else class="muted">还没有附上依据</span>
                  <p v-if="row.rationale" class="muted">{{ row.rationale }}</p>
                </template>
              </el-table-column>
              <el-table-column label="更新时间" width="132">
                <template #default="{ row }">
                  <span class="tabular">{{ row.evidence[0]?.publishedAt?.slice(0, 10) || "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="132">
                <template #default="{ row }">
                  <el-button text size="small" :disabled="busy" @click="assess(row.id)">
                    按截止日核对
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>

          <PanelCard
            title="产业趋势及关键指标"
            info="横轴为事实申报月份，纵轴为累计已入库事实条数；后端没有产业景气指数接口，因此不画景气曲线"
          >
            <template #actions>
              <el-radio-group v-model="range" size="small">
                <el-radio-button v-for="item in RANGES" :key="item.key" :value="item.key">
                  {{ item.label }}
                </el-radio-button>
              </el-radio-group>
            </template>
            <div v-if="monthLabels.length" class="chart-wrap">
              <VChart class="chart" :option="trendOption" autoresize />
            </div>
            <StateBlock
              v-else
              state="empty"
              :message="
                facts.length
                  ? `${rangeStart} 之后没有可见事实，请把区间放宽到近3年或近5年`
                  : `该截止日没有可见事实，因此没有可画的时间序列。原因：${factsError || '接口返回 0 条事实'}`
              "
            />
            <p class="muted">
              区间 {{ rangeStart }} 起，数据截至 {{ asOf }}；共 {{ rangedFacts.length }} 条事实。
              关键指标（销售额、出货量、产能利用率等）后端没有对应接口，此卡不展示替代数字。
            </p>
          </PanelCard>

          <PanelCard
            title="催化剂与事件时间线"
            info="节点日期取接口返回的 published_at，立场取 evidence.stance；后端没有独立的利好/利空事件接口"
          >
            <template #actions>
              <StatusPill text="支撑" tone="ok" />
              <StatusPill text="反驳" tone="danger" />
              <StatusPill text="未标注" tone="muted" />
            </template>
            <StateBlock
              v-if="!signedIn || thesesDenied"
              state="denied"
              message="时间线来自你为假设附上的依据，登录后才会出现节点"
            />
            <StateBlock
              v-else-if="!eventDays.length"
              state="empty"
              message="还没有附上任何依据，因此没有事件节点。附上依据后，每个申报日会成为一个节点。"
            />
            <ol v-else class="timeline">
              <li v-for="node in eventDays" :key="node.day">
                <span class="timeline-day tabular">{{ node.day }}</span>
                <div class="timeline-body">
                  <p v-for="item in node.items" :key="item.key" class="timeline-item">
                    <StatusPill :text="stanceText(item.stance)" :tone="stanceTone(item.stance)" />
                    <span>{{ item.quote }}</span>
                    <em class="muted">{{ item.thesisTitle }}</em>
                    <a v-if="item.sourceUrl" :href="item.sourceUrl" target="_blank" rel="noreferrer">
                      原文
                    </a>
                  </p>
                </div>
              </li>
            </ol>
          </PanelCard>
        </div>

        <div class="stack">
          <PanelCard
            title="已入库事实"
            info="GET /api/v1/industries/{id}/facts?as_of=；后端没有需求端/供给端指标接口，因此这里展示真正可核对的事实"
          >
            <template #actions>
              <span class="muted">{{ facts.length }} 条</span>
            </template>
            <StateBlock v-if="!facts.length" state="empty" :message="factsError || '该截止日没有可见事实'" />
            <ul v-else class="fact-list">
              <li v-for="fact in facts.slice(0, 8)" :key="fact.id">
                <p class="fact-quote">{{ fact.quote }}</p>
                <p class="muted">
                  申报日 {{ fact.filed }}
                  <a v-if="fact.sourceUrl" :href="fact.sourceUrl" target="_blank" rel="noreferrer">
                    原文
                  </a>
                </p>
                <div v-if="theses.length" class="fact-actions">
                  <el-button
                    text
                    size="small"
                    :disabled="busy"
                    @click="attach(fact.id, theses[0]?.id ?? '', 'support')"
                  >
                    附到「{{ theses[0]?.title }}」作为支撑
                  </el-button>
                  <el-button
                    text
                    size="small"
                    :disabled="busy"
                    @click="attach(fact.id, theses[0]?.id ?? '', 'challenge')"
                  >
                    作为反驳
                  </el-button>
                </div>
              </li>
            </ul>
            <p v-if="facts.length > 8" class="muted">仅展示最近 8 条，完整清单见 /industry 列表页。</p>
          </PanelCard>

          <PanelCard title="事实来源分布" info="按原始链接域名统计，域名无法解析的不计入">
            <StateBlock v-if="!sourceSegments.length" state="empty" message="没有可解析来源链接的事实" />
            <MiniRing
              v-else
              :segments="sourceSegments"
              :size="128"
              :thickness="13"
              :center-text="String(facts.length)"
              center-caption="事实条数"
              show-legend
            />
          </PanelCard>

          <PanelCard title="核心观点" info="列出该产业下的假设原文与后端给出的核对说明，逐条编号">
            <StateBlock
              v-if="!theses.length"
              state="empty"
              message="还没有假设，因此没有可列出的观点"
            />
            <ol v-else class="point-list">
              <li v-for="thesis in theses" :key="thesis.id">
                <p class="cell-title">{{ thesis.title }}</p>
                <p class="doc-text">{{ thesis.hypothesis || "—" }}</p>
                <p class="muted">{{ thesis.rationale || "后端未给出核对说明" }}</p>
              </li>
            </ol>
          </PanelCard>

          <PanelCard title="假设状态" info="后端 thesis-status-v1 的四种状态与设计规范一致">
            <StateBlock v-if="!statusSegments.length" state="empty" message="还没有假设，因此没有状态" />
            <ul v-else class="status-list">
              <li v-for="item in statusSegments" :key="item.label">
                <StatusPill :text="item.label" :tone="item.tone" />
                <DeltaValue :value="item.ratio" :colored="false" suffix=" 条" size="sm" />
              </li>
            </ul>
          </PanelCard>

          <PanelCard title="政策与外部因素" info="设计要求：没有接口就如实说明">
            <p class="doc-text">
              政策强度、外部因素计数需要政策库与事件分类接口。后端目前没有这两类接口，
              因此本卡不填任何指数或百分比。
            </p>
            <p class="doc-text">
              已入库的公告类事实（含申报日与原文链接）在上方「已入库事实」卡中逐条列出，
              它们是目前唯一可核对的依据来源。
            </p>
          </PanelCard>

          <PanelCard title="代表性公司" info="相关性需要成分股接口，当前后端只提供事实原文">
            <StateBlock
              state="empty"
              message="后端没有产业成分股与关联度接口，因此不列出公司名称、涨幅或关联度，避免出现编造数据。"
            />
            <p class="muted">可核对的替代信息：上方事实卡中出现的公司名称来自公告原文。</p>
          </PanelCard>
        </div>
      </div>

      <template v-else-if="tab === '景气跟踪'">
        <PanelCard
          title="景气跟踪 · 事实入库节奏"
          info="横向为申报月份，数值为累计已入库事实条数；这是入库节奏，不是景气指数"
        >
          <div class="grid-3">
            <div class="mini-tile">
              <span class="muted">已入库事实</span>
              <p class="tile-value tabular">{{ facts.length }}</p>
            </div>
            <div class="mini-tile">
              <span class="muted">覆盖申报月份</span>
              <p class="tile-value tabular">{{ factsByMonth.length }}</p>
            </div>
            <div class="mini-tile">
              <span class="muted">活跃区间</span>
              <p class="tile-value tabular">{{ firstFiled }} ~ {{ lastFiled }}</p>
            </div>
          </div>
          <div v-if="factsByMonth.length" class="chart-wrap">
            <VChart class="chart" :option="trendOption" autoresize />
          </div>
          <StateBlock v-else state="empty" message="没有可见事实，因此画不出入库节奏" />
        </PanelCard>

        <PanelCard title="景气度说明" info="设计规范要求不编造数据">
          <p class="doc-text">
            效果图里的「产业景气度 68/100」「机构关注度 152 次」「相关公司数量 236 家」需要产业级指数、
            关注度统计与成分股接口，后端目前都没有，因此本页不显示这些分数。
          </p>
        </PanelCard>
      </template>

      <template v-else>
        <PanelCard title="相关主题" info="同一批产业分类，点击进入对应产业">
          <p class="doc-text">产业清单来自 GET /api/v1/industries，详情页按分类 id 取事实与假设。</p>
          <RouterLink class="back" to="/industry">返回产业列表</RouterLink>
        </PanelCard>

        <PanelCard title="相关页面" info="本页只覆盖产业事实与假设">
          <div class="link-row">
            <RouterLink to="/hotspots">市场热点追踪</RouterLink>
            <RouterLink to="/market/sectors">板块强弱</RouterLink>
            <RouterLink to="/reports">研究报告中心</RouterLink>
          </div>
        </PanelCard>
      </template>

      <SourceFooter
        :as-of="asOf"
        :sources="domains.length ? domains : ['尚未入库事实来源']"
        note="事实与假设直接来自后端接口，前端不做收益、涨幅或景气的二次计算"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.notice {
  margin: 0 0 12px;
  font-size: 13px;
}

.notice.ok {
  color: var(--ok);
}

.notice.danger {
  color: var(--danger);
}

.cell-title {
  margin: 0 0 2px;
  font-weight: 600;
}

.bare-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 6px;
}

.bare-list li {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 13px;
}

.chart {
  width: 100%;
  height: 280px;
}

.chart-wrap {
  margin-top: 8px;
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 14px;
  max-height: 360px;
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
}

.fact-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 12px;
}

.fact-list li {
  border-left: 2px solid var(--line);
  padding-left: 10px;
}

.fact-quote {
  margin: 0 0 2px;
  font-size: 13px;
  line-height: 1.6;
}

.fact-list .muted {
  margin: 0;
  font-size: 12px;
}

.fact-actions {
  margin-top: 4px;
}

.point-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 12px;
}

.point-list li {
  font-size: 13px;
}

.status-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.status-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.mini-tile {
  background: var(--panel-soft);
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
}

.tile-value {
  margin: 6px 0 0;
  font-size: 20px;
  font-weight: 700;
}

.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
}

.back {
  font-size: 13px;
}
</style>
