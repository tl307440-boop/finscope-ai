<script setup lang="ts">
/**
 * AI 智能投研助手（效果图 8）。
 *
 * 所有内容都来自后端真实返回，界面不会补一段假的分析正文：
 *  - GET  /api/v1/assistant/sessions                          问答列表（取最近一条，没有就新建）
 *  - POST /api/v1/assistant/sessions                          新建问答
 *  - GET  /api/v1/assistant/sessions/{id}/messages            历史消息（role/content/completion_status）
 *  - POST /api/v1/assistant/sessions/{id}/messages            提问，SSE 逐段回传 {"delta"} / {"done"}
 *  - POST /api/v1/assistant/sessions/{id}/messages/{cid}/cancel  取消未完成的回答
 *  - GET  /api/v1/analysis/model-status                       模型是否已配置（未配置时把原因原样显示）
 *  - GET  /api/v1/watchlists / POST /api/v1/watchlists        自选分组
 *  - GET  /api/v1/instruments/search                          加入自选时解析代码
 *  - GET  /api/v1/market/bars/{instrument_id}                 自选股最新价与涨跌幅
 *  - GET  /api/v1/macro/series[/{code}/observations]          宏观指标与迷你图
 *  - GET  /api/v1/documents                                   我上传的文档
 *
 * 没有 LLM key 时后端 rules_reply 只回一句「未配置模型…」，正文就是那一句；
 * 工具调用、参考来源、相关图表都按接口实际返回渲染，取不到就显示对应空态。
 */
import { computed, nextTick, onMounted, ref } from "vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { asNumber, asString, dataOf, formatMoment, listOf } from "../api/json"
import { modelNotice } from "../api/modelStatus"
import { VChart } from "../charts"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface Turn {
  id: string
  clientMessageId: string
  role: string
  content: string
  completionStatus: string
}

interface StockRow {
  instrumentId: string
  symbol: string
  nameCn: string
  latestClose: number | null
  latestDate: string
  simpleReturn: number | null
}

interface DocRow {
  id: string
  title: string
  parseStatus: string
  uploadedAt: string
}

interface MacroTile {
  code: string
  label: string
  unit: string
  value: number | null
  observationDate: string
  spark: number[]
}

interface SeriesLine {
  name: string
  dates: string[]
  values: number[]
}

interface BodyBlock {
  kind: "section" | "text"
  text: string
}

interface TextPart {
  cite: boolean
  text: string
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const question = ref("")
const sessionId = ref("")
const sessionTitle = ref("")
const turns = ref<Turn[]>([])
const sending = ref(false)
const streaming = ref(false)
const activeClientId = ref("")
const errorText = ref("")
const threadEl = ref<HTMLElement | null>(null)

/** 模型状态：未配置时把后端给的原因原样显示。 */
const modelAvailable = ref<boolean | null>(null)
const modelMessage = ref("")

/** 右侧数据栏单独加载，不阻塞问答本身。 */
const dataPhase = ref<"loading" | "ready" | "error">("loading")
const dataError = ref("")
const macroNotice = ref("")
const macroTiles = ref<MacroTile[]>([])
const macroSeriesCount = ref(0)
const seriesLines = ref<SeriesLine[]>([])
const stocks = ref<StockRow[]>([])
const watchGroupId = ref("")
const symbolInput = ref("")
const stockMessage = ref("")
const docs = ref<DocRow[]>([])
const dataUpdatedAt = ref("")

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再提问"
    return
  }
  try {
    const listed = await authorizedFetch(
      "/api/v1/assistant/sessions",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const listedBody = await readJson(listed)
    const first = listOf(dataOf(listedBody)?.sessions)[0] ?? null
    const existing = asString(first?.id)
    if (existing) {
      sessionId.value = existing
      sessionTitle.value = asString(first?.title) ?? "研究问答"
    } else {
      const created = await authorizedFetch(
        "/api/v1/assistant/sessions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "研究问答" }),
        },
        session.accessToken,
        refreshToken,
      )
      const createdBody = dataOf(await readJson(created))
      sessionId.value = asString(createdBody?.id) ?? ""
      sessionTitle.value = asString(createdBody?.title) ?? "研究问答"
    }
    await loadMessages()
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadMessages(): Promise<void> {
  if (!sessionId.value || !session.accessToken) {
    return
  }
  const response = await authorizedFetch(
    `/api/v1/assistant/sessions/${sessionId.value}/messages`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  turns.value = listOf(dataOf(payload)?.messages).flatMap(readTurn)
  await scrollToLatest()
}

function readTurn(value: Record<string, unknown>): Turn[] {
  const id = asString(value.id)
  const role = asString(value.role)
  const content = asString(value.content)
  if (!id || !role || content === null) {
    return []
  }
  return [
    {
      id,
      clientMessageId: asString(value.client_message_id) ?? "",
      role,
      content,
      completionStatus: asString(value.completion_status) ?? "",
    },
  ]
}

async function scrollToLatest(): Promise<void> {
  await nextTick()
  const host = threadEl.value
  if (host) {
    host.scrollTop = host.scrollHeight
  }
}

/** 提问：POST 拿 SSE，逐段把 delta 追加到临时气泡。 */
async function ask(): Promise<void> {
  const text = question.value.trim()
  if (!text || !sessionId.value || !session.accessToken || sending.value) {
    if (!text) {
      errorText.value = "请输入问题"
    }
    return
  }
  sending.value = true
  streaming.value = true
  errorText.value = ""
  const clientMessageId = crypto.randomUUID()
  activeClientId.value = clientMessageId
  turns.value = [
    ...turns.value,
    {
      id: clientMessageId,
      clientMessageId,
      role: "user",
      content: text,
      completionStatus: "completed",
    },
    {
      id: clientMessageId,
      clientMessageId,
      role: "assistant",
      content: "",
      completionStatus: "streaming",
    },
  ]
  question.value = ""
  await scrollToLatest()
  try {
    const response = await authorizedFetch(
      `/api/v1/assistant/sessions/${sessionId.value}/messages`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_message_id: clientMessageId, content: text }),
      },
      session.accessToken,
      refreshToken,
    )
    if (!response.ok) {
      errorText.value = `无法发送：后端返回 ${response.status}`
      turns.value = turns.value.filter((turn) => turn.clientMessageId !== clientMessageId)
      return
    }
    await readStream(response)
    await loadMessages()
  } catch {
    errorText.value = "无法连接接口"
    turns.value = turns.value.filter((turn) => turn.clientMessageId !== clientMessageId)
  } finally {
    sending.value = false
    streaming.value = false
    activeClientId.value = ""
  }
}

/** 解析 SSE：逐行看 data: 后面的 JSON，delta 是正文片段，done 表示结束。 */
async function readStream(response: Response): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) {
    appendDelta(await response.text())
    return
  }
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const chunk = await reader.read()
    if (chunk.done) {
      return
    }
    buffer += decoder.decode(chunk.value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith("data:")) {
        continue
      }
      const payload = parseEvent(trimmed.slice(5).trim())
      if (!payload) {
        continue
      }
      const delta = asString(payload.delta)
      if (delta !== null) {
        appendDelta(delta)
      }
      if (payload.done === true) {
        return
      }
    }
  }
}

function parseEvent(raw: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
    return null
  } catch {
    return null
  }
}

function appendDelta(part: string): void {
  const target = turns.value.find((turn) => turn.id === activeClientId.value)
  if (!target) {
    return
  }
  target.content += part
  void scrollToLatest()
}

/**
 * 停止。后端把整段回答一次算完后才分段回传，所以「停止」只能撤销界面上这段
 * 还没落库的临时气泡；已入库的回答要取消请点气泡里的取消按钮。
 */
function stop(): void {
  const rows = turns.value
  const last = rows[rows.length - 1]
  if (last && last.role === "assistant" && last.completionStatus === "streaming") {
    turns.value = rows.slice(0, -1)
  }
  errorText.value = "已停止显示。后端是先算完整段回答再分块回传，所以停止只能撤销这段还没落库的显示。"
}

/** 重新生成：拿最近一条提问重新 POST，后端会按新的 client_message_id 存成新的一条。 */
function regenerate(): void {
  const asked = [...turns.value].reverse().find((turn) => turn.role === "user")
  if (!asked) {
    return
  }
  question.value = asked.content
  void ask()
}

const lastTurn = computed<Turn | null>(() => turns.value[turns.value.length - 1] ?? null)
const pendingClientId = computed<string>(() =>
  lastTurn.value !== null &&
  lastTurn.value.role === "assistant" &&
  lastTurn.value.completionStatus === "streaming"
    ? lastTurn.value.clientMessageId
    : "",
)

const SECTION = /^[一二三四五六七八九十]+[、.．]/
const BULLET = /^([-*•·]|\d+[.)、]|[（(]\d+[)）])\s*/

/** 把正文按行拆成小标题/段落；行首的列表符号交给模板判断。 */
function bodyBlocks(text: string): BodyBlock[] {
  const blocks: BodyBlock[] = []
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) {
      continue
    }
    blocks.push(SECTION.test(line) ? { kind: "section", text: line } : { kind: "text", text: line })
  }
  return blocks
}

function isListLine(line: string): boolean {
  return BULLET.test(line)
}

function listText(line: string): string {
  return line.replace(BULLET, "")
}

/** 正文里若带 [1]【2】这类角标就渲染成 .cite，没有就按纯文本渲染，不自己造引用。 */
function citeParts(line: string): TextPart[] {
  const parts: TextPart[] = []
  const pattern = /\[(\d{1,2})\]|【(\d{1,2})】/g
  let cursor = 0
  for (const match of line.matchAll(pattern)) {
    const at = match.index
    const head = line.slice(cursor, at)
    if (head) {
      parts.push({ cite: false, text: head })
    }
    parts.push({ cite: true, text: match[1] ?? match[2] ?? "" })
    cursor = at + match[0].length
  }
  const tail = line.slice(cursor)
  if (tail) {
    parts.push({ cite: false, text: tail })
  }
  return parts.length ? parts : [{ cite: false, text: line }]
}

async function loadSidebar(): Promise<void> {
  if (!session.accessToken) {
    return
  }
  dataPhase.value = "loading"
  dataError.value = ""
  try {
    await loadModelStatus()
    await Promise.all([loadMacro(), loadStocks(), loadDocs()])
    dataUpdatedAt.value = formatMoment(new Date())
    dataPhase.value = "ready"
  } catch {
    dataError.value = "无法连接接口"
    dataPhase.value = "error"
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

async function loadMacro(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/macro/series",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const data = dataOf(await readJson(response))
  macroNotice.value = asString(data?.notice) ?? ""
  const all = listOf(data?.series)
  macroSeriesCount.value = all.length
  const picked = all.slice(0, 4)
  const tiles: MacroTile[] = []
  const lines: SeriesLine[] = []
  for (const item of picked) {
    const code = asString(item.series_code)
    if (!code) {
      continue
    }
    const label = asString(item.name_cn) ?? code
    const page = await loadSeries(code)
    if (!page) {
      continue
    }
    const last = page.values.length - 1
    tiles.push({
      code,
      label,
      unit: asString(item.base_unit) ?? "",
      value: last >= 0 ? page.values[last] : null,
      observationDate: last >= 0 ? page.dates[last] : "",
      spark: page.values,
    })
    if (page.values.length >= 2 && lines.length < 2) {
      lines.push({ name: label, dates: page.dates, values: page.values })
    }
  }
  macroTiles.value = tiles
  seriesLines.value = lines
}

async function loadSeries(code: string): Promise<{ dates: string[]; values: number[] } | null> {
  const response = await authorizedFetch(
    `/api/v1/macro/series/${code}/observations`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  if (!response.ok) {
    return null
  }
  const rows = listOf(dataOf(await readJson(response))?.observations).slice(-24)
  const dates: string[] = []
  const values: number[] = []
  for (const row of rows) {
    const value = asNumber(row.value)
    const date = asString(row.observation_date)
    if (value === null || !date) {
      continue
    }
    dates.push(date)
    values.push(value)
  }
  return { dates, values }
}

const macroChartOption = computed(() => {
  const lines = seriesLines.value.filter((line) => line.values.length >= 2)
  if (!lines.length) {
    return null
  }
  return {
    grid: { left: 48, right: 12, top: 26, bottom: 24 },
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
      data: lines[0].dates,
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
    series: lines.map((line) => ({
      name: line.name,
      type: "line",
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 2 },
      data: line.values,
    })),
  }
})

async function loadStocks(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/watchlists",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const group = listOf(dataOf(await readJson(response))?.groups)[0] ?? null
  if (!group) {
    watchGroupId.value = ""
    stocks.value = []
    return
  }
  watchGroupId.value = asString(group.id) ?? ""
  const built: StockRow[] = []
  for (const row of listOf(group.items).slice(0, 8)) {
    const instrumentId = asString(row.instrument_id)
    if (!instrumentId) {
      continue
    }
    const bar = await loadLatestBar(instrumentId)
    built.push({
      instrumentId,
      symbol: asString(row.display_symbol) ?? "—",
      nameCn: asString(row.name_cn) ?? "—",
      latestClose: bar.close,
      latestDate: bar.date,
      simpleReturn: bar.simpleReturn,
    })
  }
  stocks.value = built
}

async function loadLatestBar(
  instrumentId: string,
): Promise<{ close: number | null; date: string; simpleReturn: number | null }> {
  const response = await authorizedFetch(
    `/api/v1/market/bars/${instrumentId}`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  if (!response.ok) {
    return { close: null, date: "", simpleReturn: null }
  }
  const rows = listOf(dataOf(await readJson(response))?.bars)
  const last = rows[rows.length - 1]
  return {
    close: last ? asNumber(last.close) : null,
    date: last ? (asString(last.trade_date) ?? asString(last.session_date) ?? "") : "",
    simpleReturn: last ? asNumber(last.simple_return) : null,
  }
}

async function ensureGroup(): Promise<string> {
  if (watchGroupId.value) {
    return watchGroupId.value
  }
  const created = await authorizedFetch(
    "/api/v1/watchlists",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "关注" }),
    },
    session.accessToken,
    refreshToken,
  )
  watchGroupId.value = asString(dataOf(await readJson(created))?.id) ?? ""
  return watchGroupId.value
}

async function addSymbol(): Promise<void> {
  const text = symbolInput.value.trim()
  if (!text || !session.accessToken) {
    return
  }
  const groupId = await ensureGroup()
  if (!groupId) {
    stockMessage.value = "无法新建自选分组"
    return
  }
  const found = await fetch(apiUrl(`/api/v1/instruments/search?q=${encodeURIComponent(text)}`))
  const hit = listOf(dataOf(await readJson(found))?.results)[0] ?? null
  const instrumentId = asString(hit?.id)
  if (!instrumentId) {
    stockMessage.value = "暂无该证券"
    return
  }
  const added = await authorizedFetch(
    `/api/v1/watchlists/${groupId}/items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument_id: instrumentId }),
    },
    session.accessToken,
    refreshToken,
  )
  if (!added.ok) {
    stockMessage.value = "无法加入自选"
    return
  }
  symbolInput.value = ""
  stockMessage.value = ""
  await loadStocks()
}

async function loadDocs(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/documents",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  if (!response.ok) {
    docs.value = []
    return
  }
  const payload = await readJson(response)
  docs.value = listOf(dataOf(payload)?.documents)
    .slice(0, 5)
    .flatMap((row) => {
      const id = asString(row.id)
      const title = asString(row.title)
      if (!id || !title) {
        return []
      }
      return [
        {
          id,
          title,
          parseStatus: asString(row.parse_status) ?? "",
          uploadedAt: formatMoment(row.uploaded_at),
        },
      ]
    })
}

/** 模板里的「取消」走真正的 cancel 接口，用于后端还没落库的那条。 */
async function cancelPending(): Promise<void> {
  const clientId = pendingClientId.value
  if (!clientId || !sessionId.value || !session.accessToken) {
    stop()
    return
  }
  const response = await authorizedFetch(
    `/api/v1/assistant/sessions/${sessionId.value}/messages/${clientId}/cancel`,
    { method: "POST" },
    session.accessToken,
    refreshToken,
  )
  if (!response.ok) {
    errorText.value = "无法取消这条回答"
    return
  }
  await loadMessages()
}

onMounted(() => {
  void load()
  void loadSidebar()
})
</script>

<template>
  <main class="page stack">
    <PageHeader
      title="AI 智能投研助手"
      subtitle="基于全市场数据与专业研究框架，提供可验证、可追溯的投研分析"
    >
      <template #actions>
        <StatusPill
          v-if="modelAvailable !== null"
          :text="modelAvailable ? '模型已配置' : '模型未配置'"
          :tone="modelAvailable ? 'ok' : 'warn'"
          dot
        />
        <StatusPill v-else text="模型状态未知" tone="muted" />
      </template>
    </PageHeader>

    <el-alert
      v-if="modelMessage"
      :type="modelAvailable ? 'info' : 'warning'"
      :closable="false"
      show-icon
      :title="modelMessage"
    />

    <div class="grid-1-2">
      <!-- 左：问答 -->
      <PanelCard
        title="研究问答"
        :info="`当前会话：${sessionTitle || '—'}`"
        compact
      >
        <template #actions>
          <span v-if="phase === 'ready'" class="muted msg-count">共 {{ turns.length }} 条消息</span>
        </template>

        <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

        <StateBlock v-else-if="phase === 'error'" state="error" :message="message" @retry="load" />

        <div v-else class="chat">
          <div ref="threadEl" class="thread">
            <p v-if="!turns.length" class="muted thread-empty">
              还没有提问。未配置模型时后端只回一句说明，不会编造财务数字。
            </p>

            <div
              v-for="turn in turns"
              :key="`${turn.role}-${turn.id}`"
              class="turn"
              :class="turn.role === 'user' ? 'turn-user' : 'turn-assistant'"
            >
              <span v-if="turn.role === 'assistant'" class="avatar" aria-hidden="true">AI</span>

              <div class="bubble">
                <header class="bubble-head">
                  <strong>{{ turn.role === "user" ? "我" : "FinScope AI" }}</strong>
                  <StatusPill
                    v-if="turn.role === 'assistant' && turn.completionStatus === 'streaming'"
                    text="生成中"
                    tone="accent"
                    dot
                  />
                  <StatusPill
                    v-else-if="turn.role === 'assistant' && turn.completionStatus === 'cancelled'"
                    text="已取消"
                    tone="muted"
                  />
                  <StatusPill
                    v-else-if="turn.role === 'assistant' && turn.completionStatus === 'completed'"
                    text="思考完成"
                    tone="ok"
                  />
                </header>

                <p v-if="turn.role === 'user'" class="doc-text user-text">{{ turn.content }}</p>

                <template v-else>
                  <p v-if="!turn.content" class="muted">正在等待后端返回内容…</p>
                  <template v-else>
                    <template v-for="(block, index) in bodyBlocks(turn.content)" :key="index">
                      <h3 v-if="block.kind === 'section'" class="doc-section">{{ block.text }}</h3>
                      <ul v-else-if="isListLine(block.text)" class="doc-list">
                        <li>
                          <template v-for="(part, at) in citeParts(listText(block.text))" :key="at">
                            <span v-if="part.cite" class="cite">{{ part.text }}</span>
                            <template v-else>{{ part.text }}</template>
                          </template>
                        </li>
                      </ul>
                      <p v-else class="doc-text">
                        <template v-for="(part, at) in citeParts(block.text)" :key="at">
                          <span v-if="part.cite" class="cite">{{ part.text }}</span>
                          <template v-else>{{ part.text }}</template>
                        </template>
                      </p>
                    </template>
                  </template>

                  <footer v-if="pendingClientId === turn.clientMessageId" class="reply-actions">
                    <el-button size="small" @click="stop">停止</el-button>
                    <el-button size="small" @click="cancelPending">取消这条回答</el-button>
                  </footer>
                  <footer v-else-if="turn.completionStatus === 'completed'" class="reply-actions">
                    <el-button size="small" text @click="regenerate">重新生成</el-button>
                  </footer>
                </template>
              </div>
            </div>
          </div>

          <p v-if="errorText" class="muted error-line" role="status">{{ errorText }}</p>

          <form class="composer" @submit.prevent="ask">
            <el-input
              v-model="question"
              type="textarea"
              :rows="2"
              resize="none"
              maxlength="2000"
              placeholder="继续提问，支持宏观、行业、公司、策略等多维度问题…"
            />
            <div class="composer-foot">
              <el-switch
                disabled
                active-text="联网搜索"
                title="后端没有联网检索通道，这个开关固定关闭；未配置模型时也不会用自己的知识补一段分析。"
              />
              <div class="composer-right">
                <el-button v-if="streaming" @click="stop">停止</el-button>
                <el-button
                  type="primary"
                  native-type="submit"
                  :loading="sending"
                  :disabled="sending"
                >
                  发送
                </el-button>
              </div>
            </div>
          </form>
        </div>
      </PanelCard>

      <!-- 右：本次分析使用的数据 -->
      <PanelCard title="本次分析使用的数据" compact>
        <template #actions>
          <span class="muted side-asof">数据更新时间：{{ dataUpdatedAt || "—" }}</span>
        </template>

        <StateBlock v-if="dataPhase === 'loading'" state="loading" :rows="5" />
        <StateBlock
          v-else-if="dataPhase === 'error'"
          state="error"
          :message="dataError"
          @retry="loadSidebar"
        />

        <div v-else class="stack side-body">
          <!-- 宏观指标 -->
          <section class="side-block">
            <header class="side-head">
              <h3>宏观指标<template v-if="macroSeriesCount">（{{ macroSeriesCount }}）</template></h3>
              <router-link class="more" to="/macro/us">查看全部 ›</router-link>
            </header>
            <p v-if="macroNotice" class="muted side-note">{{ macroNotice }}</p>
            <div v-if="macroTiles.length" class="grid-2">
              <MetricTile
                v-for="tile in macroTiles"
                :key="tile.code"
                :label="tile.label"
                :value="tile.value"
                :period="tile.observationDate"
                :unit="tile.unit"
              >
                <template #chart>
                  <MiniSpark
                    :points="tile.spark"
                    kind="line"
                    tone="accent"
                    :width="120"
                    :height="34"
                  />
                </template>
              </MetricTile>
            </div>
            <p v-else class="muted side-note">还没有同步到观测值的宏观序列，这里不填演示数字。</p>
          </section>

          <!-- 相关股票 -->
          <section class="side-block">
            <header class="side-head">
              <h3>相关股票<template v-if="stocks.length">（{{ stocks.length }}）</template></h3>
              <router-link class="more" to="/watchlist">查看全部 ›</router-link>
            </header>
            <el-table v-if="stocks.length" :data="stocks" size="small" class="side-table">
              <el-table-column prop="symbol" label="代码" width="92" />
              <el-table-column prop="nameCn" label="公司简称" min-width="96" />
              <el-table-column label="最新价" width="86" align="right">
                <template #default="scope">
                  <span class="tabular">
                    {{ scope.row.latestClose === null ? "—" : scope.row.latestClose }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="涨跌幅" width="94" align="right">
                <template #default="scope">
                  <DeltaValue
                    :value="scope.row.simpleReturn === null ? null : scope.row.simpleReturn * 100"
                    suffix="%"
                    size="sm"
                  />
                </template>
              </el-table-column>
              <el-table-column label="数据日期" width="104">
                <template #default="scope">
                  <span class="muted">{{ scope.row.latestDate || "—" }}</span>
                </template>
              </el-table-column>
              <el-table-column label="相关性" width="96">
                <template #default>
                  <StatusPill text="未计算" tone="muted" />
                </template>
              </el-table-column>
              <el-table-column label="操作" width="96">
                <template #default="scope">
                  <router-link class="row-action" :to="`/market/stocks/${scope.row.symbol}`">
                    查看
                  </router-link>
                </template>
              </el-table-column>
            </el-table>
            <p v-else class="muted side-note">
              自选分组是空的。搜索代码加入自选后，这里显示已入库的收盘价与涨跌幅。
            </p>
            <form class="inline-form" @submit.prevent="addSymbol">
              <el-input v-model="symbolInput" size="small" placeholder="代码或名称，例如 002371" />
              <el-button size="small" native-type="submit">加入自选</el-button>
            </form>
            <p v-if="stockMessage" class="muted side-note" role="status">{{ stockMessage }}</p>
          </section>

          <!-- 相关研报与文档 -->
          <section class="side-block">
            <header class="side-head">
              <h3>相关研报与文档<template v-if="docs.length">（{{ docs.length }}）</template></h3>
              <router-link class="more" to="/data-center">查看全部 ›</router-link>
            </header>
            <ul v-if="docs.length" class="doc-rows">
              <li v-for="doc in docs" :key="doc.id">
                <span class="doc-title">{{ doc.title }}</span>
                <span class="muted doc-meta">{{ doc.uploadedAt }}</span>
                <StatusPill :text="doc.parseStatus || '—'" tone="muted" />
              </li>
            </ul>
            <p v-else class="muted side-note">还没有上传过文档，这里不展示示例研报。</p>
          </section>

          <!-- 相关图表 -->
          <section class="side-block">
            <header class="side-head">
              <h3>相关图表</h3>
              <router-link class="more" to="/macro/us">查看全部 ›</router-link>
            </header>
            <VChart
              v-if="macroChartOption"
              class="side-chart"
              :option="macroChartOption"
              autoresize
            />
            <p v-else class="muted side-note">
              可画的序列不足两条（每条至少两个观测值），按规范不画假曲线。
            </p>
          </section>
        </div>
      </PanelCard>
    </div>

    <SourceFooter
      :as-of="dataUpdatedAt || '—'"
      :sources="['后端问答接口', 'FRED（宏观序列）', 'A股日线（东方财富 / 腾讯行情）']"
      note="正文按后端返回原样渲染；未配置模型时不会有 AI 段落与引用。"
    />
  </main>
</template>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.thread {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-height: 620px;
  overflow-y: auto;
  padding-right: 4px;
}

.msg-count,
.thread-empty {
  font-size: 13px;
}

.thread-empty {
  margin: 0;
}

.turn {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.turn-user {
  flex-direction: row-reverse;
}

.avatar {
  flex: 0 0 30px;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
}

.bubble {
  min-width: 0;
  flex: 1 1 auto;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  background: var(--panel-soft);
}

.turn-user .bubble {
  flex: 0 1 auto;
  max-width: 88%;
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  border-color: color-mix(in srgb, var(--accent) 32%, transparent);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.user-text {
  margin-bottom: 0;
}

.reply-actions {
  margin-top: 10px;
}

.error-line {
  margin: 0;
  font-size: 13px;
}

.composer {
  border-top: 1px solid var(--line);
  padding-top: 12px;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}

.composer-right {
  display: flex;
  gap: 8px;
}

.side-asof {
  font-size: 12px;
}

.side-body {
  gap: 14px;
}

.side-block + .side-block {
  border-top: 1px solid var(--line);
  padding-top: 12px;
}

.side-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.side-head h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.more {
  color: var(--muted);
  font-size: 13px;
  text-decoration: none;
}

.more:hover {
  color: var(--accent);
}

.side-note {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.7;
}

.row-action {
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
}

.side-chart {
  height: 180px;
}

.side-table {
  margin-bottom: 8px;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
}

.doc-rows {
  margin: 0;
  padding: 0;
  list-style: none;
}

.doc-rows li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--line);
  font-size: 13px;
}

.doc-rows li:last-child {
  border-bottom: none;
}

.doc-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-meta {
  flex: 0 0 auto;
}

.inline-form {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
