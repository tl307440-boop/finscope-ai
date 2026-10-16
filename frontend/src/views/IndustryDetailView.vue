<script setup lang="ts">
/**
 * 产业研究 · 逻辑追踪详情（/industry/:id）。
 *
 * 数据来源：
 *  - GET  /api/v1/industries?q=                     行业名称、分类与说明（也用于顶部切换）
 *  - GET  /api/v1/market/sectors?as_of=             该行业成分股的相对强弱分数
 *  - GET  /api/v1/industries/{id}/facts?as_of=      成分公司已入库的财报事实（含申报日与原文链接）
 *  - GET  /api/v1/industries/{id}/theses?as_of=     当前账号的研究假设与已附依据（需登录）
 *  - POST /api/v1/industries/{id}/theses            新增假设
 *  - POST /api/v1/theses/{id}/evidence              把事实附到假设上
 *  - POST /api/v1/theses/{id}/assessments           按截止日核对假设状态
 *
 * 不编造数据：需求端/供给端指标、景气度、机构关注度、利好利空分类都没有数据源，页面不放这些卡片。
 */
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { DataAnalysis, Document, TrendCharts } from "@element-plus/icons-vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import {
  STRENGTH_RULE,
  factText,
  hostLabel,
  industryFamily,
  parseFactQuote,
  parseSectors,
  strengthCaption,
  strengthTone,
  type SectorStrength,
} from "../api/industry"
import { restoreSession, useSessionStore } from "../stores/auth"
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
  label: string
  display: string
  accession: string
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
const router = useRouter()

const session = useSessionStore()
const asOf = ref(new Date().toISOString().slice(0, 10))
const phase = ref<Phase>("loading")
const failure = ref("")
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)

const catalog = ref<{ id: string; name: string }[]>([])
const industryName = ref("")
const industryCode = ref("")
const industryDescription = ref("")
const strength = ref<SectorStrength | null>(null)
const facts = ref<FactRow[]>([])
const factsError = ref("")
const theses = ref<ThesisRow[]>([])
const thesesLoaded = ref(false)
const thesesDenied = ref(false)

const tab = ref("logic")
const range = ref("all")
const factPage = ref(1)
const FACT_PAGE_SIZE = 15
const attachTarget = ref("")

const draftTitle = ref("")
const draftHypothesis = ref("")
const draftSupport = ref("")
const draftChallenge = ref("")
const draftWindow = ref(90)

const RANGES: { key: string; label: string; days: number | null }[] = [
  { key: "1y", label: "近1年", days: 365 },
  { key: "3y", label: "近3年", days: 1095 },
  { key: "all", label: "全部", days: null },
]

const STATUS_META: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  has_support: { label: "得到支持", tone: "ok" },
  disputed: { label: "正反并存", tone: "warn" },
  has_challenge: { label: "受到挑战", tone: "danger" },
  open: { label: "证据不足", tone: "muted" },
}

const signedIn = computed<boolean>(() => session.authenticated)
const loginHref = computed<string>(
  () => `/login?redirect=${encodeURIComponent(`/industry/${props.id}`)}`,
)
const family = computed(() => industryFamily(industryCode.value))
const draftReady = computed<boolean>(
  () =>
    draftTitle.value.trim().length > 0 &&
    draftHypothesis.value.trim().length > 0 &&
    draftSupport.value.trim().length > 0 &&
    draftChallenge.value.trim().length > 0,
)

const tabs = computed(() => [
  { key: "overview", label: "行业总览", to: "/industry" },
  { key: "logic", label: `逻辑追踪${theses.value.length ? ` ${theses.value.length}` : ""}` },
  { key: "facts", label: `已入库事实${facts.value.length ? ` ${facts.value.length}` : ""}` },
])

const sortedFiled = computed<string[]>(() => facts.value.map((fact) => fact.filed).filter(Boolean).sort())
const firstFiled = computed<string>(() => sortedFiled.value[0] ?? "")
const lastFiled = computed<string>(() => sortedFiled.value[sortedFiled.value.length - 1] ?? "")
const latestFacts = computed<FactRow[]>(() =>
  [...facts.value].sort((left, right) => right.filed.localeCompare(left.filed)),
)
const pagedFacts = computed<FactRow[]>(() =>
  latestFacts.value.slice((factPage.value - 1) * FACT_PAGE_SIZE, factPage.value * FACT_PAGE_SIZE),
)

const rangeStart = computed<string>(() => {
  const days = RANGES.find((item) => item.key === range.value)?.days ?? null
  if (days === null) {
    return ""
  }
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
})

/** 事实按申报月聚合，月份直接取申报日，不插值补齐。 */
const months = computed(() => {
  const buckets = new Map<string, number>()
  for (const day of sortedFiled.value) {
    if (rangeStart.value && day < rangeStart.value) {
      continue
    }
    const key = day.slice(0, 7)
    buckets.set(key, (buckets.get(key) ?? 0) + 1)
  }
  return [...buckets.entries()].sort((left, right) => left[0].localeCompare(right[0]))
})
const monthlyCounts = computed<number[]>(() => months.value.map((item) => item[1]))
const rangedCount = computed<number>(() => monthlyCounts.value.reduce((sum, count) => sum + count, 0))

const sourceNames = computed<string[]>(() => {
  const found = new Set<string>()
  for (const fact of facts.value) {
    const name = hostLabel(fact.sourceUrl)
    if (name) {
      found.add(name)
    }
  }
  return [...found]
})

const statusSegments = computed(() => {
  const counts = new Map<string, number>()
  for (const thesis of theses.value) {
    counts.set(thesis.status, (counts.get(thesis.status) ?? 0) + 1)
  }
  return [...counts.entries()].map(([status, count]) => ({
    label: verdictText(status),
    ratio: count,
    tone: verdictTone(status),
  }))
})

const eventDays = computed(() => {
  const buckets = new Map<string, { key: string; thesisTitle: string; stance: string; quote: string; sourceUrl: string }[]>()
  for (const thesis of theses.value) {
    thesis.evidence.forEach((item, index) => {
      const day = item.publishedAt.slice(0, 10) || "日期未知"
      const list = buckets.get(day) ?? []
      list.push({
        key: `${thesis.id}-${index}`,
        thesisTitle: thesis.title,
        stance: item.stance,
        quote: factText(item.quote),
        sourceUrl: item.sourceUrl,
      })
      buckets.set(day, list)
    })
  }
  return [...buckets.entries()]
    .sort((left, right) => right[0].localeCompare(left[0]))
    .map(([day, items]) => ({ day, items }))
})

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") {
    return fallback
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

const trendOption = computed(() => {
  let running = 0
  const cumulative = monthlyCounts.value.map((count) => (running += count))
  return {
    grid: { left: 44, right: 44, top: 36, bottom: 30 },
    tooltip: { trigger: "axis" },
    legend: { top: 0, left: "center" },
    xAxis: { type: "category", data: months.value.map((item) => item[0]) },
    yAxis: [
      { type: "value", minInterval: 1, name: "当月条数" },
      { type: "value", minInterval: 1, name: "累计", splitLine: { show: false } },
    ],
    series: [
      {
        name: "当月入库",
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { color: cssVar("--cyan", "#22d3ee"), borderRadius: [3, 3, 0, 0] },
        data: monthlyCounts.value,
      },
      {
        name: "累计事实",
        type: "line",
        yAxisIndex: 1,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.12 },
        data: cumulative,
      },
    ],
  }
})

function verdictText(status: string): string {
  return STATUS_META[status]?.label ?? "待核对"
}

function verdictTone(status: string): "ok" | "warn" | "danger" | "muted" {
  return STATUS_META[status]?.tone ?? "muted"
}

function stanceText(stance: string): string {
  return stance === "support" ? "支撑" : stance === "challenge" ? "反驳" : "未标注"
}

function stanceTone(stance: string): "ok" | "danger" | "muted" {
  return stance === "support" ? "ok" : stance === "challenge" ? "danger" : "muted"
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function serverMessage(body: unknown): string {
  return asString(asRecord(asRecord(body)?.error)?.message) ?? "操作未成功，请稍后重试"
}

function parseFact(value: unknown): FactRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  const quote = asString(row?.quote) ?? ""
  const view = parseFactQuote(quote)
  return [
    {
      id,
      quote,
      label: view?.label ?? "公告摘录",
      display: view?.display ?? quote,
      accession: view?.accession ?? "",
      filed: asString(row?.filed) ?? "",
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

async function loadIndustry(): Promise<void> {
  const response = await fetch(apiUrl("/api/v1/industries?q="))
  const body = await readJson(response)
  if (!response.ok) {
    failure.value = serverMessage(body)
    return
  }
  const list = asList(asRecord(asRecord(body)?.data)?.industries).flatMap((item) => {
    const row = asRecord(item)
    const id = asString(row?.id)
    const name = asString(row?.name)
    return id && name ? [{ id, name, code: asString(row?.code) ?? "", description: asString(row?.description) ?? "" }] : []
  })
  catalog.value = list.map(({ id, name }) => ({ id, name }))
  const hit = list.find((row) => row.id === props.id)
  if (!hit) {
    failure.value = "找不到这个行业，可能已被移出行业目录"
    return
  }
  industryName.value = hit.name
  industryCode.value = hit.code
  industryDescription.value = hit.description
}

async function loadStrength(): Promise<void> {
  try {
    const response = await fetch(
      apiUrl(`/api/v1/market/sectors?${new URLSearchParams({ as_of: asOf.value }).toString()}`),
    )
    strength.value = response.ok
      ? (parseSectors(await readJson(response)).find((row) => row.name === industryName.value) ?? null)
      : null
  } catch {
    strength.value = null
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
    factsError.value = "网络连接失败，请稍后重试"
  }
}

async function loadTheses(): Promise<void> {
  if (!signedIn.value) {
    theses.value = []
    thesesLoaded.value = false
    thesesDenied.value = false
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
  if (!theses.value.some((thesis) => thesis.id === attachTarget.value)) {
    attachTarget.value = theses.value[0]?.id ?? ""
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  actionError.value = ""
  factPage.value = 1
  try {
    await loadIndustry()
    if (failure.value) {
      phase.value = "error"
      return
    }
    await Promise.all([loadStrength(), loadFacts(), loadTheses()])
    phase.value = "ready"
  } catch {
    failure.value = "网络连接失败，请稍后重试"
    phase.value = "error"
  }
}

function switchIndustry(next: string): void {
  if (next && next !== props.id) {
    void router.push(`/industry/${next}`)
  }
}

async function createThesis(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!signedIn.value) {
    actionError.value = "请先登录后再写下假设"
    return
  }
  if (!draftReady.value) {
    actionError.value = "标题、假设、支撑条件与反驳条件都必须填写"
    return
  }
  busy.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/industries/${encodeURIComponent(props.id)}/theses`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: draftTitle.value.trim(),
          hypothesis: draftHypothesis.value.trim(),
          support_condition: draftSupport.value.trim(),
          challenge_condition: draftChallenge.value.trim(),
          observation_window_days: draftWindow.value >= 1 ? draftWindow.value : 90,
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
    draftWindow.value = 90
    actionOk.value = "假设已保存"
    await loadTheses()
  } catch {
    actionError.value = "网络连接失败，请稍后重试"
  } finally {
    busy.value = false
  }
}

async function attach(factId: string, stance: "support" | "challenge"): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  const target = theses.value.find((thesis) => thesis.id === attachTarget.value)
  if (!signedIn.value || !target) {
    actionError.value = signedIn.value ? "先写下一条假设，再把事实附上去" : "请先登录"
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
    actionOk.value = `已作为${stanceText(stance)}依据附到「${target.title}」`
    await loadTheses()
  } catch {
    actionError.value = "网络连接失败，请稍后重试"
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
    actionOk.value = `已按 ${asOf.value} 重新核对假设状态`
    await loadTheses()
  } catch {
    actionError.value = "网络连接失败，请稍后重试"
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
    <PageHeader title="产业研究 / 逻辑追踪" :subtitle="industryDescription">
      <template #actions>
        <el-select
          v-if="catalog.length"
          :model-value="props.id"
          class="industry-select"
          filterable
          @change="switchIndustry"
        >
          <el-option v-for="item in catalog" :key="item.id" :label="item.name" :value="item.id" />
        </el-select>
        <el-date-picker
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          :clearable="false"
          class="asof-picker"
          @change="load"
        />
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs v-model="tab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="5" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else>
      <p v-if="actionOk" class="notice ok" role="status">{{ actionOk }}</p>
      <p v-if="actionError" class="notice danger" role="alert">{{ actionError }}</p>

      <div class="grid-4 section">
        <PanelCard class="hero" compact>
          <div class="hero-body">
            <span class="hero-mark" aria-hidden="true">{{ industryName.slice(0, 1) }}</span>
            <div class="hero-text">
              <h2>{{ industryName }}</h2>
              <p class="hero-tags">
                <StatusPill :text="family" tone="accent" />
                <StatusPill
                  v-if="strength?.members"
                  :text="`有效成分 ${strength.members} 只`"
                  tone="muted"
                />
              </p>
              <p class="muted hero-desc">
                {{ family === "申万一级" ? "申万 2021 版一级行业，成分为人工整理的代表性 A 股。" : "目录分类，用于归档成分公司的财报事实。" }}
              </p>
            </div>
          </div>
        </PanelCard>
        <StatCard
          title="板块强弱"
          :value="strength?.scored ? strength.score : null"
          unit="/100"
          :caption="strengthCaption(strength)"
          :info="STRENGTH_RULE"
          more-text=""
          tone="cyan"
        >
          <template #icon><DataAnalysis /></template>
          <template #chart>
            <span v-if="strength?.scored" class="gauge">
              <i
                :class="`tone-${strengthTone(strength)}`"
                :style="{ width: `${strength.score ?? 0}%` }"
              />
            </span>
          </template>
        </StatCard>
        <StatCard
          title="研究假设"
          :value="signedIn ? theses.length : null"
          unit="条"
          :caption="signedIn ? `${statusSegments.length} 种核对状态` : '登录后查看自己的假设'"
          info="你在这个行业下写下的可证伪假设，只有自己可见"
          more-text=""
          tone="violet"
        >
          <template #icon><TrendCharts /></template>
          <template #chart>
            <MiniRing
              v-if="statusSegments.length"
              :segments="statusSegments"
              :size="58"
              :thickness="8"
            />
          </template>
        </StatCard>
        <StatCard
          title="已入库财报事实"
          :value="facts.length || null"
          unit="条"
          :caption="facts.length ? `${firstFiled} 至 ${lastFiled}` : '成分公司的财报事实尚未入库'"
          info="截止日前已公开的成分公司财报数字，每条都能追溯到原始申报"
          more-text=""
          tone="orange"
        >
          <template #icon><Document /></template>
          <template #chart>
            <MiniSpark
              v-if="monthlyCounts.length > 1"
              :points="monthlyCounts"
              kind="bar"
              :width="104"
              :height="38"
            />
          </template>
        </StatCard>
      </div>

      <div v-if="tab === 'logic'" class="grid-2-1 section">
        <div class="stack">
          <PanelCard title="核心逻辑与研究假设" info="每条假设写明支撑与反驳条件，按截止日用已附依据核对状态" flush>
            <div v-if="!signedIn" class="gate">
              <StateBlock state="denied" message="研究假设属于个人记录，登录后可以写下假设并附上财报依据" />
              <RouterLink class="gate-link" :to="loginHref">去登录 ›</RouterLink>
            </div>
            <div v-else-if="thesesDenied" class="gate">
              <StateBlock state="denied" message="登录已失效，重新登录后可读取研究假设" />
              <RouterLink class="gate-link" :to="loginHref">重新登录 ›</RouterLink>
            </div>
            <StateBlock v-else-if="!thesesLoaded" state="loading" :rows="3" />
            <StateBlock
              v-else-if="!theses.length"
              state="empty"
              message="这个行业下还没有研究假设，用右侧表单写下第一条"
            />
            <el-table v-else :data="theses">
              <el-table-column type="index" label="#" width="48" />
              <el-table-column label="研究假设" min-width="240">
                <template #default="{ row }">
                  <p class="cell-title">{{ row.title }}</p>
                  <p class="muted cell-sub">{{ row.hypothesis }}</p>
                </template>
              </el-table-column>
              <el-table-column label="当前状态" width="104">
                <template #default="{ row }">
                  <StatusPill :text="verdictText(row.status)" :tone="verdictTone(row.status)" />
                </template>
              </el-table-column>
              <el-table-column label="核心依据" min-width="220">
                <template #default="{ row }">
                  <ul v-if="row.evidence.length" class="bare-list">
                    <li v-for="item in row.evidence.slice(0, 2)" :key="item.id">
                      <StatusPill :text="stanceText(item.stance)" :tone="stanceTone(item.stance)" />
                      <span>{{ factText(item.quote) }}</span>
                    </li>
                  </ul>
                  <span v-else class="muted">还没有附上依据</span>
                </template>
              </el-table-column>
              <el-table-column label="更新时间" width="108">
                <template #default="{ row }">
                  <span class="tabular">{{ row.evidence[0]?.publishedAt?.slice(0, 10) || "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="" width="92" align="right">
                <template #default="{ row }">
                  <el-button text size="small" :disabled="busy" @click="assess(row.id)">核对</el-button>
                </template>
              </el-table-column>
            </el-table>
          </PanelCard>

          <PanelCard title="财报事实入库节奏" info="按申报月份统计成分公司财报事实条数；反映信息披露节奏，不是景气指数">
            <template v-if="facts.length" #actions>
              <el-radio-group v-model="range" size="small">
                <el-radio-button v-for="item in RANGES" :key="item.key" :value="item.key">
                  {{ item.label }}
                </el-radio-button>
              </el-radio-group>
            </template>
            <VChart v-if="months.length" class="chart" :option="trendOption" autoresize />
            <StateBlock
              v-else-if="facts.length"
              state="empty"
              :message="`${rangeStart} 之后没有新的申报，放宽时间范围查看`"
            />
            <StateBlock
              v-else
              state="empty"
              :message="factsError || '成分公司的财报事实尚未入库（目前只收录美股 SEC 财报），暂无可画的节奏'"
            />
            <p v-if="months.length" class="muted chart-note">
              所选区间共 {{ rangedCount }} 条，跨 {{ months.length }} 个申报月份；来源 {{ sourceNames.join("、") || "—" }}。
            </p>
          </PanelCard>

          <PanelCard title="催化剂与事件时间线" info="节点来自你为假设附上的依据，按申报日排列">
            <template #actions>
              <StatusPill text="支撑" tone="ok" />
              <StatusPill text="反驳" tone="danger" />
            </template>
            <StateBlock
              v-if="!signedIn || thesesDenied"
              state="denied"
              message="登录并为假设附上依据后，这里按日期展示事件节点"
            />
            <StateBlock
              v-else-if="!eventDays.length"
              state="empty"
              message="还没有附上依据；在「已入库事实」里把事实附到假设上即可生成节点"
            />
            <ol v-else class="timeline">
              <li v-for="node in eventDays" :key="node.day">
                <span class="timeline-day tabular">{{ node.day }}</span>
                <div class="timeline-body">
                  <p v-for="item in node.items" :key="item.key" class="timeline-item">
                    <StatusPill :text="stanceText(item.stance)" :tone="stanceTone(item.stance)" />
                    <span>{{ item.quote }}</span>
                    <em class="muted">{{ item.thesisTitle }}</em>
                    <a v-if="item.sourceUrl" :href="item.sourceUrl" target="_blank" rel="noreferrer">原文</a>
                  </p>
                </div>
              </li>
            </ol>
          </PanelCard>
        </div>

        <div class="stack">
          <PanelCard title="成分与强弱" :info="STRENGTH_RULE" more-text="板块强弱" more-to="/market/sectors">
            <template v-if="strength?.scored">
              <div class="strength-row">
                <span class="strength-num tabular">{{ strength.score?.toFixed(1) }}</span>
                <span :class="['strength-label', `tone-${strengthTone(strength)}`]">{{ strength.label }}</span>
              </div>
              <span class="gauge wide">
                <i :class="`tone-${strengthTone(strength)}`" :style="{ width: `${strength.score ?? 0}%` }" />
              </span>
              <p class="gauge-scale muted tabular">
                <span v-for="mark in [0, 40, 60, 100]" :key="mark" :style="{ left: `${mark}%` }">{{ mark }}</span>
              </p>
            </template>
            <StateBlock v-else state="empty" :message="`${strengthCaption(strength)}：有效成分有完整日线后自动计算`" />
            <p v-if="strength?.notice" class="muted fine">{{ strength.notice }}</p>
          </PanelCard>

          <PanelCard title="写下可证伪的假设" info="写清楚什么证据算支撑、什么算反驳，之后按截止日核对">
            <div v-if="!signedIn" class="gate">
              <p class="muted">登录后可以在这个行业下写假设并附依据。</p>
              <RouterLink class="gate-link" :to="loginHref">去登录 ›</RouterLink>
            </div>
            <el-form v-else label-position="top" class="draft" @submit.prevent="createThesis">
              <el-form-item label="标题">
                <el-input v-model="draftTitle" maxlength="80" :placeholder="`例如：${industryName}盈利能力改善`" />
              </el-form-item>
              <el-form-item label="假设">
                <el-input
                  v-model="draftHypothesis"
                  type="textarea"
                  :rows="2"
                  maxlength="400"
                  placeholder="写出可以被证伪的判断"
                />
              </el-form-item>
              <div class="grid-2 draft-pair">
                <el-form-item label="支撑条件">
                  <el-input v-model="draftSupport" maxlength="200" placeholder="出现什么算支撑" />
                </el-form-item>
                <el-form-item label="反驳条件">
                  <el-input v-model="draftChallenge" maxlength="200" placeholder="出现什么算反驳" />
                </el-form-item>
              </div>
              <div class="draft-foot">
                <el-form-item label="观察窗口（天）">
                  <el-input-number v-model="draftWindow" :min="1" :max="3650" controls-position="right" />
                </el-form-item>
                <el-button type="primary" :loading="busy" :disabled="!draftReady" @click="createThesis">
                  保存假设
                </el-button>
              </div>
            </el-form>
          </PanelCard>

          <PanelCard title="核心观点" info="你写下的假设与最近一次核对说明">
            <StateBlock
              v-if="!theses.length"
              state="empty"
              :message="signedIn ? '还没有假设，保存后在这里逐条列出' : '登录后显示你的研究观点'"
            />
            <ol v-else class="point-list">
              <li v-for="thesis in theses" :key="thesis.id">
                <p class="cell-title">
                  {{ thesis.title }}
                  <StatusPill :text="verdictText(thesis.status)" :tone="verdictTone(thesis.status)" />
                </p>
                <p class="doc-text">{{ thesis.hypothesis }}</p>
                <p v-if="thesis.rationale" class="muted">{{ thesis.rationale }}</p>
              </li>
            </ol>
          </PanelCard>

          <PanelCard title="最新已入库事实" :info="`共 ${facts.length} 条，完整清单见「已入库事实」页签`">
            <StateBlock
              v-if="!facts.length"
              state="empty"
              :message="factsError || '暂无成分公司的财报事实'"
            />
            <ul v-else class="fact-list">
              <li v-for="fact in latestFacts.slice(0, 5)" :key="fact.id">
                <p class="fact-line">
                  <span>{{ fact.label }}</span>
                  <strong class="tabular">{{ fact.display }}</strong>
                </p>
                <p class="muted fact-meta">
                  申报日 {{ fact.filed || "—" }}
                  <a v-if="fact.sourceUrl" :href="fact.sourceUrl" target="_blank" rel="noreferrer">原文</a>
                </p>
              </li>
            </ul>
            <el-button v-if="facts.length > 5" text size="small" @click="tab = 'facts'">查看全部 ›</el-button>
          </PanelCard>
        </div>
      </div>

      <PanelCard v-else title="已入库事实" info="截止日前已公开的成分公司财报数字；可把任意一条附到假设上作为支撑或反驳" flush>
        <template #actions>
          <template v-if="theses.length">
            <span class="muted">附到假设</span>
            <el-select v-model="attachTarget" size="small" class="attach-select">
              <el-option v-for="thesis in theses" :key="thesis.id" :label="thesis.title" :value="thesis.id" />
            </el-select>
          </template>
          <span v-else class="muted">{{ signedIn ? "写下假设后可把事实附上去" : "登录后可把事实附到假设上" }}</span>
        </template>
        <StateBlock
          v-if="!facts.length"
          state="empty"
          :message="factsError || '成分公司的财报事实尚未入库（目前只收录美股 SEC 财报）'"
        />
        <template v-else>
          <el-table :data="pagedFacts">
            <el-table-column label="科目" min-width="140">
              <template #default="{ row }">{{ row.label }}</template>
            </el-table-column>
            <el-table-column label="数值" min-width="150" align="right">
              <template #default="{ row }"><span class="tabular strong">{{ row.display }}</span></template>
            </el-table-column>
            <el-table-column label="申报日" width="116">
              <template #default="{ row }"><span class="tabular">{{ row.filed || "—" }}</span></template>
            </el-table-column>
            <el-table-column label="申报号" min-width="190">
              <template #default="{ row }"><span class="tabular muted">{{ row.accession || "—" }}</span></template>
            </el-table-column>
            <el-table-column label="来源" width="110">
              <template #default="{ row }">
                <a v-if="row.sourceUrl" :href="row.sourceUrl" target="_blank" rel="noreferrer">
                  {{ hostLabel(row.sourceUrl) || "原文" }}
                </a>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column v-if="theses.length" label="用作依据" width="150" align="right">
              <template #default="{ row }">
                <el-button text size="small" :disabled="busy" @click="attach(row.id, 'support')">支撑</el-button>
                <el-button text size="small" :disabled="busy" @click="attach(row.id, 'challenge')">反驳</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination
              v-model:current-page="factPage"
              :page-size="FACT_PAGE_SIZE"
              :total="facts.length"
              layout="total, prev, pager, next"
              small
              background
            />
          </div>
        </template>
      </PanelCard>

      <SourceFooter
        :as-of="asOf"
        :sources="sourceNames.length ? [...sourceNames, '成分股日线'] : ['申万行业分类（2021 版）', '成分股日线']"
        note="事实与强弱分数直接取自入库数据，页面不做收益或景气的二次估算"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.industry-select {
  width: 150px;
}

.asof-picker {
  width: 150px;
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

.hero-body {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.hero-mark {
  flex: none;
  width: 52px;
  height: 52px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 40%, transparent), var(--panel-deep));
  border: 1px solid var(--edge-light);
  color: var(--text-strong);
  font-size: 22px;
  font-weight: 700;
}

.hero-text h2 {
  margin: 0 0 6px;
  font-size: 22px;
  color: var(--text-strong);
}

.hero-tags {
  margin: 0 0 6px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.hero-desc {
  margin: 0;
  line-height: 1.6;
}

.gauge {
  display: block;
  width: 104px;
  height: 6px;
  border-radius: 999px;
  background: var(--panel-raise);
  overflow: hidden;
}

.gauge.wide {
  width: 100%;
  height: 8px;
  margin-top: 10px;
}

.gauge i {
  display: block;
  height: 100%;
  border-radius: 999px;
}

i.tone-up {
  background: var(--rise);
}

i.tone-down {
  background: var(--fall);
}

i.tone-accent {
  background: var(--accent);
}

.tone-up {
  color: var(--rise);
}

.tone-down {
  color: var(--fall);
}

.tone-accent {
  color: var(--accent);
}

.tone-muted {
  color: var(--muted);
}

.gauge-scale {
  position: relative;
  height: 18px;
  margin: 4px 0 0;
  font-size: 12px;
}

.gauge-scale span {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
}

.gauge-scale span:first-child {
  transform: none;
}

.gauge-scale span:last-child {
  transform: translateX(-100%);
}

:deep(.el-radio-group) {
  --el-radio-button-checked-bg-color: var(--accent-soft);
  --el-radio-button-checked-text-color: var(--text-strong);
  --el-radio-button-checked-border-color: var(--accent);
}

.strength-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.strength-num {
  font-size: 32px;
  font-weight: 600;
  color: var(--text-strong);
}

.strength-label {
  font-size: 15px;
  font-weight: 600;
}

.fine {
  margin: 10px 0 0;
  line-height: 1.6;
}

.gate {
  display: grid;
  gap: 8px;
}

.gate p {
  margin: 0;
}

.gate-link {
  font-size: 13px;
  text-decoration: none;
}

.cell-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 2px;
  font-weight: 600;
  color: var(--text-strong);
}

.cell-sub {
  margin: 0;
  line-height: 1.6;
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

.chart-note {
  margin: 6px 0 0;
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
  font-size: 13px;
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
  margin-bottom: 10px;
}

.draft :deep(.el-form-item__label) {
  font-size: 13px;
  color: var(--muted);
}

.draft-pair {
  gap: 10px;
}

.draft-foot {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
}

.draft-foot :deep(.el-form-item) {
  margin-bottom: 0;
}

.point-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 12px;
  font-size: 13px;
}

.point-list p {
  margin: 0 0 4px;
}

.fact-list {
  margin: 0 0 6px;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.fact-list li {
  border-left: 2px solid var(--line);
  padding-left: 10px;
}

.fact-line {
  margin: 0;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
}

.fact-line strong {
  color: var(--text-strong);
  font-weight: 600;
}

.fact-meta {
  margin: 2px 0 0;
}

.fact-meta a {
  margin-left: 6px;
}

.strong {
  color: var(--text-strong);
  font-weight: 600;
}

.attach-select {
  width: 200px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 10px 16px 4px;
}
</style>
