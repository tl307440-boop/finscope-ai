<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useRoute } from "vue-router"
import { authorizedFetch, readJson } from "../api/client"
import { evidenceLinks, markdownFilename, type EvidenceLink } from "../api/reportEvidence"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

const route = useRoute()
const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const title = ref("")
const body = ref("")
const notice = ref("")
const links = ref<EvidenceLink[]>([])
const selected = ref<EvidenceLink | null>(null)

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
    links.value = evidenceLinks(body.value)
    selected.value = null
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
      <button class="retry" type="button" @click="downloadMarkdown">下载 Markdown</button>
      <button class="retry" type="button" @click="printReport">打印为 PDF</button>
      <h2>核对数字</h2>
      <p v-if="!links.length" class="muted">这份记录没有可核对的申报数字。</p>
      <ul v-else>
        <li v-for="link in links" :key="link.key">
          <button type="button" @click="selected = link">{{ link.label }}</button>
          <a
            class="source-link"
            :href="link.sourceUrl"
            target="_blank"
            rel="noreferrer noopener"
          >来源</a>
        </li>
      </ul>
      <aside v-if="selected" class="panel" role="dialog" aria-label="数字来源">
        <p>数值 {{ selected.value }}</p>
        <p>申报号 {{ selected.accession }}</p>
        <p>
          来源
          <a
            class="source-link"
            :href="selected.sourceUrl"
            target="_blank"
            rel="noreferrer noopener"
          >{{ selected.sourceUrl }}</a>
        </p>
        <button type="button" @click="selected = null">关闭</button>
      </aside>
      <pre class="report-body">{{ body }}</pre>
    </section>
  </main>
</template>

<style scoped>
.report-body {
  white-space: pre-wrap;
}

/* 规格书 §1.4 的财报引用要给出可点的「来源 URL」，§1.3 的报告页要求 PDFDeepLink。 */
.source-link {
  margin-left: 8px;
  color: var(--accent);
  font-size: 13px;
}
</style>
