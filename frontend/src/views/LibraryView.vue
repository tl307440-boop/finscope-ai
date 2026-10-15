<script setup lang="ts">
import { onMounted, ref } from "vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { restoreSession, session } from "../stores/auth"

interface ReportRow {
  reportId: string
  runId: string
  title: string
}

interface WatchRow {
  instrumentId: string
  label: string
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const query = ref("")
const reports = ref<ReportRow[]>([])
const groupId = ref("")
const items = ref<WatchRow[]>([])
const symbol = ref("")

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录"
    return
  }
  try {
    await loadReports()
    await loadWatchlist()
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadReports(): Promise<void> {
  const response = await authorizedFetch(
    `/api/v1/analysis/reports?q=${encodeURIComponent(query.value.trim())}`,
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  const rows = asRecord(asRecord(payload)?.data)?.reports
  reports.value = Array.isArray(rows) ? rows.flatMap(readReport) : []
}

async function loadWatchlist(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/watchlists",
    { method: "GET" },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  const groups = asRecord(asRecord(payload)?.data)?.groups
  const first = Array.isArray(groups) ? asRecord(groups[0]) : null
  if (!first) {
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
    const createdBody = await readJson(created)
    groupId.value = asString(asRecord(asRecord(createdBody)?.data)?.id) ?? ""
    items.value = []
    return
  }
  groupId.value = asString(first.id) ?? ""
  const rawItems = first.items
  items.value = Array.isArray(rawItems) ? rawItems.flatMap(readItem) : []
}

async function addSymbol(): Promise<void> {
  const text = symbol.value.trim()
  if (!text || !groupId.value || !session.accessToken) {
    return
  }
  const found = await fetch(apiUrl(`/api/v1/instruments/search?q=${encodeURIComponent(text)}`))
  const foundBody = await readJson(found)
  const results = asRecord(asRecord(foundBody)?.data)?.results
  const hit = Array.isArray(results) ? asRecord(results[0]) : null
  const instrumentId = asString(hit?.id)
  if (!instrumentId) {
    message.value = "暂无该证券"
    return
  }
  const added = await authorizedFetch(
    `/api/v1/watchlists/${groupId.value}/items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ instrument_id: instrumentId }),
    },
    session.accessToken,
    refreshToken,
  )
  if (!added.ok) {
    message.value = "无法加入自选"
    return
  }
  symbol.value = ""
  message.value = ""
  await loadWatchlist()
}

async function removeItem(instrumentId: string): Promise<void> {
  if (!session.accessToken || !groupId.value) {
    return
  }
  await authorizedFetch(
    `/api/v1/watchlists/${groupId.value}/items/${instrumentId}`,
    { method: "DELETE" },
    session.accessToken,
    refreshToken,
  )
  await loadWatchlist()
}

async function hideReport(reportId: string): Promise<void> {
  if (!session.accessToken) {
    return
  }
  await authorizedFetch(
    `/api/v1/analysis/reports/${reportId}`,
    { method: "DELETE" },
    session.accessToken,
    refreshToken,
  )
  await loadReports()
}

function readReport(value: unknown): ReportRow[] {
  const row = asRecord(value)
  const reportId = asString(row?.report_id)
  const runId = asString(row?.run_id)
  const title = asString(row?.title)
  if (!reportId || !runId || !title) {
    return []
  }
  return [{ reportId, runId, title }]
}

function readItem(value: unknown): WatchRow[] {
  const row = asRecord(value)
  const instrumentId = asString(row?.instrument_id)
  const name = asString(row?.name_cn)
  const symbolText = asString(row?.display_symbol)
  if (!instrumentId || !name || !symbolText) {
    return []
  }
  return [{ instrumentId, label: `${name} ${symbolText}` }]
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

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取资料库</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取资料库</h1>
      <p>{{ message }}</p>
    </section>
    <template v-else>
      <section class="panel">
        <h1>研究记录</h1>
        <form class="form" @submit.prevent="loadReports">
          <label>
            标题筛选
            <input name="report-query" v-model="query" />
          </label>
          <button class="retry" type="submit">筛选</button>
        </form>
        <p v-if="!reports.length" class="muted">没有符合条件的研究记录。</p>
        <ul v-else>
          <li v-for="report in reports" :key="report.reportId">
            <a :href="`/reports/${report.runId}`">{{ report.title }}</a>
            <button type="button" @click="hideReport(report.reportId)">删除</button>
          </li>
        </ul>
      </section>
      <section class="panel">
        <h2>自选</h2>
        <form class="form" @submit.prevent="addSymbol">
          <label>
            代码或名称
            <input name="watch-symbol" v-model="symbol" />
          </label>
          <button class="retry" type="submit">加入</button>
        </form>
        <p v-if="message" role="status">{{ message }}</p>
        <p v-if="!items.length" class="muted">还没有自选证券。</p>
        <ul v-else>
          <li v-for="item in items" :key="item.instrumentId">
            {{ item.label }}
            <button type="button" @click="removeItem(item.instrumentId)">移出</button>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>
