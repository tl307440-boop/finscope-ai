<script setup lang="ts">
/**
 * 单份研究报告（路由 /reports/:runId），复用报告中心的详情预览表达。
 *
 * 接口（与改造前一致，未新增）：
 *  - GET /api/v1/analysis/runs/{runId}   报告正文（title / status / body / notice / as_of）
 *  - GET /api/v1/analysis/model-status   模型是否已配置，未配置时把后端给的原因原样显示
 *
 * 正文是后端按已入库申报数字拼装的纯文本，没有 markdown 小标题；
 * 这里逐行解析成「指标行 + 其余原文」，只有解析出两对以上 早期/后期 数值才画柱状图，
 * 解析不出来就按原文渲染，不编造观点、评级或置信度。
 */
import { computed, onMounted, ref } from "vue"
import { useRoute } from "vue-router"
import { authorizedFetch, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import { evidenceLinks, markdownFilename, type EvidenceLink } from "../api/reportEvidence"
import { restoreSession, useSessionStore } from "../stores/auth"
import { VChart } from "../charts"
import MetricTile from "../components/ui/MetricTile.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const session = useSessionStore()
const route = useRoute()

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
}

interface BodyBlock {
  kind: "metric" | "raw"
  text: string
  metric: MetricRow | null
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const title = ref("")
const status = ref("")
const body = ref("")
const notice = ref("")
const asOf = ref("")
const links = ref<EvidenceLink[]>([])
const selected = ref<EvidenceLink | null>(null)
const modelAvailable = ref<boolean | null>(null)
const modelMessage = ref("")

/* 后端把每行数字拼成「概念 单位 早期 x 申报号 y 来源 z 事实 id … 变化 n」，按这个形状解析。 */
const groups = new Set([
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

const fieldPattern = /^(早期|后期) (\S+) (?:申报号|来源) (\S+)$/

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
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
  const data = asRecord(asRecord(await readJson(response))?.data)
  modelAvailable.value = data?.available === true
  modelMessage.value = asString(data?.message) ?? ""
}

async function load(): Promise<void> {
  phase.value = "loading"
  const runId = typeof route.params.runId === "string" ? route.params.runId : ""
  if (!session.accessToken || !runId) {
    phase.value = "error"
    message.value = "请先登录后再查看研究记录"
    return
  }
  try {
    await loadModelStatus()
    const response = await authorizedFetch(
      `/api/v1/analysis/runs/${runId}`,
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const payload = asRecord(await readJson(response))
    const data = asRecord(payload?.data)
    const meta = asRecord(payload?.meta)
    if (!response.ok || !data) {
      const error = asRecord(payload?.error)
      message.value = asString(error?.message) ?? "找不到这份研究记录"
      phase.value = "error"
      return
    }
    title.value = asString(data.title) ?? "研究记录"
    status.value = asString(data.status) ?? ""
    body.value = asString(data.body) ?? ""
    notice.value = asString(data.notice) ?? ""
    asOf.value = asString(data.as_of) ?? asString(meta?.as_of) ?? ""
    links.value = evidenceLinks(body.value)
    selected.value = null
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

function readSide(chunk: string, label: string): MetricSide {
  const match = fieldPattern.exec(chunk)
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

function parseMetrics(text: string): MetricRow[] {
  const rows: MetricRow[] = []
  for (const raw of text.split(/\r?\n/)) {
    const parts = raw.trim().split(/\s+/)
    if (parts.length < 3 || groups.has(parts[0])) {
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
      change:
        raw
          .slice(0, reasonAt > 0 ? reasonAt : raw.length)
          .match(/变化\s+(\S+)/)?.[1] ?? "无法计算",
      reason: reasonAt > 0 ? raw.slice(reasonAt + 4).trim() : "",
    })
  }
  return rows
}

const metricRows = computed<MetricRow[]>(() => parseMetrics(body.value))

const pairedRows = computed<MetricRow[]>(() =>
  metricRows.value.filter((row) => row.earlier.value !== null && row.later.value !== null),
)

/** 正文没有小标题，标题行与指标行靠解析结果比对区分；比对不上的行按原文当小标题渲染。 */
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
      blocks.push({ kind: "metric", text: line, metric: hit })
      continue
    }
    blocks.push({ kind: "raw", text: line, metric: null })
  }
  return blocks
}

const chartOption = computed(() => {
  const rows = pairedRows.value.slice(0, 8)
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
      data: rows.map((row) => row.label),
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
        name: `早期 ${rows[0].earlier.period}`,
        type: "bar",
        barMaxWidth: 22,
        itemStyle: { color: "#3b82f6" },
        data: rows.map((row) => row.earlier.value),
      },
      {
        name: `后期 ${rows[0].later.period}`,
        type: "bar",
        barMaxWidth: 22,
        itemStyle: { color: "#95a6be" },
        data: rows.map((row) => row.later.value),
      },
    ],
  }
})

function printReport(): void {
  window.print()
}

function downloadMarkdown(): void {
  const file = new Blob([body.value], { type: "text/markdown;charset=utf-8" })
  const url = URL.createObjectURL(file)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = markdownFilename(title.value)
  anchor.click()
  URL.revokeObjectURL(url)
}

async function copyReference(): Promise<void> {
  const link = `${window.location.origin}/reports/${String(route.params.runId ?? "")}`
  try {
    await navigator.clipboard.writeText(link)
    message.value = `已复制报告链接：${link}`
  } catch {
    message.value = `复制不可用，链接为 ${link}`
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page stack">
    <PageHeader :title="title || '研究记录'">
      <template #actions>
        <div class="head-actions">
          <StatusPill
            v-if="status"
            :text="status === 'succeeded' ? '整理完成' : status === 'failed' ? '整理失败' : status"
            :tone="status === 'succeeded' ? 'ok' : status === 'failed' ? 'danger' : 'muted'"
          />
          <StatusPill
            v-if="modelAvailable !== null"
            :text="modelAvailable ? '模型已配置' : '模型未配置'"
            :tone="modelAvailable ? 'ok' : 'warn'"
            dot
          />
          <el-button size="small" @click="copyReference">分享</el-button>
          <el-button size="small" @click="printReport">打印为 PDF</el-button>
          <el-button size="small" type="primary" @click="downloadMarkdown">下载报告</el-button>
        </div>
      </template>
    </PageHeader>

    <el-alert
      v-if="modelMessage"
      :type="modelAvailable ? 'info' : 'warning'"
      title="模型状态"
      :description="modelMessage"
      :closable="false"
      show-icon
    />

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" message="正在读取研究记录…" />
    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取研究记录"
      :message="message"
      @retry="load"
    />

    <div v-else class="grid-1-2">
      <!-- 左：来源与核对（沿用改造前的证据抽屉，改成可点列表 + 详情） -->
      <PanelCard title="数字来源" info="规格书 §1.4：财报引用要给出可点的来源 URL">
        <template #actions>
          <StatusPill :text="`${links.length} 条`" tone="muted" />
        </template>

        <StateBlock
          v-if="!links.length"
          state="empty"
          message="这份记录没有可核对的申报数字：正文里未出现「早期/后期 … 申报号 … 来源」形式的已入库事实。"
        />
        <ul v-else class="evidence-list">
          <li v-for="link in links" :key="link.key" :class="{ active: selected?.key === link.key }">
            <button class="evidence-main" type="button" @click="selected = link">
              <span class="evidence-label">{{ link.label }}</span>
            </button>
            <a
              class="source-link"
              :href="link.sourceUrl"
              target="_blank"
              rel="noreferrer noopener"
            >来源</a>
          </li>
        </ul>

        <aside v-if="selected" class="evidence-drawer" role="dialog" aria-label="数字来源">
          <header>
            <strong>{{ selected.label }}</strong>
            <button class="text-button" type="button" @click="selected = null">关闭</button>
          </header>
          <dl>
            <div>
              <dt>数值</dt>
              <dd>{{ selected.value }}</dd>
            </div>
            <div>
              <dt>申报号</dt>
              <dd>{{ selected.accession }}</dd>
            </div>
          </dl>
          <p class="source-row">
            来源
            <a
              class="source-link"
              :href="selected.sourceUrl"
              target="_blank"
              rel="noreferrer noopener"
            >{{ selected.sourceUrl }}</a>
          </p>
        </aside>
      </PanelCard>

      <!-- 右：封面 + 核心观点 + 正文（与报告中心的预览同一套表达） -->
      <PanelCard title="报告预览">
        <div class="cover">
          <span class="cover-badge">研究记录</span>
          <h3>{{ title || "研究记录" }}</h3>
          <p class="cover-meta muted">
            {{ status === "succeeded" ? "整理完成" : status || "状态未知" }}
            · 数据时点 {{ asOf || "—" }}
            · 正文 {{ body.length }} 字符
            · 可对比指标 {{ pairedRows.length }} 项
          </p>
        </div>

        <PanelCard title="核心观点" compact>
          <StateBlock
            v-if="!metricRows.length"
            state="empty"
            message="正文里没有逐条的已入库数字对比，此处不填占位指标。"
          />
          <div v-else class="grid-2">
            <MetricTile
              v-for="row in metricRows.slice(0, 4)"
              :key="row.key"
              :label="row.label"
              :value="row.later.text || null"
              :period="row.later.period"
              :unit="row.unit"
              :info="`早期 ${row.earlier.text || '暂无'}（${row.earlier.period || '—'}）`"
            />
          </div>
        </PanelCard>

        <h3 class="doc-section">正文</h3>
        <StateBlock
          v-if="!body"
          state="empty"
          message="这份记录没有存正文。后端只保存了执行结果，正文为空时不生成模板文字。"
        />
        <template v-else>
          <p v-if="notice" class="muted metric-note">{{ notice }}</p>
          <template v-for="(block, index) in bodyBlocks(body)" :key="index">
            <template v-if="block.metric">
              <h4 class="doc-section">{{ block.metric.label }}</h4>
              <p class="doc-text metric-line">
                早期 {{ block.metric.earlier.text || "暂无" }}{{ block.metric.unit }}（{{ block.metric.earlier.period || "—" }}）
                → 后期 {{ block.metric.later.text || "暂无" }}{{ block.metric.unit }}（{{ block.metric.later.period || "—" }}）
                <span class="muted"> · 变化 {{ block.metric.change }}</span>
              </p>
              <p v-if="block.metric.reason" class="doc-text muted">
                变化原因：{{ block.metric.reason }}
              </p>
            </template>
            <h3 v-else class="doc-section">{{ block.text }}</h3>
          </template>

          <VChart v-if="chartOption" class="report-chart" :option="chartOption" autoresize />
          <p v-else class="muted metric-note">
            可对比的指标不足两个，按规范只列数字，不画曲线。
          </p>
        </template>
      </PanelCard>
    </div>

    <SourceFooter
      :as-of="asOf"
      :sources="['SEC 申报事实（accession）', '后端规则整理']"
      :note="notice"
    />
  </main>
</template>

<style scoped>
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.cover {
  border-radius: var(--radius);
  padding: 16px;
  margin-bottom: 14px;
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

.metric-line {
  margin-bottom: 6px;
}

.metric-note {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
}

.report-chart {
  width: 100%;
  height: 280px;
  margin-top: 12px;
}

.evidence-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.evidence-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line-soft);
  border-radius: var(--radius-sm);
}

.evidence-list li.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.evidence-main {
  flex: 1;
  min-width: 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.evidence-label {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-drawer {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-soft);
  display: grid;
  gap: 10px;
}

.evidence-drawer header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.evidence-drawer dl {
  display: grid;
  gap: 6px;
  margin: 0;
}

.evidence-drawer dl div {
  display: flex;
  gap: 8px;
  font-size: 13px;
}

.evidence-drawer dt {
  color: var(--muted);
  min-width: 56px;
}

.evidence-drawer dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
}

.source-row {
  margin: 0;
  font-size: 13px;
  word-break: break-all;
}

/* 规格书 §1.4 的财报引用要给出可点的「来源 URL」，§1.3 的报告页要求 PDFDeepLink。 */
.source-link {
  margin-left: 8px;
  color: var(--accent);
  font-size: 13px;
}
</style>
