<script setup lang="ts">
/**
 * 上传财报 PDF（`POST /api/v1/documents` + `POST /api/v1/documents/{id}/index` + `GET /api/v1/documents/search`），
 * 并列出当前账号已上传的文件（`GET /api/v1/documents`）。
 *
 * 硬性口径（与后端一致）：仅支持 PDF、≤30MB、≤200 页；内容不是 PDF、
 * 扫描件（需要文字版）、重复文件都在上传阶段就被拒或标记，并回显后端原文。
 * 上传成功后必须显式调一次索引接口，所以这里把「校验 → 解析 → 建立索引 → 完成」
 * 如实分步展示：每一步只写后端真正返回的状态，进度不做百分比估算。
 */
import { computed, onMounted, ref } from "vue"
import { Document, Files, Search, UploadFilled } from "@element-plus/icons-vue"
import { authorizedFetch, readJson } from "../api/client"
import { pageLabel } from "../api/documentSearch"
import { formatShanghai } from "../api/health"
import { documentStatus, uploadStatusText } from "../api/uploads"
import { restoreSession, useSessionStore } from "../stores/auth"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { asNumber, asString } from "../api/json"

const props = defineProps<{ symbol?: string }>()

interface Hit {
  chunkId: string
  documentTitle: string
  pageStart: number
  pageEnd: number
  snippet: string
}

interface UploadedDocument {
  id: string
  title: string
  pageCount: number | null
  parseStatus: string
  parseError: string | null
  uploadedAt: string | null
}

type Stage = "idle" | "validating" | "parsing" | "indexing" | "done" | "error"

const MAX_BYTES = 30 * 1024 * 1024

const session = useSessionStore()

const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const stage = ref<Stage>("idle")
const message = ref("")
const indexMessage = ref("")
const file = ref<File | null>(null)
const dragging = ref(false)
const query = ref("")
const hits = ref<Hit[]>([])
const searchMessage = ref("")
const searching = ref(false)
const documents = ref<UploadedDocument[]>([])
const documentsPhase = ref<"loading" | "ready" | "error">("loading")
const documentsFailure = ref("")

const STAGE_STEPS: { key: Stage; label: string }[] = [
  { key: "validating", label: "校验格式与大小" },
  { key: "parsing", label: "解析 PDF 文本" },
  { key: "indexing", label: "按页建立索引" },
  { key: "done", label: "完成" },
]

const STAGE_ORDER: Stage[] = ["idle", "validating", "parsing", "indexing", "done"]

const loginHref = computed(() => {
  const target = props.symbol ? `/company/${encodeURIComponent(props.symbol)}/uploads` : "/"
  return `/login?redirect=${encodeURIComponent(target)}`
})

const subtitle = computed(() =>
  props.symbol
    ? `${props.symbol} · 上传后可按原文检索，并定位到具体页码`
    : "上传后可按原文检索，并定位到具体页码",
)

const fileSize = computed(() => {
  const picked = file.value
  return picked ? `${(picked.size / (1024 * 1024)).toFixed(2)} MB` : ""
})

/** 本地预检只为提示，最终判定以后端返回的 status / error 为准。 */
const precheckWarning = computed<string>(() => {
  const picked = file.value
  if (!picked) {
    return ""
  }
  const okType = picked.type === "application/pdf" || picked.name.toLowerCase().endsWith(".pdf")
  if (!okType) {
    return "这不是 PDF 文件，上传会被拒收"
  }
  if (picked.size > MAX_BYTES) {
    return "文件超过 30MB，上传会被拒收"
  }
  return ""
})

const stageIndex = computed(() => STAGE_ORDER.indexOf(stage.value))

function stageState(key: Stage): "done" | "active" | "pending" | "failed" {
  if (stage.value === "error") {
    return "failed"
  }
  const target = STAGE_ORDER.indexOf(key)
  if (target < stageIndex.value) {
    return "done"
  }
  if (target === stageIndex.value) {
    return "active"
  }
  return "pending"
}

function pick(next: File | null): void {
  file.value = next
  indexMessage.value = ""
  if (stage.value === "done" || stage.value === "error") {
    stage.value = "idle"
    message.value = ""
  }
}

function choose(event: Event): void {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) {
    return
  }
  pick(input.files?.[0] ?? null)
}

function drop(event: DragEvent): void {
  dragging.value = false
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) {
    pick(dropped)
  }
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadDocuments(): Promise<void> {
  if (!session.accessToken) {
    documentsPhase.value = "ready"
    documents.value = []
    return
  }
  documentsPhase.value = "loading"
  try {
    const response = await authorizedFetch("/api/v1/documents", { method: "GET" }, session.accessToken, refreshToken)
    const payload = await readJson(response)
    const record = asRecord(payload)
    if (!response.ok) {
      documentsFailure.value = asString(asRecord(record?.error)?.message) ?? "无法读取已上传的文件"
      documentsPhase.value = "error"
      return
    }
    documents.value = readDocuments(asRecord(record?.data)?.documents)
    documentsPhase.value = "ready"
  } catch {
    documentsFailure.value = "无法连接服务，请稍后重试"
    documentsPhase.value = "error"
  }
}

async function submit(): Promise<void> {
  if (!session.accessToken) {
    stage.value = "error"
    phase.value = "error"
    message.value = "请先登录后再上传"
    return
  }
  if (!file.value) {
    stage.value = "error"
    phase.value = "error"
    message.value = "请先选择一份 PDF"
    return
  }
  stage.value = "validating"
  phase.value = "loading"
  message.value = ""
  indexMessage.value = ""
  const body = new FormData()
  body.set("file", file.value)
  try {
    const response = await authorizedFetch(
      "/api/v1/documents",
      { method: "POST", body },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const record = asRecord(payload)
    if (!response.ok) {
      const error = asRecord(record?.error)
      message.value = asString(error?.message) ?? "无法上传"
      stage.value = "error"
      phase.value = "error"
      return
    }
    stage.value = "parsing"
    const data = asRecord(record?.data)
    const status = asString(data?.status) ?? ""
    const documentId = asString(data?.document_id)
    message.value = uploadStatusText(status, asString(data?.message) ?? "")
    phase.value = "ready"
    if (documentId && (status === "queued" || status === "duplicate")) {
      await indexDocument(documentId)
    } else {
      stage.value = "done"
    }
  } catch {
    message.value = "无法连接服务，请稍后重试"
    stage.value = "error"
    phase.value = "error"
  } finally {
    void loadDocuments()
  }
}

async function indexDocument(documentId: string): Promise<void> {
  stage.value = "indexing"
  try {
    const response = await authorizedFetch(
      `/api/v1/documents/${documentId}/index`,
      { method: "POST" },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const record = asRecord(payload)
    const data = asRecord(record?.data)
    const error = asRecord(record?.error)
    indexMessage.value = asString(data?.message) ?? asString(error?.message) ?? ""
    if (!response.ok) {
      stage.value = "error"
      phase.value = "error"
      return
    }
    stage.value = "done"
  } catch {
    indexMessage.value = "建立索引时无法连接服务"
    stage.value = "error"
    phase.value = "error"
  }
}

async function runSearch(): Promise<void> {
  searchMessage.value = ""
  hits.value = []
  if (!session.accessToken) {
    searchMessage.value = "请先登录后再检索"
    return
  }
  const text = query.value.trim()
  if (!text) {
    searchMessage.value = "请输入要检索的文字"
    return
  }
  searching.value = true
  try {
    const response = await authorizedFetch(
      `/api/v1/documents/search?q=${encodeURIComponent(text)}`,
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    const record = asRecord(payload)
    if (!response.ok) {
      const error = asRecord(record?.error)
      searchMessage.value = asString(error?.message) ?? "无法检索"
      return
    }
    const data = asRecord(record?.data)
    hits.value = readHits(data?.items)
    searchMessage.value = hits.value.length ? `找到 ${hits.value.length} 处匹配` : "没有找到对应页"
  } catch {
    searchMessage.value = "无法连接服务，请稍后重试"
  } finally {
    searching.value = false
  }
}

function readHits(value: unknown): Hit[] {
  if (!Array.isArray(value)) {
    return []
  }
  const items: Hit[] = []
  for (const entry of value) {
    const row = asRecord(entry)
    const chunkId = asString(row?.chunk_id)
    const documentTitle = asString(row?.document_title)
    const pageStart = asNumber(row?.page_start)
    const pageEnd = asNumber(row?.page_end)
    const snippet = asString(row?.snippet) ?? ""
    if (!chunkId || !documentTitle || pageStart === null || pageEnd === null) {
      continue
    }
    items.push({ chunkId, documentTitle, pageStart, pageEnd, snippet })
  }
  return items
}

function readDocuments(value: unknown): UploadedDocument[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((entry) => {
    const row = asRecord(entry)
    const id = asString(row?.id)
    if (!id) {
      return []
    }
    return [
      {
        id,
        title: asString(row?.title) ?? "未命名文件",
        pageCount: asNumber(row?.page_count),
        parseStatus: asString(row?.parse_status) ?? "",
        parseError: asString(row?.parse_error),
        uploadedAt: asString(row?.uploaded_at),
      },
    ]
  })
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

onMounted(() => {
  void loadDocuments()
})
</script>

<template>
  <main class="page">
    <PageHeader title="上传财报 PDF" :subtitle="subtitle">
      <template #actions>
        <a v-if="!session.accessToken" class="login-link" :href="loginHref">请先登录</a>
        <StatusPill v-else tone="ok" :text="session.username ?? '已登录'" dot />
      </template>
    </PageHeader>

    <div class="stack">
      <div class="grid-1-2">
        <PanelCard title="选择文件" info="仅支持文字版 PDF；文件不按原名保存">
          <template #icon><el-icon><UploadFilled /></el-icon></template>
          <form class="form" @submit.prevent="submit">
            <label
              class="drop"
              :class="{ picked: file, dragging }"
              @dragover.prevent="dragging = true"
              @dragleave="dragging = false"
              @drop.prevent="drop"
            >
              <input class="sr-only" name="pdf-file" type="file" accept="application/pdf,.pdf" @change="choose" />
              <el-icon class="drop-icon" aria-hidden="true"><Document v-if="file" /><UploadFilled v-else /></el-icon>
              <span class="drop-title">{{ file ? file.name : "点击选择或拖入 PDF 文件" }}</span>
              <span class="drop-sub">{{ file ? fileSize : "单份 ≤30MB · ≤200 页" }}</span>
            </label>
            <p v-if="precheckWarning" class="warn-line" role="alert">{{ precheckWarning }}</p>
            <ul class="limits">
              <li>只接受 PDF，Word 文档和图片会被拒收。</li>
              <li>单份不超过 30MB、200 页，超出的文件不入库。</li>
              <li>扫描件（纯图片 PDF）不做文字识别，会标记为「需要文字版」。</li>
              <li>同一份文件重复上传会提示「已经上传过」。</li>
            </ul>
            <el-button type="primary" native-type="submit" :loading="phase === 'loading'" :disabled="!file">
              上传并解析
            </el-button>
          </form>
        </PanelCard>

        <PanelCard title="解析与索引进度" info="按实际处理结果逐步更新，不估算百分比">
          <template #icon><el-icon><Files /></el-icon></template>
          <ol class="steps">
            <li v-for="(item, index) in STAGE_STEPS" :key="item.key" :class="`is-${stageState(item.key)}`">
              <span class="step-no" aria-hidden="true">{{ index + 1 }}</span>
              <span>{{ item.label }}</span>
            </li>
          </ol>
          <p v-if="stage === 'idle'" class="muted stage-line">选择文件后点「上传并解析」。</p>
          <p v-else-if="message" class="stage-line" :class="{ danger: stage === 'error' }" :role="phase === 'error' ? 'alert' : 'status'">
            {{ message }}
          </p>
          <p v-if="indexMessage" class="stage-line muted">索引：{{ indexMessage }}</p>
          <p v-if="stage === 'error'" class="muted stage-line">失败的文件不会自动重试，修正后可以重新上传。</p>
        </PanelCard>
      </div>

      <PanelCard title="我的上传" info="当前账号上传过的 PDF，按上传时间排列" flush>
        <template #icon><el-icon><Document /></el-icon></template>
        <template #actions>
          <span v-if="documentsPhase === 'ready'" class="count">共 {{ documents.length }} 份</span>
        </template>
        <StateBlock v-if="documentsPhase === 'loading'" state="loading" :rows="2" />
        <StateBlock
          v-else-if="documentsPhase === 'error'"
          state="error"
          :message="documentsFailure"
          @retry="loadDocuments"
        />
        <StateBlock
          v-else-if="!documents.length"
          state="empty"
          :message="session.accessToken ? '还没有上传过 PDF，上传后会出现在这里' : '登录后可以查看自己上传的文件'"
        />
        <el-table v-else :data="documents" size="small" style="width: 100%">
          <el-table-column label="文件" min-width="260">
            <template #default="{ row }">
              <span class="cell-strong">{{ row.title }}</span>
              <span v-if="row.parseError" class="cell-sub danger">{{ row.parseError }}</span>
            </template>
          </el-table-column>
          <el-table-column label="页数" width="90" align="right">
            <template #default="{ row }">
              <span class="tabular">{{ row.pageCount ?? "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="130">
            <template #default="{ row }">
              <StatusPill :text="documentStatus(row.parseStatus).text" :tone="documentStatus(row.parseStatus).tone" dot />
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="190">
            <template #default="{ row }">
              <span class="tabular">{{ formatShanghai(row.uploadedAt ?? undefined) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <PanelCard title="按原文检索" info="粘贴报告中的一句话，定位到已解析 PDF 的具体页码；使用本地文本匹配，不调用外部模型">
        <template #icon><el-icon><Search /></el-icon></template>
        <form class="search-form" @submit.prevent="runSearch">
          <el-input
            v-model="query"
            name="chunk-query"
            placeholder="粘贴报告里的一句话"
            aria-label="检索原句"
            clearable
          />
          <el-button type="primary" native-type="submit" :loading="searching">检索</el-button>
        </form>

        <div v-if="searching" class="search-state"><StateBlock state="loading" :rows="2" /></div>
        <StateBlock v-else-if="searchMessage === '没有找到对应页'" class="search-state" state="empty" :message="searchMessage" />
        <p v-else-if="searchMessage" class="muted search-msg" role="status">{{ searchMessage }}</p>

        <ul v-if="hits.length" class="hit-list">
          <li v-for="hit in hits" :key="hit.chunkId">
            <p class="hit-head">
              <strong>{{ hit.documentTitle }}</strong>
              <span class="muted">{{ pageLabel(hit.pageStart, hit.pageEnd) }}</span>
            </p>
            <p class="hit-snippet">{{ hit.snippet }}</p>
          </li>
        </ul>
      </PanelCard>
    </div>
  </main>
</template>

<style scoped>
.form {
  display: grid;
  gap: 12px;
  justify-items: start;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.drop {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 4px;
  width: 100%;
  padding: 20px 16px;
  border: 1px dashed color-mix(in srgb, var(--accent) 45%, var(--line));
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--accent) 6%, transparent);
  cursor: pointer;
  text-align: center;
  transition:
    border-color 160ms ease,
    background 160ms ease;
}

.drop:hover,
.drop.dragging,
.drop:focus-within {
  border-color: var(--accent);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}

.drop.picked {
  border-style: solid;
}

.drop-icon {
  font-size: 26px;
  color: var(--accent);
}

.drop-title {
  max-width: 100%;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drop-sub {
  font-size: 13px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

.warn-line {
  margin: 0;
  font-size: 13px;
  color: var(--warn);
}

.limits {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

.steps {
  margin: 0 0 14px;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
  font-size: 13px;
}

.steps li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  color: var(--muted);
}

.step-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line);
  font-family: var(--font-num);
  font-size: 12px;
}

.steps .is-done {
  color: var(--ok);
}

.steps .is-done .step-no {
  background: color-mix(in srgb, var(--ok) 18%, transparent);
  border-color: var(--ok);
}

.steps .is-active {
  color: var(--accent);
  font-weight: 600;
}

.steps .is-active .step-no {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border-color: var(--accent);
}

.steps .is-failed {
  color: var(--danger);
}

.steps .is-failed .step-no {
  border-color: var(--danger);
}

.stage-line {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.6;
}

.stage-line.danger {
  color: var(--danger);
}

.count {
  font-size: 13px;
  color: var(--muted);
}

.cell-strong {
  display: block;
  font-size: 13px;
  color: var(--text-strong);
}

.cell-sub {
  display: block;
  font-size: 12px;
  color: var(--muted);
}

.cell-sub.danger {
  color: var(--danger);
}

.search-form {
  display: flex;
  align-items: center;
  gap: 10px;
}

.search-form :deep(.el-input) {
  flex: 1 1 auto;
}

.search-state,
.search-msg {
  margin-top: 12px;
  font-size: 13px;
}

.hit-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  display: grid;
}

.hit-list li {
  padding: 12px 0;
  border-top: 1px solid var(--line-soft);
}

.hit-list li:first-child {
  border-top: 0;
}

.hit-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0 0 4px;
  font-size: 13px;
}

.hit-snippet {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}

.login-link {
  color: var(--accent);
  font-size: 13px;
}
</style>
