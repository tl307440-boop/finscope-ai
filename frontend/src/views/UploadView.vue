<script setup lang="ts">
import { ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { pageLabel } from "../api/documentSearch"
import { uploadStatusText } from "../api/uploads"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface Hit {
  chunkId: string
  documentTitle: string
  pageStart: number
  pageEnd: number
  snippet: string
}

const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const message = ref("")
const file = ref<File | null>(null)
const query = ref("")
const hits = ref<Hit[]>([])
const searchMessage = ref("")
const searching = ref(false)

function choose(event: Event): void {
  const input = event.target
  if (!(input instanceof HTMLInputElement)) {
    return
  }
  file.value = input.files?.[0] ?? null
}

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function submit(): Promise<void> {
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录后再上传"
    return
  }
  if (!file.value) {
    phase.value = "error"
    message.value = "请选择 PDF"
    return
  }
  phase.value = "loading"
  message.value = ""
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
      phase.value = "error"
      return
    }
    const data = asRecord(record?.data)
    const status = asString(data?.status) ?? ""
    const documentId = asString(data?.document_id)
    message.value = uploadStatusText(status, asString(data?.message) ?? "")
    phase.value = "ready"
    if (documentId && (status === "queued" || status === "duplicate")) {
      await indexDocument(documentId)
    }
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function indexDocument(documentId: string): Promise<void> {
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
  message.value = asString(data?.message) ?? asString(error?.message) ?? message.value
  if (!response.ok) {
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

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}
</script>

<template>
  <main class="page">
    <section class="panel">
      <h1>上传财报 PDF</h1>
      <p class="muted">只接受 PDF。超过大小或页数、内容不是 PDF、重复文件和扫描件都会给出状态。文字版会按页建立索引。文件不会按原名保存。</p>
      <p v-if="!session.accessToken" class="muted">
        <a href="/login">请先登录</a>
      </p>
      <form class="form" @submit.prevent="submit">
        <label>
          PDF 文件
          <input name="pdf-file" type="file" accept="application/pdf,.pdf" @change="choose" />
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">上传</button>
        <p v-if="phase === 'loading'">正在校验</p>
        <p v-else-if="message" :role="phase === 'error' ? 'alert' : 'status'">{{ message }}</p>
      </form>
    </section>
    <section class="panel">
      <h2>按原文检索</h2>
      <p class="muted">结果给出文件和页码。当前向量是可复现的本地哈希，不是外部语义模型。</p>
      <form class="form" @submit.prevent="runSearch">
        <label>
          原句
          <input name="chunk-query" v-model="query" />
        </label>
        <button class="retry" type="submit" :disabled="searching">检索</button>
        <p v-if="searching">正在检索</p>
        <p v-else-if="searchMessage" role="status">{{ searchMessage }}</p>
      </form>
      <ul v-if="hits.length">
        <li v-for="hit in hits" :key="hit.chunkId">
          <strong>{{ hit.documentTitle }}</strong>
          {{ pageLabel(hit.pageStart, hit.pageEnd) }}
          <p>{{ hit.snippet }}</p>
        </li>
      </ul>
    </section>
  </main>
</template>
