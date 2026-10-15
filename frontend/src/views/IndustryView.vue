<script setup lang="ts">
/**
 * 产业研究 · 产业列表（/industry）。
 *
 * 数据来源（API 调用与原实现完全一致，未新增后端依赖）：
 *  - GET  /api/v1/industries?q=信息技术            所有已入库产业（后端用名称包含匹配）
 *  - GET  /api/v1/industries/{id}/facts?as_of=      公开的已入库事实
 *  - GET  /api/v1/industries/{id}/theses?as_of=     当前登录用户自己的研究假设（需 Bearer）
 *  - POST /api/v1/industries/{id}/theses            写下可证伪的假设
 *  - POST /api/v1/theses/{id}/evidence              为假设附上已入库依据
 *  - POST /api/v1/theses/{id}/assessments           按截止日更新假设状态
 *
 * 不编造数据：后端目前只有「产业 + 已入库事实 + 用户假设」三类数据，
 * 没有景气指数、机构关注度、相关公司名单等接口。这些位置如实显示空态与原因，
 * 表格里只出现接口真实返回的行。
 */
import { computed, onMounted, ref, watch } from "vue"
import { Histogram, Star, TrendCharts } from "@element-plus/icons-vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"
import MiniRing from "../components/ui/MiniRing.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"

interface IndustryFact {
  id: string
  quote: string
  filed: string
  sourceUrl: string
}

interface IndustryRow {
  id: string
  code: string
  name: string
  description: string
  facts: IndustryFact[]
  factsLoaded: boolean
  factsError: string
}

interface ThesisRow {
  id: string
  title: string
  hypothesis: string
  supportCondition: string
  challengeCondition: string
  status: string
  rationale: string
  evidence: { stance: string; quote: string; publishedAt: string; sourceUrl: string }[]
}

interface EvidenceRow {
  thesisId: string
  title: string
  stance: string
  quote: string
  publishedAt: string
  sourceUrl: string
}

const session = useSessionStore()

/** 与后端默认口径对齐的在库事实截止日：按浏览器当天取，不写死历史日期。 */
const asOf = ref(new Date().toISOString().slice(0, 10))
const phase = ref<Phase>("loading")
const failure = ref("")
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)

const industries = ref<IndustryRow[]>([])
const industryId = ref("")
const theses = ref<ThesisRow[]>([])
const thesesLoaded = ref(false)
const thesesDenied = ref(false)
const tab = ref("logic")

const draftTitle = ref("")
const draftHypothesis = ref("")
const draftSupport = ref("")
const draftChallenge = ref("")
const draftWindow = ref("90")

const signedIn = computed<boolean>(() => Boolean(session.accessToken))
const selected = computed<IndustryRow | null>(
  () => industries.value.find((item) => item.id === industryId.value) ?? null,
)
const loginHref = computed<string>(
  () => `/login?redirect=${encodeURIComponent(`/industry/${industryId.value}`)}`,
)
const draftReady = computed<boolean>(
  () =>
    draftTitle.value.trim().length > 0 &&
    draftHypothesis.value.trim().length > 0 &&
    draftSupport.value.trim().length > 0 &&
    draftChallenge.value.trim().length > 0,
)

/** 页签上的条数全部来自接口真实返回，没有数据就不显示数字。 */
const tabs = computed(() => [
  { key: "logic", label: `逻辑追踪${theses.value.length ? ` ${theses.value.length}` : ""}` },
  {
    key: "facts",
    label: `产业链图谱${selected.value?.facts.length ? ` ${selected.value.facts.length}` : ""}`,
  },
  { key: "boom", label: "景气跟踪" },
  { key: "related", label: "相关主题" },
])

const selectedFacts = computed<IndustryFact[]>(() => selected.value?.facts ?? [])

/** 事实按申报日聚合，日期直接取接口返回的 filed，不做插值补齐。 */
const factsByMonth = computed(() => {
  const buckets = new Map<string, number>()
  for (const fact of selectedFacts.value) {
    const key = fact.filed.slice(0, 7)
    if (key.length === 7) {
      buckets.set(key, (buckets.get(key) ?? 0) + 1)
    }
  }
  return [...buckets.entries()].sort((left, right) => left[0].localeCompare(right[0]))
})

const monthLabels = computed<string[]>(() => factsByMonth.value.map((item) => item[0]))
const monthCounts = computed<number[]>(() => factsByMonth.value.map((item) => item[1]))

/** 累计事实条数：只累加接口返回的条数，不构造任何估值。 */
const cumulativeFacts = computed<number[]>(() => {
  let total = 0
  return monthCounts.value.map((count) => {
    total += count
    return total
  })
})

const firstFiled = computed<string>(() => {
  const dates = selectedFacts.value.map((fact) => fact.filed).filter((item) => item !== "—")
  return dates.length ? [...dates].sort()[0] ?? "—" : "—"
})
const lastFiled = computed<string>(() => {
  const dates = selectedFacts.value.map((fact) => fact.filed).filter((item) => item !== "—")
  return dates.length ? [...dates].sort()[dates.length - 1] ?? "—" : "—"
})

const evidenceRows = computed<EvidenceRow[]>(() =>
  theses.value.flatMap((thesis) =>
    thesis.evidence.map((item) => ({
      thesisId: thesis.id,
      title: thesis.title,
      stance: item.stance,
      quote: item.quote,
      publishedAt: item.publishedAt,
      sourceUrl: item.sourceUrl,
    })),
  ),
)

/** 依据时间线：同一天的依据合并到一个节点，日期取接口返回的 published_at。 */
const evidenceDays = computed(() => {
  const buckets = new Map<string, EvidenceRow[]>()
  for (const row of evidenceRows.value) {
    const day = row.publishedAt || "—"
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

/** 假设状态分布：只统计后端 returned 的四个状态，未知状态归入「其他」。 */
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

const maxEvidence = computed<number>(() =>
  Math.max(1, ...theses.value.map((thesis) => thesis.evidence.length)),
)

/** 多序列趋势：各产业累计已入库事实条数，序列名就是接口返回的产业名。 */
const trendOption = computed(() => {
  const axes = monthLabels.value
  const series = selected.value
    ? [
        {
          name: selected.value.name,
          type: "line",
          smooth: true,
          showSymbol: true,
          symbolSize: 5,
          areaStyle: { opacity: 0.16 },
          data: cumulativeFacts.value,
        },
      ]
    : []
  return {
    grid: { left: 48, right: 20, top: 34, bottom: 34 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, textStyle: { color: "#95a6be", fontSize: 11 }, icon: "circle" },
    xAxis: {
      type: "category",
      data: axes,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(149,166,190,0.12)" } },
    },
    series,
  }
})

/** 假设条数柱状图：一个产业一根柱，数值来自各自 facts 的实际条数。 */
const industryBarOption = computed(() => ({
  grid: { left: 56, right: 20, top: 20, bottom: 44 },
  tooltip: { trigger: "axis" },
  xAxis: {
    type: "category",
    data: industries.value.map((item) => item.name),
    axisLabel: { color: "#95a6be", fontSize: 11 },
    axisLine: { lineStyle: { color: "rgba(149,166,190,0.4)" } },
  },
  yAxis: {
    type: "value",
    minInterval: 1,
    name: "已入库事实",
    nameTextStyle: { color: "#95a6be", fontSize: 11 },
    axisLabel: { color: "#95a6be", fontSize: 11 },
    splitLine: { lineStyle: { color: "rgba(149,166,190,0.12)" } },
  },
  series: [
    {
      name: "已入库事实",
      type: "bar",
      barMaxWidth: 28,
      itemStyle: { color: "#3b82f6" },
      data: industries.value.map((item) => item.facts.length),
    },
  ],
}))

const statusLabel: Record<string, string> = {
  has_support: "有支持证据",
  has_challenge: "有反证",
  disputed: "正反并存",
  open: "证据不足",
}

const statusTone: Record<string, "ok" | "warn" | "danger" | "muted"> = {
  has_support: "ok",
  has_challenge: "danger",
  disputed: "warn",
  open: "muted",
}

function statusText(status: string): string {
  return statusLabel[status] ?? (status || "未标注")
}

function statusPillTone(status: string): "ok" | "warn" | "danger" | "muted" {
  return statusTone[status] ?? "muted"
}

function stanceLabel(stance: string): string {
  if (stance === "support") {
    return "支撑"
  }
  if (stance === "challenge") {
    return "反驳"
  }
  return stance || "未标注"
}

function stanceTone(stance: string): "ok" | "danger" | "muted" {
  if (stance === "support") {
    return "ok"
  }
  return stance === "challenge" ? "danger" : "muted"
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function parseFact(value: unknown): IndustryFact[] {
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

function parseIndustry(value: unknown): IndustryRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  const name = asString(row?.name)
  if (!id || !name) {
    return []
  }
  return [
    {
      id,
      name,
      code: asString(row?.code) ?? "",
      description: asString(row?.description) ?? "",
      facts: [],
      factsLoaded: false,
      factsError: "",
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

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "请求未被接受"
}

async function loadFacts(target: IndustryRow): Promise<void> {
  const params = new URLSearchParams({ as_of: asOf.value })
  try {
    const response = await fetch(
      apiUrl(`/api/v1/industries/${encodeURIComponent(target.id)}/facts?${params.toString()}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      target.facts = []
      target.factsError = serverMessage(body)
    } else {
      target.facts = asList(asRecord(asRecord(body)?.data)?.facts).flatMap(parseFact)
      target.factsError = ""
    }
  } catch {
    target.facts = []
    target.factsError = "无法连接接口"
  } finally {
    target.factsLoaded = true
  }
}

async function loadTheses(): Promise<void> {
  const id = industryId.value.trim()
  if (!id) {
    theses.value = []
    thesesLoaded.value = false
    return
  }
  if (!signedIn.value) {
    theses.value = []
    thesesLoaded.value = false
    thesesDenied.value = true
    return
  }
  const params = new URLSearchParams({ as_of: asOf.value })
  const response = await authorizedFetch(
    `/api/v1/industries/${encodeURIComponent(id)}/theses?${params.toString()}`,
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
  theses.value = []
  thesesLoaded.value = false
  thesesDenied.value = false
  try {
    const response = await fetch(apiUrl("/api/v1/industries?q=信息技术"))
    const body = await readJson(response)
    if (!response.ok) {
      failure.value = serverMessage(body)
      phase.value = "error"
      return
    }
    industries.value = asList(asRecord(asRecord(body)?.data)?.industries).flatMap(parseIndustry)
    if (!industries.value.length) {
      phase.value = "error"
      failure.value = "后端还没有返回任何产业分类"
      return
    }
    const previous = industryId.value
    industryId.value = industries.value.some((item) => item.id === previous)
      ? previous
      : industries.value[0]?.id ?? ""
    await Promise.all(industries.value.map((item) => loadFacts(item)))
    await loadTheses()
    phase.value = "ready"
  } catch {
    failure.value = "无法连接接口"
    phase.value = "error"
  }
}

async function selectIndustry(next: string): Promise<void> {
  industryId.value = next
  await loadTheses()
}

async function createThesis(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!signedIn.value) {
    actionError.value = "请先登录后再写下假设"
    return
  }
  if (!industryId.value || !draftReady.value) {
    actionError.value = "标题、假设、支撑条件与反驳条件都必须填写"
    return
  }
  const days = Number.parseInt(draftWindow.value, 10)
  busy.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/industries/${encodeURIComponent(industryId.value)}/theses`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: draftTitle.value.trim(),
          hypothesis: draftHypothesis.value.trim(),
          support_condition: draftSupport.value.trim(),
          challenge_condition: draftChallenge.value.trim(),
          observation_window_days: Number.isFinite(days) && days >= 1 ? days : 90,
        }),
      },
      session.accessToken,
      refreshToken,
    )
    const body = await readJson(response)
    if (!response.ok) {
      actionError.value = serverMessage(body)
      return
    }
    draftTitle.value = ""
    draftHypothesis.value = ""
    draftSupport.value = ""
    draftChallenge.value = ""
    draftWindow.value = "90"
    actionOk.value = "假设已保存"
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

async function attach(factId: string, stance: "support" | "challenge"): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!signedIn.value) {
    actionError.value = "请先登录"
    return
  }
  const target = theses.value[0]
  if (!target) {
    actionError.value = "先写下一个假设，再把事实附到它上面"
    return
  }
  busy.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/theses/${encodeURIComponent(target.id)}/evidence`,
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
    actionOk.value = `依据已附到「${target.title}」`
    await loadTheses()
  } catch {
    actionError.value = "无法连接接口"
  } finally {
    busy.value = false
  }
}

watch(industryId, () => {
  if (phase.value === "ready") {
    void loadTheses()
  }
})

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="产业研究"
      :subtitle="selected?.description || '按已入库事实与可证伪假设追踪产业逻辑，不做景气度打分'"
    >
      <template #actions>
        <el-select
          v-if="industries.length"
          :model-value="industryId"
          class="industry-select"
          placeholder="选择产业"
          @change="selectIndustry"
        >
          <el-option
            v-for="item in industries"
            :key="item.id"
            :label="item.name"
            :value="item.id"
          />
        </el-select>
        <el-date-picker v-model="asOf" type="date" value-format="YYYY-MM-DD" :clearable="false" />
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs v-model="tab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock
      v-if="phase === 'loading'"
      state="loading"
      :rows="5"
    />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      :message="failure"
      @retry="load"
    />

    <template v-else>
      <p v-if="actionOk" class="notice ok" role="status">{{ actionOk }}</p>
      <p v-if="actionError" class="notice danger" role="alert">{{ actionError }}</p>

      <div class="grid-4 section">
        <StatCard
          title="产业"
          :value="selected?.name ?? null"
          :caption="selected?.code ? `分类代码 ${selected.code}` : '分类代码未提供'"
          info="GET /api/v1/industries 返回的产业名称与描述，描述原文见页面副标题"
        >
          <template #icon><Star /></template>
        </StatCard>
        <StatCard
          title="已入库事实"
          :value="selectedFacts.length"
          unit="条"
          :caption="`${firstFiled} 起 · ${asOf} 截止日`"
          info="GET /api/v1/industries/{id}/facts?as_of= 在截止日可见的事实条数，不含任何估算值"
        >
          <template #icon><Histogram /></template>
          <template #chart>
            <MiniSpark :points="cumulativeFacts" kind="area" :width="110" :height="38" />
          </template>
        </StatCard>
        <StatCard
          title="研究假设"
          :value="theses.length"
          unit="条"
          :caption="thesesDenied ? '登录后可读自己的假设' : '仅当前账号自己的记录'"
          info="GET /api/v1/industries/{id}/theses 返回的假设条数，属于登录用户自己的研究记录"
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
          title="最近申报日"
          :value="lastFiled"
          :caption="`覆盖 ${monthLabels.length} 个申报月份`"
          info="已入库事实里最新的申报日；后端尚未提供产业成员公司名单，因此这里不列公司数量"
        >
          <template #icon><Histogram /></template>
          <template #chart>
            <MiniSpark :points="monthCounts" kind="bar" :width="110" :height="38" />
          </template>
        </StatCard>
      </div>

      <template v-if="tab === 'logic'">
        <div class="grid-2-1 section">
          <div class="stack">
            <PanelCard
              title="核心逻辑与研究假设"
              info="每行来自 GET /api/v1/industries/{id}/theses：假设原文、后端核对状态、核心依据、依据更新日"
            >
              <StateBlock
                v-if="!signedIn"
                state="denied"
                message="研究假设属于登录用户自己的记录，登录后可以写下假设并附上依据"
              />
              <StateBlock
                v-else-if="thesesDenied"
                state="denied"
                :message="`无法读取研究假设，登录可能已失效。可在登录页重新登录：${loginHref}`"
              />
              <StateBlock
                v-else-if="!thesesLoaded"
                state="loading"
                :rows="3"
              />
              <StateBlock
                v-else-if="!theses.length"
                state="empty"
                message="这个产业下还没有写过研究假设。下方表单保存后，这里会出现「假设 / 状态 / 核心依据 / 更新时间」四列。"
              />
              <el-table v-else :data="theses" stripe>
                <el-table-column type="index" label="#" width="52" />
                <el-table-column label="研究假设" min-width="240">
                  <template #default="{ row }">
                    <p class="cell-title">{{ row.title }}</p>
                    <p class="muted">{{ row.hypothesis || "—" }}</p>
                  </template>
                </el-table-column>
                <el-table-column label="当前状态" width="118">
                  <template #default="{ row }">
                    <StatusPill :text="statusText(row.status)" :tone="statusPillTone(row.status)" />
                  </template>
                </el-table-column>
                <el-table-column label="核心依据" min-width="260">
                  <template #default="{ row }">
                    <ul v-if="row.evidence.length" class="bare-list">
                      <li v-for="(item, index) in row.evidence" :key="index">
                        <StatusPill :text="stanceLabel(item.stance)" :tone="stanceTone(item.stance)" />
                        <span>{{ item.quote }}</span>
                      </li>
                    </ul>
                    <span v-else class="muted">还没有附上依据</span>
                  </template>
                </el-table-column>
                <el-table-column label="更新时间" width="140">
                  <template #default="{ row }">
                    <span class="tabular">{{ row.evidence[0]?.publishedAt || "—" }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="132">
                  <template #default="{ row }">
                    <el-button
                      text
                      size="small"
                      :disabled="busy"
                      @click="assess(row.id)"
                    >
                      按截止日核对
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>

              <div class="progress-list">
                <div v-for="row in theses" :key="row.id" class="progress">
                  <span class="progress-label">{{ row.title }}</span>
                  <span class="progress-track">
                    <i
                      :style="{ width: `${Math.round((row.evidence.length / maxEvidence) * 100)}%` }"
                    />
                  </span>
                  <span class="tabular muted">{{ row.evidence.length }} 条依据</span>
                </div>
                <p v-if="!theses.length" class="muted">暂无假设，因此没有依据覆盖度可显示。</p>
              </div>
            </PanelCard>

            <PanelCard
              title="产业趋势及关键指标"
              info="横轴为事实申报月份，纵轴为累计已入库事实条数；后端没有产业景气指数接口，因此不画景气曲线"
            >
              <template #actions>
                <span class="muted">累计事实</span>
              </template>
              <div v-if="monthLabels.length" class="chart-wrap">
                <VChart class="chart" :option="trendOption" autoresize />
              </div>
              <StateBlock
                v-else
                state="empty"
                :message="`该截止日没有可见事实，因此没有可画的时间序列。原因：${selected?.factsError || '后端返回 0 条事实'}`"
              />
              <p class="muted">
                区间 {{ firstFiled }} 至 {{ lastFiled }}。多产业对比见「景气跟踪」页签，数值同样只统计事实条数。
              </p>
            </PanelCard>

            <PanelCard
              title="催化剂与事件时间线"
              info="节点日期取接口返回的 published_at，立场取 evidence.stance；后端没有独立的催化剂/风险事件接口"
            >
              <StateBlock
                v-if="!signedIn || thesesDenied"
                state="denied"
                message="时间线来自你为假设附上的依据，登录后才会出现节点"
              />
              <StateBlock
                v-else-if="!evidenceDays.length"
                state="empty"
                message="还没有附上任何依据，因此没有事件节点。附上依据后，每个申报日会成为一个节点。"
              />
              <ol v-else class="timeline">
                <li v-for="node in evidenceDays" :key="node.day">
                  <span class="timeline-day tabular">{{ node.day }}</span>
                  <div class="timeline-body">
                    <p v-for="(item, index) in node.items" :key="index" class="timeline-item">
                      <StatusPill :text="stanceLabel(item.stance)" :tone="stanceTone(item.stance)" />
                      <span>{{ item.quote }}</span>
                      <em class="muted">{{ item.title }}</em>
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
            <PanelCard title="写下可证伪的假设" info="POST /api/v1/industries/{id}/theses">
              <el-alert v-if="!signedIn" type="info" :closable="false" show-icon>
                <template #title>
                  <a :href="loginHref">登录后可以写假设并附依据</a>
                </template>
              </el-alert>
              <el-form v-else label-position="top" class="draft" @submit.prevent="createThesis">
                <el-form-item label="标题">
                  <el-input v-model="draftTitle" maxlength="80" placeholder="例如：先进制程需求回升" />
                </el-form-item>
                <el-form-item label="假设">
                  <el-input
                    v-model="draftHypothesis"
                    type="textarea"
                    :rows="3"
                    maxlength="400"
                    placeholder="写出可以被证伪的判断"
                  />
                </el-form-item>
                <el-form-item label="支撑条件">
                  <el-input v-model="draftSupport" maxlength="200" />
                </el-form-item>
                <el-form-item label="反驳条件">
                  <el-input v-model="draftChallenge" maxlength="200" />
                </el-form-item>
                <el-form-item label="观察窗口（天）">
                  <el-input-number v-model="draftWindow" :min="1" :max="3650" controls-position="right" />
                </el-form-item>
                <el-button
                  type="primary"
                  :loading="busy"
                  :disabled="!draftReady"
                  @click="createThesis"
                >
                  保存假设
                </el-button>
              </el-form>
            </PanelCard>

            <PanelCard title="核心依据" info="最新一条已入库事实的原文与来源">
              <StateBlock
                v-if="!selectedFacts.length"
                state="empty"
                :message="selected?.factsError || '该截止日没有已入库事实'"
              />
              <template v-else>
                <p class="doc-text">{{ selectedFacts[selectedFacts.length - 1]?.quote }}</p>
                <p class="muted">
                  申报日 {{ selectedFacts[selectedFacts.length - 1]?.filed }} ·
                  <a
                    v-if="selectedFacts[selectedFacts.length - 1]?.sourceUrl"
                    :href="selectedFacts[selectedFacts.length - 1]?.sourceUrl"
                    target="_blank"
                    rel="noreferrer"
                  >
                    原始来源
                  </a>
                  <span v-else>来源未提供</span>
                </p>
              </template>
            </PanelCard>

            <PanelCard
              title="假设状态分布"
              info="按后端 thesis-status-v1 返回的状态计数，标签含义与设计规范一致"
            >
              <StateBlock
                v-if="!statusSegments.length"
                state="empty"
                message="还没有假设，因此没有状态分布"
              />
              <MiniRing
                v-else
                :segments="statusSegments"
                :size="132"
                :thickness="13"
                :center-text="String(theses.length)"
                center-caption="假设总数"
                show-legend
              />
            </PanelCard>

            <PanelCard title="相关主题" info="同一批已入库产业分类，点击进入对应产业详情">
              <ul class="link-list">
                <li v-for="item in industries" :key="item.id">
                  <RouterLink :to="`/industry/${item.id}`">{{ item.name }}</RouterLink>
                  <span class="muted tabular">{{ item.facts.length }} 条事实</span>
                </li>
              </ul>
            </PanelCard>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'facts'">
        <PanelCard
          title="产业链图谱 · 已入库事实"
          info="GET /api/v1/industries/{id}/facts?as_of=；每行都是一条可追溯到原始来源的事实"
        >
          <StateBlock v-if="!selected?.factsLoaded" state="loading" :rows="4" />
          <StateBlock
            v-else-if="selected?.factsError"
            state="error"
            :message="selected.factsError"
            @retry="selected && loadFacts(selected)"
          />
          <StateBlock
            v-else-if="!selectedFacts.length"
            state="empty"
            :message="`${asOf} 截止日该产业没有可见事实。后端只展示 published_at 不晚于截止日的条目。`"
          />
          <template v-else>
            <el-table :data="selectedFacts" stripe>
              <el-table-column type="index" label="#" width="52" />
              <el-table-column prop="quote" label="事实原文" min-width="360" />
              <el-table-column prop="filed" label="申报日" width="120" />
              <el-table-column label="来源" width="120">
                <template #default="{ row }">
                  <a v-if="row.sourceUrl" :href="row.sourceUrl" target="_blank" rel="noreferrer">
                    原始链接
                  </a>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
              <el-table-column label="用作依据" width="196">
                <template #default="{ row }">
                  <el-button text size="small" :disabled="busy" @click="attach(row.id, 'support')">
                    用作支撑
                  </el-button>
                  <el-button text size="small" :disabled="busy" @click="attach(row.id, 'challenge')">
                    用作反驳
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <p class="muted">
              「用作依据」把该事实附到列表里的第一个假设（{{ theses[0]?.title ?? "暂无假设" }}）上，
              需要先登录并至少写下一条假设。
            </p>
          </template>
        </PanelCard>

        <PanelCard title="依据时间线" info="节点来自假设已附依据的 published_at">
          <StateBlock
            v-if="!evidenceDays.length"
            state="empty"
            message="还没有附上依据，因此没有节点"
          />
          <ol v-else class="timeline">
            <li v-for="node in evidenceDays" :key="node.day">
              <span class="timeline-day tabular">{{ node.day }}</span>
              <div class="timeline-body">
                <p v-for="(item, index) in node.items" :key="index" class="timeline-item">
                  <StatusPill :text="stanceLabel(item.stance)" :tone="stanceTone(item.stance)" />
                  <span>{{ item.quote }}</span>
                  <a v-if="item.sourceUrl" :href="item.sourceUrl" target="_blank" rel="noreferrer">
                    原文
                  </a>
                </p>
              </div>
            </li>
          </ol>
        </PanelCard>
      </template>

      <template v-else-if="tab === 'boom'">
        <PanelCard
          title="景气跟踪 · 事实入库节奏"
          info="横轴为申报月份，纵轴为各产业累计事实条数；这是入库节奏，不是景气指数"
        >
          <div class="grid-3">
            <div class="mini-tile">
              <span class="muted">已入库事实</span>
              <p class="tile-value tabular">{{ selectedFacts.length }}</p>
            </div>
            <div class="mini-tile">
              <span class="muted">覆盖申报月份</span>
              <p class="tile-value tabular">{{ monthLabels.length }}</p>
            </div>
            <div class="mini-tile">
              <span class="muted">活跃区间</span>
              <p class="tile-value tabular">{{ firstFiled }} ~ {{ lastFiled }}</p>
            </div>
          </div>
          <div v-if="monthLabels.length" class="chart-wrap">
            <VChart class="chart" :option="trendOption" autoresize />
          </div>
          <StateBlock
            v-else
            state="empty"
            message="没有可见事实，因此画不出入库节奏"
          />
        </PanelCard>

        <PanelCard
          title="各产业事实条数"
          info="数据来自每个产业各自的 GET /api/v1/industries/{id}/facts，条数直接取接口返回的行数"
        >
          <div class="chart-wrap">
            <VChart class="chart" :option="industryBarOption" autoresize />
          </div>
        </PanelCard>

        <PanelCard title="景气度说明" info="设计规范要求不编造数据">
          <p class="doc-text">
            效果图里的「产业景气度 68/100」「机构关注度 152 次」需要产业级指数与关注度统计接口。
            后端目前没有这两个接口，因此本页不显示分数，也不显示替代性的估算值。
          </p>
          <p class="doc-text">
            可核对的替代口径是「已入库事实条数」与「月度入库节奏」，它们都能追溯到具体申报与原始链接。
          </p>
        </PanelCard>
      </template>

      <template v-else>
        <PanelCard title="相关主题" info="同一批产业分类，条数来自各自的 facts 接口">
          <el-table :data="industries" stripe>
            <el-table-column prop="name" label="产业" min-width="180" />
            <el-table-column label="分类代码" width="200">
              <template #default="{ row }">
                <span class="tabular">{{ row.code || "—" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="已入库事实" width="130">
              <template #default="{ row }">
                <span class="tabular">{{ row.factsLoaded ? row.facts.length : "—" }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="description" label="分类说明" min-width="240" />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <RouterLink :to="`/industry/${row.id}`">进入详情</RouterLink>
              </template>
            </el-table-column>
          </el-table>
        </PanelCard>

        <PanelCard title="相关页面" info="当前页只覆盖产业事实与假设，行情与研报在别的页面">
          <div class="link-row">
            <RouterLink to="/hotspots">市场热点追踪</RouterLink>
            <RouterLink to="/reports">研究报告中心</RouterLink>
            <RouterLink to="/market/sectors">板块强弱</RouterLink>
          </div>
        </PanelCard>
      </template>

      <p class="muted">
        请求号与数据时间见各接口返回的 meta；本页只做展示，不计算财务数字。
        截止日 {{ asOf }}。
      </p>
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.industry-select {
  width: 180px;
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

.cell-title + .muted {
  margin: 0;
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
  height: 260px;
}

.chart-wrap {
  margin-top: 8px;
}

.progress-list {
  margin-top: 16px;
  display: grid;
  gap: 8px;
}

.progress {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px auto;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.progress-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-track {
  display: block;
  height: 6px;
  border-radius: 999px;
  background: var(--panel-soft);
  overflow: hidden;
}

.progress-track i {
  display: block;
  height: 100%;
  background: var(--accent);
}

.timeline {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 14px;
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

.draft :deep(.el-form-item) {
  margin-bottom: 12px;
}

.draft :deep(.el-form-item__label) {
  font-size: 12px;
  color: var(--muted);
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

.link-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.link-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
}

.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 13px;
}
</style>
