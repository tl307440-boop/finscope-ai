<script setup lang="ts">
import { onMounted, ref } from "vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

const hotspot = ref("")
const china = ref<string[]>([])
const digest = ref<string[]>([])
const phase = ref<"loading" | "ready" | "error">("loading")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const hot = await fetch(apiUrl("/api/v1/market/hotspots"))
    const hotBody = await readJson(hot)
    hotspot.value = asString(asRecord(asRecord(hotBody)?.data)?.label) ?? ""
    const macro = await fetch(apiUrl("/api/v1/macro/china"))
    const macroBody = await readJson(macro)
    const series = asRecord(asRecord(macroBody)?.data)?.series
    china.value = Array.isArray(series)
      ? series.flatMap((item) => {
          const row = asRecord(item)
          const name = asString(row?.name)
          const status = asString(row?.status)
          return name && status ? [`${name}：${status === "unavailable" ? "不可用" : status}`] : []
        })
      : []
    if (session.accessToken) {
      const note = await authorizedFetch(
        "/api/v1/me/digest",
        { method: "GET" },
        session.accessToken,
        async () => {
          const restored = await restoreSession()
          return restored ? session.accessToken : null
        },
      )
      const noteBody = await readJson(note)
      const items = asRecord(asRecord(noteBody)?.data)?.items
      digest.value = Array.isArray(items)
        ? items.flatMap((item) => {
            const row = asRecord(item)
            const title = asString(row?.title)
            const summary = asString(row?.summary)
            return title && summary ? [`${title}：${summary}`] : []
          })
        : []
    }
    phase.value = "ready"
  } catch {
    phase.value = "error"
  }
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取研究板</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert"><h1>无法读取研究板</h1></section>
    <template v-else>
      <section class="panel">
        <h1>公告热度</h1>
        <p>{{ hotspot }}</p>
      </section>
      <section class="panel">
        <h2>中国宏观</h2>
        <ul>
          <li v-for="item in china" :key="item">{{ item }}</li>
        </ul>
      </section>
      <section class="panel">
        <h2>站内摘要</h2>
        <p v-if="!session.accessToken" class="muted">登录后可以看今天的站内摘要。</p>
        <ul v-else>
          <li v-for="item in digest" :key="item">{{ item }}</li>
        </ul>
      </section>
    </template>
  </main>
</template>
