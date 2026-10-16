<script setup lang="ts">
/**
 * 研究报告中心（效果图 9）。
 *
 * 接口（只读，一个都不新增）：
 *  - GET /api/v1/analysis/reports?q=    报告列表，每行只有 report_id / run_id / title / status / as_of
 *  - GET /api/v1/analysis/runs/{runId}  报告正文，返回 title / body / message / notice / status
 *  - GET /api/v1/analysis/model-status  模型是否已配置（未配置时把原因原样显示）
 *  - GET /api/v1/instruments/search     新建研究任务的入口：搜到证券后跳到该公司的分析页
 *
 * 列表接口不返回报告类型、行业、作者与置信度，所以：
 *  - 页签、报告类型、行业三个筛选不伪造分类，统一显示「全部」，并在 ⓘ 里写明后端没有这些字段；
 *  - 时间范围与排序用真实的 as_of 字段本地过滤；
 *  - 卡片右侧不画「92% 高可信度」这种圆环，后端没有 confidence，画了就是编数据。
 */
import { computed, onMounted, ref, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { authorizedFetch, readJson } from "../api/client"
import { searchInstruments } from "../api/instruments"
import { asString, dataOf, listOf } from "../api/json"
import { modelNotice } from "../api/modelStatus"
import { evidenceLinks, markdownFilename, type EvidenceLink } from "../api/reportEvidence"
import { restoreSession, useSessionStore } from "../stores/auth"
import { VChart } from "../charts"
import MetricTile from "../components/ui/MetricTile.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const session = useSessionStore()
const route = useRoute()
const router = useRouter()

interface ReportRow {
  reportId: string
  runId: string
  title: string
  status: string
  asOf: string
}

interface MetricSide {
  period: string
  value: number | null
  text: string
}

interface MetricRow {
  key: string
  label: string
  unit: string
  earlier: MetricSide
  later: MetricSide
  change: string
  reason: string
  ids: string[]
  sourceUrl: string
}

interface BodyBlock {
  kind: "section" | "text"
  text: string
  metric: MetricRow | null
}

const tabs = [
  { key: "all", label: "全部报告" },
  { key: "company", label: "公司报告" },
  { key: "macro", label: "宏观报告" },
  { key: "industry", label: "产业报告" },
]

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const modelMessage = ref("")
const modelAvailable = ref<boolean | null>(null)

const query = ref("")
const tab = ref("all")
const rangeText = ref("全部时间")
const sortText = ref("最新发布")

const reports = ref<ReportRow[]>([])
const selectedRunId = ref("")

const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const detailError = ref("")
const detail = ref<{ runId: string; title: string; status: string; notice: string; body: string }>({
  runId: "",
  title: "",
  status: "",
  notice: "",
  body: "",
})

const taskQuery = ref("")
const taskError = ref("")
const taskBusy = ref(false)

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再查看研究报告"
    return
  }
  try {
    await Promise.all([loadModelStatus(), loadReports()])
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadModelStatus(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/analysis/model-status",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  if (!response.ok) {
    return
  }
  const data = dataOf(await readJson(response))
  const available = data?.available === true
  modelAvailable.value = available
  modelMessage.value = modelNotice(asString(data?.message), available)
}

async function loadReports(): Promise<void> {
  const response = await authorizedFetch(
    `/api/v1/analysis/reports?q=${encodeURIComponent(query.value.trim())}`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  reports.value = listOf(dataOf(payload)?.reports).flatMap(readReport)
  const wanted = deepLinkRunId()
  const next = wanted || reports.value[0]?.runId || ""
  if (next) {
    await selectReport(next)
  } else {
    selectedRunId.value = ""
    detailPhase.value = "idle"
  }
}

function readReport(value: Record<string, unknown>): ReportRow[] {
  const reportId = asString(value.report_id)
  const runId = asString(value.run_id)
  const title = asString(value.title)
  if (!reportId || !runId || !title) {
    return []
  }
  return [
    {
      reportId,
      runId,
      title,
      status: asString(value.status) ?? "pending",
      asOf: asString(value.as_of) ?? "",
    },
  ]
}

/** /reports/:runId 深链接：进了这条路由就直接预览对应报告。 */
function deepLinkRunId(): string {
  return typeof route.params.runId === "string" ? route.params.runId : ""
}

async function selectReport(runId: string): Promise<void> {
  selectedRunId.value = runId
  detailPhase.value = "loading"
  detailError.value = ""
  try {
    const response = await authorizedFetch(
      `/api/v1/analysis/runs/${runId}`,
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const data = dataOf(payload)
    if (!response.ok || !data) {
      detailError.value = asString(dataOf(dataOf(payload)?.error)?.message) ?? "找不到这份研究报告"
      detailPhase.value = "error"
      return
    }
    detail.value = {
      runId: asString(data.run_id) ?? runId,
      title: asString(data.title) ?? "研究报告",
      status: asString(data.status) ?? "",
      notice: asString(data.notice) ?? "",
      body: asString(data.body) ?? "",
    }
    detailPhase.value = "ready"
  } catch {
    detailError.value = "无法连接接口"
    detailPhase.value = "error"
  }
}

/* ------------------------------------------------------------------ 筛选 */

/** 后端只给了 as_of，时间范围按它本地过滤。 */
function withinRange(row: ReportRow): boolean {
  if (rangeText.value === "全部时间") {
    return true
  }
  const days = rangeText.value === "近一个月" ? 31 : rangeText.value === "近三个月" ? 92 : 366
  const at = Date.parse(`${row.asOf}T00:00:00Z`)
  if (Number.isNaN(at)) {
    return true
  }
  return Date.now() - at <= days * 24 * 60 * 60 * 1000
}

const visibleReports = computed<ReportRow[]>(() => {
  const rows = reports.value.filter(withinRange)
  if (sortText.value === "最早发布") {
    return [...rows].reverse()
  }
  if (sortText.value === "标题") {
    return [...rows].sort((left, right) => left.title.localeCompare(right.title, "zh-Hans-CN"))
  }
  return rows
})

function displayDate(value: string): string {
  return value || "—"
}

const selectedRow = computed<ReportRow | null>(
  () => reports.value.find((row) => row.runId === selectedRunId.value) ?? null,
)

const selectedDate = computed<string>(() => selectedRow.value?.asOf ?? "")

const bodySize = computed<number>(() => detail.value.body.length)

/* ------------------------------------------------------------ 正文解析 */

const GROUPS = new Set([
  "早期",
  "后期",
  "变化",
  "事实",
  "来源",
  "申报号",
  "暂无",
  "无法计算",
  "报告期",
  "与",
])

const FIELD = /^(早期|后期) (\S+) (?:申报号|来源) (\S+)$/

/** 正文里的「概念 单位 早期 x 申报号 y 来源 z 事实 id … 变化 n」逐行解析成可核对的指标行。 */
function parseMetrics(body: string): MetricRow[] {
  const rows: MetricRow[] = []
  for (const raw of body.split(/\r?\n/)) {
    const parts = raw.trim().split(/\s+/)
    if (parts.length < 3 || GROUPS.has(parts[0])) {
      continue
    }
    const unitAt = parts.findIndex((part) => /^[A-Za-z][A-Za-z0-9_/%-]*$/.test(part))
    if (unitAt < 1) {
      continue
    }
    const label = parts.slice(0, unitAt).join(" ")
    const later = readSide(parts.slice(unitAt + 2).join(" "), "后期")
    const earlier = readSide(parts.slice(unitAt + 1).join(" "), "早期")
    if (!later.value && !earlier.value && !later.text && !earlier.text) {
      continue
    }
    const reasonAt = raw.indexOf("变化原因")
    rows.push({
      key: `${label}-${rows.length}`,
      label,
      unit: parts[unitAt],
      earlier,
      later,
      // 「变化 n」在「事实 <id>」之后、可选的「变化原因 …」之前。
      change:
        raw
          .slice(0, reasonAt > 0 ? reasonAt : raw.length)
          .match(/变化\s+(\S+)/)?.[1] ?? "无法计算",
      reason: reasonAt > 0 ? raw.slice(reasonAt + 4).trim() : "",
      ids: [...raw.matchAll(/事实\s*(\S+)/g)].map((match) => match[1]),
      sourceUrl: [...raw.matchAll(/来源\s*(\S+)/g)].map((match) => match[1]).find(Boolean) ?? "",
    })
  }
  return rows
}

function readSide(chunk: string, label: string): MetricSide {
  const match = FIELD.exec(chunk)
  if (!match || match[1] !== label) {
    return { period: "", value: null, text: "" }
  }
  const numeric = match[2] === "暂无" ? null : Number(match[2])
  return {
    period: match[3],
    value: numeric !== null && Number.isFinite(numeric) ? numeric : null,
    text: match[2],
  }
}

/** 正文没有小标题，标题行与指标行靠与解析结果比对区分；比对不上的行按原文当标题渲染。 */
function bodyBlocks(text: string): BodyBlock[] {
  const metrics = parseMetrics(text)
  const used = new Set<string>()
  const blocks: BodyBlock[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) {
      continue
    }
    const hit =
      metrics.find((metric) => !used.has(metric.key) && line.startsWith(metric.label)) ?? null
    if (hit) {
      used.add(hit.key)
      blocks.push({ kind: "text", text: line, metric: hit })
      continue
    }
    blocks.push({ kind: "section", text: line, metric: null })
  }
  return blocks
}

const bodyMetrics = computed<MetricRow[]>(() => parseMetrics(detail.value.body))

const evidence = computed<EvidenceLink[]>(() => evidenceLinks(detail.value.body))

/** 至少两个指标同时有早期与后期数值才画柱状图，否则按规范只留空态说明。 */
const chartOption = computed(() => {
  const rows = bodyMetrics.value
    .filter((metric) => metric.earlier.value !== null && metric.later.value !== null)
    .slice(0, 8)
  if (rows.length < 2) {
    return null
  }
  return {
    grid: { left: 56, right: 16, top: 34, bottom: 26 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#121e30",
      borderColor: "rgba(231,237,248,0.12)",
      textStyle: { color: "#e7edf8", fontSize: 12 },
    },
    legend: {
      top: 0,
      icon: "circle",
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: "#95a6be", fontSize: 11 },
    },
    xAxis: {
      type: "category",
      data: rows.map((metric) => metric.label),
      axisLabel: { color: "#95a6be", fontSize: 11 },
      axisLine: { lineStyle: { color: "rgba(231,237,248,0.12)" } },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: "#95a6be", fontSize: 11 },
      splitLine: { lineStyle: { color: "rgba(231,237,248,0.06)" } },
    },
    series: [
      {
        name: `早期（${rows[0].earlier.period}）`,
        type: "bar",
        barMaxWidth: 22,
        itemStyle: { color: "#3b82f6" },
        data: rows.map((metric) => metric.earlier.value),
      },
      {
        name: `后期（${rows[0].later.period}）`,
        type: "bar",
        barMaxWidth: 22,
        itemStyle: { color: "#95a6be" },
        data: rows.map((metric) => metric.later.value),
      },
    ],
  }
})

const metricTiles = computed(() => bodyMetrics.value.slice(0, 4))

/* ------------------------------------------------------------------ 操作 */

function downloadReport(): void {
  const file = new Blob([detail.value.body], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(file)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = markdownFilename(detail.value.title)
  anchor.click()
  URL.revokeObjectURL(url)
}

async function copyReference(): Promise<void> {
  const link = `${window.location.origin}/reports/${detail.value.runId}`
  try {
    await navigator.clipboard.writeText(link)
    message.value = `已复制报告链接：${link}`
  } catch {
    message.value = `复制失败，链接为 ${link}`
  }
}

/** 新建研究任务：先搜到真实证券，再跳到该公司的分析入口。后端只有公司维度能生成记录。 */
async function createTask(): Promise<void> {
  const text = taskQuery.value.trim()
  taskError.value = ""
  if (!text) {
    taskError.value = "请输入证券代码或名称"
    return
  }
  taskBusy.value = true
  try {
    const found = await searchInstruments(text)
    const hit = found.results[0]
    if (!hit) {
      taskError.value = found.emptyMessage ?? "暂无该证券"
      return
    }
    const code = encodeURIComponent(hit.displaySymbol || hit.canonicalCode)
    await router.push(hit.market === "CN" ? `/company/${code}/filings` : `/company/${code}`)
  } catch {
    taskError.value = "无法连接接口"
  } finally {
    taskBusy.value = false
  }
}

function runSearch(): void {
  void loadReports()
}

watch(
  () => route.params.runId,
  (value) => {
    if (typeof value === "string" && value && value !== selectedRunId.value) {
      void selectReport(value)
    }
  },
)

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page stack">
    <PageHeader
      title="研究报告"
      subtitle="基于AI深度分析，生成专业、及时、可靠的研究报告，助力您的投资决策"
    >
      <template #actions>
        <div class="task-row">
          <el-input
            v-model="taskQuery"
            size="small"
            class="task-search"
            placeholder="证券代码或名称，例如 AAPL"
            @keyup.enter="createTask"
          />
          <el-button type="primary" :loading="taskBusy" @click="createTask">
            新建研究任务
          </el-button>
        </div>
      </template>
      <template #tabs>
        <SectionTabs :tabs="tabs" v-model="tab" />
      </template>
      <template #filters>
        <div class="filter-row">
          <el-input
            v-model="query"
            class="filter-search"
            clearable
            placeholder="搜索报告标题、关键词、公司或行业…"
            @keyup.enter="runSearch"
            @clear="runSearch"
          >
            <template #append>
              <el-button @click="runSearch">搜索</el-button>
            </template>
          </el-input>
          <span
            class="filter-locked"
            title="列表接口没有报告类型字段，做不了真筛选，这里不伪造分类。"
          >
            <span class="muted">报告类型</span>
            <el-select model-value="全部" disabled class="filter-select">
              <el-option label="全部" value="全部" />
            </el-select>
          </span>
          <span class="filter-locked" title="列表接口没有行业字段，做不了真筛选。">
            <span class="muted">行业领域</span>
            <el-select model-value="全部" disabled class="filter-select">
              <el-option label="全部" value="全部" />
            </el-select>
          </span>
          <span class="filter-locked">
            <span class="muted">时间范围</span>
            <el-select v-model="rangeText" class="filter-select">
              <el-option label="全部时间" value="全部时间" />
              <el-option label="近一个月" value="近一个月" />
              <el-option label="近三个月" value="近三个月" />
              <el-option label="近一年" value="近一年" />
            </el-select>
          </span>
          <span class="filter-locked">
            <span class="muted">排序方式</span>
            <el-select v-model="sortText" class="filter-select">
              <el-option label="最新发布" value="最新发布" />
              <el-option label="最早发布" value="最早发布" />
              <el-option label="标题" value="标题" />
            </el-select>
          </span>
        </div>
      </template>
    </PageHeader>

    <el-alert
      v-if="modelMessage"
      :type="modelAvailable ? 'info' : 'warning'"
      :closable="false"
      show-icon
      :title="modelMessage"
    />

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="message" @retry="load" />

    <div v-else class="grid-1-2">
      <!-- 左：报告列表 -->
      <PanelCard
        :title="`共 ${visibleReports.length} 篇报告`"
        info="列表接口只返回标题、状态与数据日期；报告类型、行业、作者与置信度后端没有这些字段。"
        compact
      >
        <StateBlock
          v-if="!visibleReports.length"
          state="empty"
          :message="
            reports.length
              ? '当前筛选条件下没有报告，换一个时间范围再试。'
              : '还没有研究记录。研究记录由公司页触发「整理财报研究记录」后生成，这里不会显示演示数据。'
          "
        />

        <ul v-else class="report-list">
          <li
            v-for="row in visibleReports"
            :key="row.reportId"
            class="report-item"
            :class="{ active: row.runId === selectedRunId }"
          >
            <button type="button" class="report-hit" @click="selectReport(row.runId)">
              <span class="thumb" aria-hidden="true">{{ row.title.slice(0, 2) }}</span>
              <span class="report-main">
                <span class="report-title">{{ row.title }}</span>
                <span class="report-tags">
                  <StatusPill text="研究记录" tone="accent" />
                  <StatusPill text="全市场" tone="muted" />
                  <StatusPill text="已入库数字" tone="muted" />
                </span>
                <span class="report-meta muted">
                  后端整理 · 数据日期 {{ displayDate(row.asOf) }}
                </span>
              </span>
              <span class="report-side">
                <StatusPill
                  :text="
                    row.status === 'succeeded'
                      ? '整理完成'
                      : row.status === 'failed'
                        ? '整理失败'
                        : row.status
                  "
                  :tone="
                    row.status === 'succeeded' ? 'ok' : row.status === 'failed' ? 'danger' : 'muted'
                  "
                />
                <span class="chevron" aria-hidden="true">›</span>
              </span>
            </button>
          </li>
        </ul>
      </PanelCard>

      <!-- 右：报告预览 -->
      <PanelCard
        :title="detailPhase === 'ready' ? detail.title : '报告预览'"
        :info="detail.notice"
        compact
      >
        <template #actions>
          <div v-if="detailPhase === 'ready'" class="detail-actions">
            <el-button size="small" @click="copyReference">分享</el-button>
            <el-button size="small" type="primary" @click="downloadReport">下载报告</el-button>
          </div>
        </template>

        <StateBlock v-if="detailPhase === 'loading'" state="loading" :rows="5" />
        <StateBlock
          v-else-if="detailPhase === 'error'"
          state="error"
          :message="detailError"
          @retry="selectReport(selectedRunId)"
        />
        <StateBlock
          v-else-if="detailPhase === 'idle'"
          state="empty"
          message="左侧还没有可预览的报告。"
        />

        <div v-else class="detail">
          <div class="cover">
            <span class="cover-badge">研究记录</span>
            <h3>{{ detail.title }}</h3>
            <p class="muted cover-meta">
              数据日期 {{ displayDate(selectedDate) }} · 正文 {{ bodySize }} 字符 · 状态
              {{ detail.status || "—" }}
            </p>
          </div>

          <PanelCard title="核心观点" compact>
            <StateBlock
              v-if="!metricTiles.length"
              state="empty"
              message="这份正文还没有逐条的已入库数字对比，重新整理一次财报研究记录后这里会显示可核对的指标。"
            />
            <div v-else class="grid-2">
              <MetricTile
                v-for="tile in metricTiles"
                :key="tile.key"
                :label="tile.label"
                :value="tile.later.value"
                :period="tile.later.period"
                :unit="tile.unit"
              />
            </div>
            <p v-if="evidence.length" class="muted metric-note">
              这份正文带 {{ evidence.length }} 条可核对的申报数字，正文里逐条列出了申报号与来源。
            </p>
          </PanelCard>

          <PanelCard title="正文" compact>
            <template v-if="!detail.body">
              <StateBlock
                state="empty"
                message="这份记录没有存正文。后端只保存了执行结果，正文为空时页面不会用模板文字填充。"
              />
            </template>
            <template v-else>
              <template v-for="(block, index) in bodyBlocks(detail.body)" :key="index">
                <template v-if="block.metric">
                  <h4 class="doc-section">{{ block.metric.label }}</h4>
                  <p class="doc-text metric-line">
                    早期 {{ block.metric.earlier.text || "暂无" }}（{{ block.metric.earlier.period || "—" }}）
                    → 后期 {{ block.metric.later.text || "暂无" }}（{{ block.metric.later.period || "—" }}）
                    <span class="muted"> · 变化 {{ block.metric.change || "无法计算" }}</span>
                  </p>
                </template>
                <h3 v-else class="doc-section">{{ block.text }}</h3>
              </template>

              <VChart
                v-if="chartOption"
                class="report-chart"
                :option="chartOption"
                autoresize
              />
              <p v-else class="muted metric-note">
                可对比的指标不足两个，按规范只给数字，不画曲线。
              </p>

              <ul v-if="evidence.length" class="doc-list evidence">
                <li v-for="link in evidence" :key="link.key">
                  {{ link.label }} · 申报号 {{ link.accession }}
                  <a :href="link.sourceUrl" target="_blank" rel="noreferrer noopener">来源</a>
                </li>
              </ul>
            </template>
          </PanelCard>
        </div>
      </PanelCard>
    </div>

    <SourceFooter
      :as-of="displayDate(selectedDate)"
      :sources="['本地样例申报事实（SEC accession）', '后端规则整理']"
      :note="modelMessage || '正文由后端按已入库数字拼装，页面只做解析与展示。'"
    />
  </main>
</template>

<style scoped>
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.filter-search {
  width: 360px;
}

.filter-locked {
  pointer-events: auto;
}

.filter-select {
  width: 150px;
}

.report-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.report-hit {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel-soft);
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.report-item.active .report-hit {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.report-hit:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
}

.thumb {
  flex: 0 0 56px;
  width: 56px;
  height: 40px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--panel-raise), var(--panel-deep));
  color: var(--muted);
  font-size: 12px;
  border: 1px solid var(--line-soft);
}

.report-main {
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  gap: 4px;
}

.report-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.45;
}

.report-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.report-meta {
  font-size: 12px;
}

.report-side {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.chevron {
  color: var(--muted);
  font-size: 18px;
}

.detail {
  display: grid;
  gap: 14px;
}

.detail-actions {
  display: flex;
  gap: 8px;
}

.cover {
  border-radius: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  background: linear-gradient(120deg, var(--panel-raise), var(--panel-deep));
}

.cover h3 {
  margin: 8px 0 6px;
  font-size: 18px;
  font-weight: 700;
}

.cover-badge {
  display: inline-flex;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
}

.cover-meta {
  margin: 0;
  font-size: 12px;
}

.metric-note {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
}

.metric-line {
  margin-bottom: 0;
}

.report-chart {
  height: 260px;
  margin: 4px 0 12px;
}

.evidence a {
  margin-left: 6px;
  font-size: 13px;
}
</style>
