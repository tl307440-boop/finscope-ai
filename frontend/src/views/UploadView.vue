<script setup lang="ts">
/**
 * 上传财报 PDF（`POST /api/v1/documents` + `POST /api/v1/documents/{id}/index` + `GET /api/v1/documents/search`）。
 *
 * 硬性口径（与后端一致）：仅支持 PDF、≤30MB、≤200 页；内容不是 PDF、
 * 扫描件（需要文字版）、重复文件都在上传阶段就被拒或标记，并回显后端原文。
 * 上传成功后必须显式调一次索引接口，所以这里把「校验 → 解析 → 建立索引 → 完成」
 * 如实分步展示：每一步只写后端真正返回的状态，进度不做百分比估算。
 */
import { computed, ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { pageLabel } from "../api/documentSearch"
import { uploadStatusText } from "../api/uploads"
import { restoreSession, useSessionStore } from "../stores/auth"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
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

type Stage = "idle" | "validating" | "parsing" | "indexing" | "done" | "error"

const session = useSessionStore()

const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const stage = ref<Stage>("idle")
const message = ref("")
const indexMessage = ref("")
const file = ref<File | null>(null)
const query = ref("")
const hits = ref<Hit[]>([])
const searchMessage = ref("")
const searching = ref(false)

const STAGE_STEPS: { key: Stage; label: string }[] = [
  { key: "validating", label: "校验格式与大小" },
  { key: "parsing", label: "解析 PDF 文本" },
  { key: "indexing", label: "按页建立索引" },
  { key: "done", label: "完成" },
]

const STAGE_ORDER: Stage[] = ["idle", "validating", "parsing", "indexing", "done"]

const scopeLabel = computed(() =>
  props.symbol ? `${props.symbol} · 上传的财报只用于该标的的检索` : "上传的财报只用于站内按原文检索",
)

const selectedLabel = computed(() => {
  const picked = file.value
  if (!picked) {
    return "还没有选择文件"
  }
  const size = picked.size / (1024 * 1024)
  return `${picked.name} · ${size.toFixed(2)}MB`
})

/** 本地预检只为提示，最终判定以后端返回的 status / error 为准。 */
const selectedTone = computed<"ok" | "warn" | "muted">(() => {
  const picked = file.value
  if (!picked) {
    return "muted"
  }
  const okType = picked.type === "application/pdf" || picked.name.toLowerCase().endsWith(".pdf")
  const okSize = picked.size <= 30 * 1024 * 1024
  return okType && okSize ? "ok" : "warn"
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

function choose(event: Event): void {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) {
    return
  }
  file.value = input.files?.[0] ?? null
  indexMessage.value = ""
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
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
    message.value = "请选择 PDF"
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
    message.value = "无法连接接口"
    stage.value = "error"
    phase.value = "error"
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
    indexMessage.value = "索引请求无法连接接口"
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
    searchMessage.value = hits.value.length ? "已定位到下面的页" : "没有找到对应页"
  } catch {
    searchMessage.value = "无法连接接口"
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

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}
</script>

<template>
  <main class="page">
    <PageHeader title="上传财报 PDF" subtitle="只接受 PDF · 单份 ≤30MB · ≤200 页 · 不做 OCR">
      <template #actions>
        <a v-if="!session.accessToken" class="login-link" href="/login">请先登录</a>
        <StatusPill v-else tone="ok" :text="session.username ?? '已登录'" />
      </template>
    </PageHeader>

    <div class="grid-1-2">
      <PanelCard title="选择文件" :info="scopeLabel">
        <form class="form" @submit.prevent="submit">
          <label class="field">
            PDF 文件
            <input name="pdf-file" type="file" accept="application/pdf,.pdf" @change="choose" />
          </label>
          <p class="picked">
            <StatusPill :tone="selectedTone" :text="selectedLabel" />
          </p>
          <ul class="limits">
            <li>仅支持 PDF：扩展名与文件头都要是 PDF，<code>.docx</code>／图片一律拒绝。</li>
            <li>单份 ≤30MB，超过会在上传阶段直接拒收。</li>
            <li>≤200 页，超页数的文件不入库。</li>
            <li>扫描件（纯图片 PDF）不做 OCR，会标记为「需要文字版」。</li>
            <li>同一份文件重复上传会返回「已经上传过」，文件不按原名保存。</li>
          </ul>
          <button class="retry" type="submit" :disabled="phase === 'loading'">上传并解析</button>
        </form>
      </PanelCard>

      <PanelCard title="解析与索引进度" info="每一步都只写后端真正返回的状态，不做百分比估算">
        <ol class="steps">
          <li v-for="item in STAGE_STEPS" :key="item.key" :class="`is-${stageState(item.key)}`">
            <span class="dot" aria-hidden="true" />
            <span>{{ item.label }}</span>
          </li>
        </ol>
        <p v-if="stage === 'idle'" class="muted stage-line">选择文件后点「上传并解析」。</p>
        <p v-else-if="message" class="stage-line" :role="phase === 'error' ? 'alert' : 'status'">
          {{ message }}
        </p>
        <p v-if="indexMessage" class="stage-line muted">索引：{{ indexMessage }}</p>
        <p v-if="stage === 'error'" class="muted stage-line">
          失败的文件已经记录，可以在数据管理页看到原因；不会自动重试。
        </p>
      </PanelCard>
    </div>

    <PanelCard title="按原文检索" info="GET /api/v1/documents/search · 向量是可复现的本地哈希，不是外部语义模型">
      <form class="search-form" @submit.prevent="runSearch">
        <label class="field grow">
          原句
          <input v-model="query" name="chunk-query" placeholder="粘贴报告里的一句话" />
        </label>
        <button class="retry" type="submit" :disabled="searching">
          {{ searching ? "检索中…" : "检索" }}
        </button>
      </form>

      <div v-if="searching" class="search-state"><StateBlock state="loading" :rows="2" /></div>
      <StateBlock v-else-if="searchMessage === '没有找到对应页'" state="empty" :message="searchMessage" />
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

    <SourceFooter
      :as-of="selectedLabel"
      :sources="['POST /api/v1/documents', 'POST /api/v1/documents/{id}/index', 'GET /api/v1/documents/search']"
      :note="indexMessage || '仅支持 PDF、≤30MB、≤200 页；扫描件不做 OCR，入库文件不按原名保存'"
    />
  </main>
</template>

<style scoped>
.form {
  display: grid;
  gap: 12px;
  justify-items: start;
}

.field {
  display: grid;
  gap: 4px;
  width: 100%;
  font-size: 12px;
  color: var(--muted);
}

.field input {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
}

.picked {
  margin: 0;
}

.limits {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
}

code {
  font-size: 12px;
  color: var(--accent);
}

.steps {
  margin: 0 0 12px;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  font-size: 13px;
}

.steps li {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--muted);
}

.steps .dot {
  width: 10px;
  height: 10px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--line);
}

.steps .is-done {
  color: var(--ok);
}

.steps .is-done .dot {
  background: var(--ok);
  border-color: var(--ok);
}

.steps .is-active {
  color: var(--accent);
  font-weight: 600;
}

.steps .is-active .dot {
  background: var(--accent);
  border-color: var(--accent);
}

.steps .is-failed {
  color: var(--danger);
}

.steps .is-failed .dot {
  background: var(--danger);
  border-color: var(--danger);
}

.stage-line {
  margin: 0 0 6px;
  font-size: 13px;
}

.search-form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.grow {
  flex: 1 1 260px;
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
  gap: 12px;
}

.hit-list li {
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
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
  color: var(--muted);
}

.login-link {
  color: var(--accent);
  font-size: 13px;
}
</style>
