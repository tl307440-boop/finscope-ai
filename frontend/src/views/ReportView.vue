<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute } from "vue-router"
import { authorizedFetch, readJson } from "../api/client"
import { restoreSession, session } from "../stores/auth"

const route = useRoute()
const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const title = ref("")
const body = ref("")
const notice = ref("")

async function load(): Promise<void> {
  phase.value = "loading"
  const runId = typeof route.params.runId === "string" ? route.params.runId : ""
  if (!session.accessToken || !runId) {
    phase.value = "error"
    message.value = "请先登录后再查看研究记录"
    return
  }
  try {
    const response = await authorizedFetch(
      `/api/v1/analysis/runs/${runId}`,
      { method: "GET" },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const payload = await readJson(response)
    const data = asRecord(asRecord(payload)?.data)
    if (!response.ok || !data) {
      const error = asRecord(asRecord(payload)?.error)
      message.value = asString(error?.message) ?? "找不到这份研究记录"
      phase.value = "error"
      return
    }
    title.value = asString(data.title) ?? "研究记录"
    body.value = asString(data.body) ?? ""
    notice.value = asString(data.notice) ?? ""
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取研究记录</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取研究记录</h1>
      <p>{{ message }}</p>
    </section>
    <section v-else class="panel">
      <h1>{{ title }}</h1>
      <p class="muted">{{ notice }}</p>
      <pre class="report-body">{{ body }}</pre>
    </section>
  </main>
</template>

<style scoped>
.report-body {
  white-space: pre-wrap;
}
</style>
