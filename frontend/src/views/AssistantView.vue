<script setup lang="ts">
/**
 * AI 智能投研助手（效果图 8）。
 *
 * 所有内容都来自后端真实返回，界面不会补一段假的分析正文：
 *  - GET  /api/v1/assistant/sessions                          问答记录（按最近提问倒序，默认打开第一条）
 *  - POST /api/v1/assistant/sessions                          新建问答（点「新对话」后发第一问时才建）
 *  - PATCH / DELETE /api/v1/assistant/sessions/{id}           改名 / 删除（连同消息；生成中返回 409）
 *  - GET  /api/v1/assistant/sessions/{id}/messages            历史消息（role/content/completion_status）
 *  - POST /api/v1/assistant/sessions/{id}/messages            提问，SSE 回传；读 data: 行里的 delta / done，
 *                                                             以及 step / block（步骤与服务端生成的图表、表格、提示，
 *                                                             见 api/assistantBlocks.ts）；`: ping` 心跳注释行忽略
 *                                                             （429 限流 / 409 同一条还在生成：显示后端给的原因）
 *  - POST /api/v1/assistant/sessions/{id}/messages/{cid}/cancel  取消未完成的回答
 *  - GET  /api/v1/analysis/model-status                       模型是否已配置
 *  - GET  /api/v1/watchlists / POST /api/v1/watchlists        自选分组
 *  - GET  /api/v1/instruments/search                          加入自选时解析代码
 *  - GET  /api/v1/market/bars/{instrument_id}                 自选股最新价与涨跌幅
 *  - GET  /api/v1/macro/series[/{code}/observations]          宏观指标与迷你图
 *  - GET  /api/v1/documents                                   我上传的文档
 *
 * 没有模型时后端只回一句固定说明；示例问题只填进输入框，不会在前端生成任何回答。
 */
import { computed, nextTick, onMounted, ref } from "vue"
import { ChatDotRound, Cpu } from "@element-plus/icons-vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import {
  applyEvent,
  EMPTY_EXTRAS,
  splitNotices,
  type AssistantBlock,
  type NoticeBlock,
  type ReplyExtras,
} from "../api/assistantBlocks"
import {
  parseSession,
  parseSessions,
  removeSession,
  upsertSession,
  type SessionSummary,
} from "../api/assistantSessions"
import { emptyReplyText, failureText, parseSseLine, parseTurn } from "../api/assistantStream"
import { asNumber, asString, dataOf, formatMoment, listOf } from "../api/json"
import { newUuid } from "../api/uuid"
import { VChart } from "../charts"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MetricTile from "../components/ui/MetricTile.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import MarkdownText from "../components/assistant/MarkdownText.vue"
import ReplyBlocks from "../components/assistant/ReplyBlocks.vue"
import ReplySources from "../components/assistant/ReplySources.vue"
import ReplySteps from "../components/assistant/ReplySteps.vue"
import SessionList from "../components/assistant/SessionList.vue"
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
  /** 步骤与渲染块：流式时逐个事件并入，历史消息一次读出；旧消息为空。 */
  extras: ReplyExtras
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
  percent: boolean
  value: number | null
  change: number | null
  observationDate: string
  spark: number[]
}

interface SeriesLine {
  name: string
  dates: string[]
  values: number[]
}

/** 示例问题都能用库里已有的数据回答；点击只填入输入框，由用户决定是否发送。 */
const EXAMPLES = [
  "苹果最近两个财年的营业收入和净利润有什么变化？",
  "银行、非银金融、食品饮料相对沪深300的强弱如何？",
  "美国10年期与2年期国债收益率最近怎么走？",
  "贵州茅台近一个月的收盘价走势怎样？",
]

/** 宏观栏优先放利率与就业，量纲一致才能放进同一张走势图。 */
const PREFERRED_SERIES = ["DGS10", "DGS2", "FEDFUNDS", "UNRATE"]

const UNIT_NAMES: Record<string, string> = {
  Percent: "%",
  "Billions of Dollars": "十亿美元",
  "Thousands of Persons": "千人",
}

const DOC_STATUS: Record<string, { text: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" }> = {
  queued: { text: "排队解析", tone: "muted" },
  parsing: { text: "解析中", tone: "accent" },
  parsed: { text: "已解析", tone: "ok" },
  failed: { text: "解析失败", tone: "danger" },
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const question = ref("")
const sessionId = ref("")
const sessionTitle = ref("")
const sessions = ref<SessionSummary[]>([])
const sessionsPhase = ref<"loading" | "ready" | "error">("loading")
const sessionsMessage = ref("")
const sessionActionError = ref("")
const turns = ref<Turn[]>([])
const sending = ref(false)
const streaming = ref(false)
const activeClientId = ref("")
const errorText = ref("")
const threadEl = ref<HTMLElement | null>(null)
const sessionPaneEl = ref<HTMLElement | null>(null)
const composerEl = ref<{ focus: () => void } | null>(null)

/** 距底部不超过这一距离时，流式输出才继续跟着滚。 */
const NEAR_BOTTOM_PX = 80
const stickToBottom = ref(true)
let scrollGeneration = 0

const CONTEXT_KEY = "finscope.assistant.contextOpen"

/** 同步读偏好，避免先展开再收起。没有记录时，窄于 1180px 默认收起。 */
function readContextOpen(): boolean {
  if (typeof window === "undefined") {
    return false
  }
  try {
    const saved = window.localStorage.getItem(CONTEXT_KEY)
    if (saved === "1") {
      return true
    }
    if (saved === "0") {
      return false
    }
    return window.matchMedia("(min-width: 1180px)").matches
  } catch {
    return false
  }
}

const contextOpen = ref(readContextOpen())

function toggleContext(): void {
  const follow = stickToBottom.value
  contextOpen.value = !contextOpen.value
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(CONTEXT_KEY, contextOpen.value ? "1" : "0")
    } catch {
      // 浏览器禁止写入时，这一次的展开状态仍然保留。
    }
  }
  // 展开或收起会改变消息区高度。原本贴在底部时继续贴住最新一条，往上翻过的不拉回去。
  if (follow) {
    void scrollToLatest(true)
  }
}

const modelAvailable = ref<boolean | null>(null)
const modelMessage = ref("")

/** 右侧上下文栏单独加载，不阻塞问答本身。 */
const dataPhase = ref<"loading" | "ready" | "error">("loading")
const dataError = ref("")
const macroTiles = ref<MacroTile[]>([])
const macroSeriesCount = ref(0)
const seriesLines = ref<SeriesLine[]>([])
const stocks = ref<StockRow[]>([])
const watchGroupId = ref("")
const symbolInput = ref("")
const stockMessage = ref("")
const docs = ref<DocRow[]>([])
const dataUpdatedAt = ref("")

const contextPeek = computed(() => {
  if (dataPhase.value === "loading") {
    return "读取中"
  }
  if (dataPhase.value === "error") {
    return dataError.value || "研究上下文暂时读不到"
  }
  const macroCount = macroSeriesCount.value || macroTiles.value.length
  return `宏观 ${macroCount} · 自选 ${stocks.value.length} · 文档 ${docs.value.length}`
})

const modelTone = computed<"ok" | "warn" | "muted">(() =>
  modelAvailable.value === null ? "muted" : modelAvailable.value ? "ok" : "warn",
)
const modelLabel = computed<string>(() =>
  modelAvailable.value === null ? "模型状态未知" : modelAvailable.value ? "模型已接入" : "模型不可用",
)

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function createSession(): Promise<void> {
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
  const item = parseSession(dataOf(await readJson(created)))
  if (!created.ok || !item) {
    throw new Error("create session failed")
  }
  sessionId.value = item.id
  sessionTitle.value = item.title
  sessions.value = upsertSession(sessions.value, item)
}

async function loadSessions(): Promise<SessionSummary[]> {
  sessionsPhase.value = "loading"
  try {
    const listed = await authorizedFetch(
      "/api/v1/assistant/sessions",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    if (!listed.ok) {
      throw new Error("list sessions failed")
    }
    sessions.value = parseSessions(await readJson(listed))
    sessionsPhase.value = "ready"
  } catch {
    sessionsMessage.value = "问答记录读不出来，请稍后重试"
    sessionsPhase.value = "error"
  }
  return sessions.value
}

/** 回答结束后刷新：后端会用第一问改标题、更新最近提问时间。失败不打扰当前对话。 */
async function refreshSessions(): Promise<void> {
  try {
    const listed = await authorizedFetch(
      "/api/v1/assistant/sessions",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    if (listed.ok) {
      sessions.value = parseSessions(await readJson(listed))
      sessionTitle.value = sessions.value.find((item) => item.id === sessionId.value)?.title ?? sessionTitle.value
    }
  } catch {
    // 列表下次打开页面再刷新。
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再提问"
    return
  }
  try {
    const listed = await loadSessions()
    const first = listed[0]
    if (first) {
      sessionId.value = first.id
      sessionTitle.value = first.title
      await loadMessages()
    } else {
      sessionId.value = ""
      sessionTitle.value = ""
      turns.value = []
    }
    phase.value = "ready"
    await presentLatest()
  } catch {
    message.value = "网络连接失败，请稍后重试"
    phase.value = "error"
  }
}

/** 新对话：先清空界面，发第一问时才在后端新建，不会留下一堆空的问答记录。 */
function startNew(): void {
  if (sending.value || !session.accessToken) {
    return
  }
  sessionId.value = ""
  sessionTitle.value = ""
  turns.value = []
  errorText.value = ""
  sessionActionError.value = ""
  void nextTick(() => composerEl.value?.focus())
}

/** 切换会话：每段对话的历史与模型上下文互相独立。生成中不许切换。 */
async function selectSession(id: string): Promise<void> {
  if (sending.value || id === sessionId.value) {
    return
  }
  const target = sessions.value.find((item) => item.id === id)
  if (!target) {
    return
  }
  sessionId.value = target.id
  sessionTitle.value = target.title
  turns.value = []
  errorText.value = ""
  sessionActionError.value = ""
  stickToBottom.value = true
  phase.value = "loading"
  try {
    await loadMessages()
    phase.value = "ready"
    await presentLatest()
  } catch {
    message.value = "这段对话读不出来，请稍后重试"
    phase.value = "error"
  }
}

async function renameSession(id: string, title: string): Promise<void> {
  sessionActionError.value = ""
  try {
    const response = await authorizedFetch(
      `/api/v1/assistant/sessions/${id}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
      session.accessToken,
      refreshToken,
    )
    const item = parseSession(dataOf(await readJson(response)))
    if (!response.ok || !item) {
      sessionActionError.value = "改名失败，请稍后重试"
      return
    }
    sessions.value = sessions.value.map((existing) => (existing.id === id ? { ...existing, title: item.title } : existing))
    if (id === sessionId.value) {
      sessionTitle.value = item.title
    }
  } catch {
    sessionActionError.value = "网络连接失败，没有改名"
  }
}

async function deleteSession(id: string): Promise<void> {
  if (sending.value) {
    return
  }
  sessionActionError.value = ""
  try {
    const response = await authorizedFetch(
      `/api/v1/assistant/sessions/${id}`,
      { method: "DELETE" },
      session.accessToken,
      refreshToken,
    )
    if (!response.ok && response.status !== 404) {
      sessionActionError.value = await failureText(response)
      return
    }
    sessions.value = removeSession(sessions.value, id)
    if (id === sessionId.value) {
      const next = sessions.value[0]
      if (next) {
        await selectSession(next.id)
      } else {
        startNew()
      }
    }
  } catch {
    sessionActionError.value = "网络连接失败，没有删除"
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
}

function readTurn(value: Record<string, unknown>): Turn[] {
  const turn = parseTurn(value)
  return turn ? [turn] : []
}

function verdictBlocks(turn: Turn): NoticeBlock[] {
  return splitNotices(turn.extras.blocks).verdict
}

function dataBlocks(turn: Turn): AssistantBlock[] {
  return splitNotices(turn.extras.blocks).data
}

function waitFrame(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve())
  })
}

function onThreadScroll(): void {
  const host = threadEl.value
  if (!host || host.clientHeight === 0) {
    return
  }
  stickToBottom.value = host.scrollHeight - host.scrollTop - host.clientHeight <= NEAR_BOTTOM_PX
}

/**
 * 把消息列表滚到最新一条。只改 `.thread` 自己的 scrollTop，
 * 不用 scrollIntoView，避免把页面和问答记录一起带走。
 * force 用于进入页面、切换会话、刚发出提问；流式追加时不强制。
 */
async function scrollToLatest(force = false): Promise<void> {
  if (force) {
    stickToBottom.value = true
  }
  if (!force && !stickToBottom.value) {
    return
  }
  const generation = ++scrollGeneration
  await nextTick()
  await waitFrame()
  if (generation !== scrollGeneration) {
    return
  }
  const host = threadEl.value
  if (!host) {
    return
  }
  if (!force && !stickToBottom.value) {
    return
  }
  host.scrollTop = host.scrollHeight
  await waitFrame()
  if (generation !== scrollGeneration) {
    return
  }
  const again = threadEl.value
  if (!again || again.clientHeight === 0) {
    return
  }
  if (!force && !stickToBottom.value) {
    return
  }
  again.scrollTop = again.scrollHeight
  stickToBottom.value = again.scrollHeight - again.scrollTop - again.clientHeight <= NEAR_BOTTOM_PX
}

/** 问答记录最新在最上。只在会话列表自己的滚动条里露出当前项，不滚动页面。 */
function revealActiveSession(): void {
  const pane = sessionPaneEl.value
  if (!pane) {
    return
  }
  const id = sessionId.value
  if (!id) {
    pane.scrollTop = 0
    return
  }
  const safe = typeof CSS !== "undefined" && typeof CSS.escape === "function" ? CSS.escape(id) : id
  const item = pane.querySelector<HTMLElement>(`[data-session="${safe}"]`)
  if (!item) {
    return
  }
  const paneRect = pane.getBoundingClientRect()
  const itemRect = item.getBoundingClientRect()
  if (itemRect.top < paneRect.top) {
    pane.scrollTop += itemRect.top - paneRect.top
  } else if (itemRect.bottom > paneRect.bottom) {
    pane.scrollTop += itemRect.bottom - paneRect.bottom
  }
}

async function presentLatest(): Promise<void> {
  await scrollToLatest(true)
  revealActiveSession()
}

async function restoreThreadOffset(offset: number): Promise<void> {
  await nextTick()
  await waitFrame()
  const host = threadEl.value
  if (!host) {
    return
  }
  host.scrollTop = offset
}

function useExample(text: string): void {
  question.value = text
  void nextTick(() => composerEl.value?.focus())
}

/** 提问：POST 拿 SSE，逐段把 delta 追加到临时气泡。 */
async function ask(): Promise<void> {
  const text = question.value.trim()
  if (!text || !session.accessToken || sending.value) {
    if (!text) {
      errorText.value = "请输入问题"
    }
    return
  }
  // 先生成 ID 再置发送状态：HTTP 访问时 crypto.randomUUID 不存在，newUuid 有降级实现。
  const clientMessageId = newUuid()
  sending.value = true
  errorText.value = ""
  if (!sessionId.value) {
    try {
      await createSession()
    } catch {
      errorText.value = "新建对话失败，问题没有发出"
      sending.value = false
      return
    }
  }
  streaming.value = true
  activeClientId.value = clientMessageId
  turns.value = [
    ...turns.value,
    {
      id: clientMessageId,
      clientMessageId,
      role: "user",
      content: text,
      completionStatus: "completed",
      extras: EMPTY_EXTRAS,
    },
    {
      id: clientMessageId,
      clientMessageId,
      role: "assistant",
      content: "",
      completionStatus: "streaming",
      extras: EMPTY_EXTRAS,
    },
  ]
  question.value = ""
  await scrollToLatest(true)
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
      errorText.value = await failureText(response)
      turns.value = turns.value.filter((turn) => turn.clientMessageId !== clientMessageId)
      question.value = text
      return
    }
    await readStream(response)
    const follow = stickToBottom.value
    const savedTop = threadEl.value?.scrollTop ?? 0
    await loadMessages()
    if (follow) {
      await scrollToLatest(true)
    } else {
      await restoreThreadOffset(savedTop)
    }
    void refreshSessions().then(() => revealActiveSession())
  } catch {
    errorText.value = "网络连接失败，问题没有发出"
    turns.value = turns.value.filter((turn) => turn.clientMessageId !== clientMessageId)
    question.value = text
  } finally {
    sending.value = false
    streaming.value = false
    activeClientId.value = ""
  }
}

function onComposerKey(event: KeyboardEvent): void {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void ask()
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
      const payload = parseSseLine(line)
      if (!payload) {
        continue
      }
      const delta = asString(payload.delta)
      if (delta !== null) {
        appendDelta(delta)
      }
      applyExtras(payload)
      if (payload.done === true) {
        return
      }
    }
  }
}

/** step / block / done 事件并入正在生成的那条；没有这些事件（旧协议）时什么也不变。 */
function applyExtras(payload: Record<string, unknown>): void {
  const target = turns.value.find((turn) => turn.id === activeClientId.value && turn.role === "assistant")
  if (!target) {
    return
  }
  const next = applyEvent(target.extras, payload)
  if (next !== target.extras) {
    target.extras = next
    if (stickToBottom.value) {
      void scrollToLatest()
    }
  }
}

function appendDelta(part: string): void {
  const target = turns.value.find((turn) => turn.id === activeClientId.value && turn.role === "assistant")
  if (!target) {
    return
  }
  const follow = stickToBottom.value
  target.content += part
  if (follow) {
    void scrollToLatest()
  }
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
  errorText.value = "已停止显示这段回答"
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

function docStatus(status: string): { text: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" } {
  return DOC_STATUS[status] ?? { text: "处理中", tone: "muted" }
}

async function loadSidebar(): Promise<void> {
  if (!session.accessToken) {
    return
  }
  dataPhase.value = "loading"
  dataError.value = ""
  try {
    await Promise.all([loadModelStatus(), loadMacro(), loadStocks(), loadDocs()])
    dataUpdatedAt.value = formatMoment(new Date().toISOString())
    dataPhase.value = "ready"
  } catch {
    dataError.value = "网络连接失败，研究上下文暂时读不到"
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
  modelAvailable.value = data?.available === true
  modelMessage.value = asString(data?.message) ?? ""
}

async function loadMacro(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/macro/series",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const all = listOf(dataOf(await readJson(response))?.series)
  macroSeriesCount.value = all.length
  const ranked = [
    ...PREFERRED_SERIES.flatMap((code) => all.filter((item) => asString(item.series_code) === code)),
    ...all.filter((item) => !PREFERRED_SERIES.includes(asString(item.series_code) ?? "")),
  ].slice(0, 4)
  const loaded = await Promise.all(
    ranked.map(async (item) => {
      const code = asString(item.series_code)
      return code ? { item, code, page: await loadSeries(code) } : null
    }),
  )
  const tiles: MacroTile[] = []
  const lines: SeriesLine[] = []
  for (const entry of loaded) {
    if (!entry?.page) {
      continue
    }
    const { item, code, page } = entry
    const rawUnit = asString(item.base_unit) ?? ""
    const last = page.values.length - 1
    const label = asString(item.name_cn) ?? code
    tiles.push({
      code,
      label,
      unit: UNIT_NAMES[rawUnit] ?? "",
      percent: rawUnit === "Percent",
      value: last >= 0 ? (page.values[last] ?? null) : null,
      change: last >= 1 ? (page.values[last] ?? 0) - (page.values[last - 1] ?? 0) : null,
      observationDate: last >= 0 ? (page.dates[last] ?? "") : "",
      spark: page.values.slice(-30),
    })
    if (rawUnit === "Percent" && page.values.length >= 2 && lines.length < 2) {
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
  const rows = listOf(dataOf(await readJson(response))?.observations).slice(-120)
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
  const base = lines[0]
  return {
    grid: { left: 36, right: 10, top: 28, bottom: 22 },
    tooltip: { trigger: "axis", valueFormatter: (value: number) => `${value}%` },
    legend: { top: 0, left: 0 },
    xAxis: { type: "category", data: base?.dates ?? [], axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", scale: true, axisLabel: { formatter: "{value}%" } },
    series: lines.map((line) => {
      const byDate = new Map(line.dates.map((date, index) => [date, line.values[index]]))
      return {
        name: line.name,
        type: "line",
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        data: (base?.dates ?? []).map((date) => byDate.get(date) ?? null),
      }
    }),
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
  const rows = listOf(group.items).slice(0, 8)
  const built = await Promise.all(
    rows.map(async (row): Promise<StockRow | null> => {
      const instrumentId = asString(row.instrument_id)
      if (!instrumentId) {
        return null
      }
      const bar = await loadLatestBar(instrumentId)
      return {
        instrumentId,
        symbol: asString(row.display_symbol) ?? "—",
        nameCn: asString(row.name_cn) ?? "—",
        latestClose: bar.close,
        latestDate: bar.date,
        simpleReturn: bar.simpleReturn,
      }
    }),
  )
  stocks.value = built.filter((row): row is StockRow => row !== null)
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
    stockMessage.value = "暂未收录该证券"
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
    stockMessage.value = "加入自选失败，可能已经在列表里"
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
  docs.value = listOf(dataOf(await readJson(response))?.documents)
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

/** 「取消这条回答」走真正的 cancel 接口，用于还没落库的那条。 */
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
    errorText.value = "这条回答已经完成，无法取消"
    return
  }
  await loadMessages()
  await scrollToLatest(true)
}

onMounted(() => {
  void load()
  void loadSidebar()
})
</script>

<template>
  <main class="page assistant-page">
    <PageHeader
      class="assistant-top"
      title="AI 智能投研助手"
      subtitle="围绕已入库的宏观、行情与财报数据提问，回答可追溯到数据来源"
    >
      <template #actions>
        <StatusPill :text="modelLabel" :tone="modelTone" dot />
      </template>
    </PageHeader>

    <div class="assistant-body" :class="{ 'context-open': contextOpen }">
      <PanelCard class="chat-card" :title="sessionTitle || '研究问答'" info="每次提问都会保存在你的问答记录里" flush>
        <template #icon><ChatDotRound /></template>
        <template #actions>
          <span v-if="phase === 'ready' && turns.length" class="muted">共 {{ turns.length }} 条消息</span>
        </template>

        <div class="chat-layout">
        <aside ref="sessionPaneEl" class="session-pane">
          <SessionList
            :sessions="sessions"
            :active-id="sessionId"
            :phase="sessionsPhase"
            :message="sessionsMessage"
            :action-error="sessionActionError"
            :locked="sending"
            @select="selectSession"
            @create="startNew"
            @rename="renameSession"
            @remove="deleteSession"
            @retry="load"
          />
        </aside>

        <div class="chat-main">
        <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />
        <StateBlock v-else-if="phase === 'error'" state="error" :message="message" @retry="load" />

        <div v-else class="chat">
          <div v-if="modelAvailable === false" class="model-banner" role="status">
            <Cpu class="banner-icon" aria-hidden="true" />
            <div>
              <strong>模型暂不可用</strong>
              <p>{{ modelMessage || "未配置模型" }}。提问仍会保存，但只会收到一句系统说明，不会出现分析正文；管理员接入模型后即可使用。</p>
            </div>
          </div>

          <div ref="threadEl" class="thread" @scroll.passive="onThreadScroll">
            <section v-if="!turns.length" class="welcome">
              <span class="avatar large" aria-hidden="true">AI</span>
              <h3>想研究点什么？</h3>
              <p class="muted">
                可以问宏观利率、行业强弱、公司财报或个股走势。点一个示例填入输入框，修改后再发送。
              </p>
              <div class="examples">
                <button
                  v-for="item in EXAMPLES"
                  :key="item"
                  type="button"
                  class="example"
                  @click="useExample(item)"
                >
                  {{ item }}
                </button>
              </div>
            </section>

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
                    v-else-if="turn.role === 'assistant' && turn.completionStatus === 'failed'"
                    text="生成失败"
                    tone="danger"
                  />
                  <StatusPill
                    v-else-if="turn.role === 'assistant' && modelAvailable === false"
                    text="系统说明"
                    tone="warn"
                  />
                  <StatusPill
                    v-else-if="turn.role === 'assistant' && turn.completionStatus === 'completed'"
                    text="回答完成"
                    tone="ok"
                  />
                </header>

                <p v-if="turn.role === 'user'" class="doc-text user-text">{{ turn.content }}</p>

                <template v-else>
                  <ReplySteps :steps="turn.extras.steps" />
                  <p v-if="!turn.content && !turn.extras.blocks.length" class="muted">
                    {{ emptyReplyText(turn.completionStatus) }}
                  </p>
                  <MarkdownText
                    v-else-if="turn.content"
                    :text="turn.content"
                    :sources="turn.extras.sources"
                  />

                  <ReplyBlocks :blocks="verdictBlocks(turn)" />
                  <ReplyBlocks :blocks="dataBlocks(turn)" />
                  <ReplySources :sources="turn.extras.sources" />
                  <p v-if="turn.extras.asOf" class="muted asof">资料截止 {{ turn.extras.asOf }}</p>

                  <footer v-if="pendingClientId === turn.clientMessageId" class="reply-actions">
                    <el-button size="small" @click="stop">停止</el-button>
                    <el-button size="small" @click="cancelPending">取消这条回答</el-button>
                  </footer>
                  <footer
                    v-else-if="
                      (turn.completionStatus === 'completed' || turn.completionStatus === 'failed') &&
                      modelAvailable !== false
                    "
                    class="reply-actions"
                  >
                    <el-button size="small" text @click="regenerate">重新生成</el-button>
                  </footer>
                </template>
              </div>
            </div>
          </div>

          <form class="composer" @submit.prevent="ask">
            <el-input
              ref="composerEl"
              v-model="question"
              type="textarea"
              :rows="3"
              resize="none"
              maxlength="2000"
              placeholder="输入问题，支持宏观、行业、公司、策略等多维度提问…"
              @keydown="onComposerKey"
            />
            <div class="composer-foot">
              <span class="muted hint">
                <template v-if="errorText"><span class="error-line" role="status">{{ errorText }}</span></template>
                <template v-else>Enter 发送 · Shift + Enter 换行</template>
              </span>
              <div class="composer-right">
                <el-button v-if="streaming" @click="stop">停止</el-button>
                <el-button type="primary" native-type="submit" :loading="sending" :disabled="sending || !question.trim()">
                  发送
                </el-button>
              </div>
            </div>
          </form>
        </div>
        </div>
        </div>
      </PanelCard>

      <PanelCard class="context-card" title="研究上下文" info="可在提问时参考的已入库数据；模型接入后才会被自动引用">
        <template #actions>
          <span v-show="!contextOpen" class="context-peek" :title="contextPeek">{{ contextPeek }}</span>
          <button
            type="button"
            class="context-toggle"
            :aria-expanded="contextOpen"
            aria-controls="assistant-context"
            @click="toggleContext"
          >
            {{ contextOpen ? "收起" : "展开" }}
          </button>
          <span v-if="contextOpen" class="muted side-asof">更新于 {{ dataUpdatedAt || "—" }}</span>
        </template>

        <div v-show="contextOpen" id="assistant-context" class="context-scroll">
        <StateBlock v-if="dataPhase === 'loading'" state="loading" :rows="5" />
        <StateBlock v-else-if="dataPhase === 'error'" state="error" :message="dataError" @retry="loadSidebar" />

        <div v-else class="side-body">
          <section class="side-block">
            <header class="side-head">
              <h3>宏观指标<span v-if="macroSeriesCount" class="count">（{{ macroSeriesCount }}）</span></h3>
              <router-link class="more" to="/macro/us">查看全部 ›</router-link>
            </header>
            <div v-if="macroTiles.length" class="grid-2 tiles">
              <MetricTile
                v-for="tile in macroTiles"
                :key="tile.code"
                :label="tile.label"
                :value="tile.value === null ? null : tile.value.toFixed(2)"
                :period="tile.observationDate"
                :unit="tile.unit"
                delta-label="较上期"
              >
                <template #delta>
                  <DeltaValue
                    :value="tile.change"
                    :suffix="tile.percent ? ' pct' : ''"
                    :colored="false"
                    signed
                    size="sm"
                  />
                </template>
                <template #chart>
                  <MiniSpark :points="tile.spark" kind="line" tone="accent" :width="72" :height="26" />
                </template>
              </MetricTile>
            </div>
            <p v-else class="muted side-note">宏观序列还没有同步到观测值，需管理员在数据中心同步 FRED。</p>
          </section>

          <section class="side-block">
            <header class="side-head">
              <h3>自选股票<span v-if="stocks.length" class="count">（{{ stocks.length }}）</span></h3>
              <router-link class="more" to="/watchlist">管理自选 ›</router-link>
            </header>
            <el-table v-if="stocks.length" :data="stocks" size="small">
              <el-table-column label="证券" min-width="120">
                <template #default="scope">
                  <router-link class="stock-link" :to="`/market/stocks/${scope.row.symbol}`">
                    {{ scope.row.nameCn }}
                  </router-link>
                  <span class="muted stock-code">{{ scope.row.symbol }}</span>
                </template>
              </el-table-column>
              <el-table-column label="最新价" width="86" align="right">
                <template #default="scope">
                  <span class="tabular" :title="scope.row.latestDate ? `收盘日 ${scope.row.latestDate}` : ''">
                    {{ scope.row.latestClose === null ? "—" : scope.row.latestClose.toFixed(2) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="涨跌幅" width="86" align="right">
                <template #default="scope">
                  <DeltaValue
                    :value="scope.row.simpleReturn === null ? null : scope.row.simpleReturn * 100"
                    suffix="%"
                    size="sm"
                  />
                </template>
              </el-table-column>
            </el-table>
            <p v-else class="muted side-note">自选还是空的。加入后，这里显示最新收盘价与涨跌幅。</p>
            <form class="inline-form" @submit.prevent="addSymbol">
              <el-input v-model="symbolInput" size="small" placeholder="代码或名称，例如 600519" />
              <el-button size="small" native-type="submit" :disabled="!symbolInput.trim()">加入自选</el-button>
            </form>
            <p v-if="stockMessage" class="muted side-note" role="status">{{ stockMessage }}</p>
          </section>

          <section class="side-block">
            <header class="side-head">
              <h3>我的文档<span v-if="docs.length" class="count">（{{ docs.length }}）</span></h3>
            </header>
            <ul v-if="docs.length" class="doc-rows">
              <li v-for="doc in docs" :key="doc.id">
                <span class="doc-title">{{ doc.title }}</span>
                <span class="muted doc-meta">{{ doc.uploadedAt }}</span>
                <StatusPill :text="docStatus(doc.parseStatus).text" :tone="docStatus(doc.parseStatus).tone" />
              </li>
            </ul>
            <p v-else class="muted side-note">还没有上传文档。在公司页上传年报或研报后，可在提问时引用。</p>
          </section>

          <section class="side-block">
            <header class="side-head">
              <h3>利率走势</h3>
              <router-link class="more" to="/macro/liquidity">流动性 ›</router-link>
            </header>
            <VChart v-if="macroChartOption" class="side-chart" :option="macroChartOption" autoresize />
            <p v-else class="muted side-note">利率序列的观测值不足，暂不画走势。</p>
          </section>
        </div>
        </div>
      </PanelCard>
    </div>

    <SourceFooter
      class="assistant-foot"
      :as-of="dataUpdatedAt || '—'"
      :sources="['FinScope 问答记录', 'FRED 宏观序列', 'A 股日线']"
      :note="modelAvailable === false ? '模型未接入：回答只有系统说明，不会生成分析或引用' : '回答按原文展示，引用角标对应数据来源'"
    />
  </main>
</template>

<style scoped>
.assistant-page {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--topbar));
  overflow: hidden;
}

.assistant-top,
.assistant-foot {
  flex: none;
}

.assistant-body {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  gap: var(--gap);
  overflow: hidden;
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: auto minmax(0, 1fr);
  grid-template-areas:
    "context"
    "chat";
}

.assistant-body.context-open {
  grid-template-rows: minmax(168px, 34vh) minmax(0, 1fr);
}

.chat-card,
.context-card {
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-card {
  grid-area: chat;
  container-type: inline-size;
  container-name: assistant-chat;
}

.context-card {
  grid-area: context;
}

.chat-card :deep(.head),
.context-card :deep(.head) {
  flex: none;
}

.chat-layout {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: 188px minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: 14px;
  overflow: hidden;
}

.session-pane {
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 12px 12px 12px 0;
  border-right: 1px solid var(--line);
}

.chat-main {
  min-width: 0;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

@container assistant-chat (max-width: 560px) {
  .chat-layout {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 148px) minmax(0, 1fr);
  }

  .session-pane {
    max-height: 148px;
    padding: 8px 0;
    border-right: none;
    border-bottom: 1px solid var(--line);
  }
}

.chat {
  display: flex;
  flex-direction: column;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.model-banner {
  flex: none;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin: 0 0 4px;
  padding: 10px 12px;
  border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--warn) 9%, transparent);
}

.banner-icon {
  flex: none;
  width: 18px;
  height: 18px;
  margin-top: 2px;
  color: var(--warn);
}

.model-banner strong {
  color: var(--warn);
  font-size: 13px;
}

.model-banner p {
  margin: 2px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
}

.thread {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 18px;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 14px 2px;
}

.welcome {
  margin: auto 0;
  display: grid;
  justify-items: center;
  gap: 8px;
  text-align: center;
  padding: 24px 0;
}

.welcome h3 {
  margin: 6px 0 0;
  font-size: 18px;
  color: var(--text-strong);
}

.welcome p {
  margin: 0;
  max-width: 460px;
  line-height: 1.7;
}

.examples {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  width: min(640px, 100%);
}

.example {
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease, background 160ms ease;
}

.example:hover,
.example:focus-visible {
  border-color: var(--accent);
  background: var(--accent-soft);
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

.avatar.large {
  flex-basis: 48px;
  width: 48px;
  height: 48px;
  font-size: 16px;
  box-shadow: 0 0 0 1px var(--edge-light) inset;
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
  max-width: 82%;
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

.asof {
  margin: 8px 0 0;
  font-size: 12px;
}

.reply-actions {
  margin-top: 10px;
}

.composer {
  flex: none;
  border-top: 1px solid var(--line);
  padding: 12px 0 4px;
}

.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}

.hint {
  font-size: 13px;
}

.error-line {
  color: var(--warn);
}

.composer-right {
  display: flex;
  gap: 8px;
}

.side-asof {
  font-size: 12px;
}

.context-toggle {
  flex: none;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  color: var(--accent);
  font-size: 12px;
  line-height: 1.6;
  cursor: pointer;
}

.context-toggle:hover,
.context-toggle:focus-visible {
  border-color: var(--accent);
}

.context-peek {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 12px;
}

.context-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
}

.assistant-body:not(.context-open) .context-card {
  height: auto;
  align-self: start;
  padding-top: 8px;
  padding-bottom: 8px;
}

.assistant-body:not(.context-open) .context-card :deep(.head) {
  margin-bottom: 0;
}

.assistant-body:not(.context-open) .context-card :deep(.actions) {
  min-width: 0;
  flex: 1 1 auto;
  justify-content: flex-end;
  overflow: hidden;
}

.side-body {
  display: grid;
  gap: 14px;
}

.side-block + .side-block {
  border-top: 1px solid var(--line-soft);
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
  color: var(--text-strong);
}

.count {
  color: var(--muted);
  font-weight: 400;
}

.more {
  color: var(--muted);
  font-size: 13px;
  text-decoration: none;
}

.more:hover {
  color: var(--accent);
}

.tiles {
  gap: 10px;
}

.side-note {
  margin: 0 0 8px;
  line-height: 1.7;
}

.stock-link {
  color: var(--text-strong);
  text-decoration: none;
}

.stock-link:hover {
  color: var(--accent);
}

.stock-code {
  margin-left: 6px;
  font-size: 12px;
}

.side-chart {
  height: 190px;
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
  border-bottom: 1px solid var(--line-soft);
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
  margin-top: 8px;
}

@media (min-width: 1180px) {
  .assistant-body,
  .assistant-body.context-open {
    grid-template-columns: minmax(0, 1fr) minmax(300px, 380px);
    grid-template-rows: minmax(0, 1fr);
    grid-template-areas: "chat context";
  }

  .assistant-body:not(.context-open) {
    grid-template-columns: minmax(0, 1fr) 56px;
  }

  .assistant-body:not(.context-open) .context-peek {
    display: none;
  }

  .assistant-body:not(.context-open) .context-card {
    height: 100%;
    align-self: stretch;
    padding: 12px 4px;
    align-items: center;
  }

  .assistant-body:not(.context-open) .context-toggle {
    padding: 6px 4px;
  }

  .assistant-body:not(.context-open) .context-card :deep(.head) {
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    gap: 14px;
    margin-bottom: 0;
    height: 100%;
  }

  .assistant-body:not(.context-open) .context-card :deep(.title) {
    flex-direction: column;
    align-items: center;
  }

  .assistant-body:not(.context-open) .context-card :deep(h2) {
    writing-mode: vertical-rl;
    text-orientation: mixed;
    letter-spacing: 0.18em;
    overflow: visible;
  }

  .assistant-body:not(.context-open) .context-card :deep(.info) {
    display: none;
  }

  .assistant-body:not(.context-open) .context-card :deep(.actions) {
    flex: none;
    overflow: visible;
  }
}

@media (max-width: 767px) {
  .assistant-page {
    height: auto;
    overflow: visible;
  }

  .assistant-body,
  .assistant-body.context-open {
    flex: none;
    overflow: visible;
    grid-template-rows: auto auto;
  }

  .chat-card,
  .context-card,
  .assistant-body.context-open .context-card {
    height: auto;
    overflow: visible;
  }

  .chat-layout,
  .chat-main,
  .chat {
    height: auto;
    overflow: visible;
    flex: none;
  }

  .thread {
    flex: none;
    min-height: 320px;
    max-height: min(70vh, 640px);
  }

  .context-scroll {
    max-height: 50vh;
  }

  .session-pane {
    max-height: 148px;
  }
}
</style>
