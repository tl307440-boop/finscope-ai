<script setup lang="ts">
import { ref } from "vue"
import { describeFacts, type SecFactLine } from "../api/secFacts"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"

const asOf = ref("2024-03-01")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")
const lines = ref<string[]>([])

async function load(): Promise<void> {
  if (!asOf.value) {
    phase.value = "error"
    failure.value = "请选择日期"
    lines.value = []
    return
  }
  phase.value = "loading"
  failure.value = ""
  lines.value = []
  try {
    const response = await fetch(
      apiUrl(`/api/v1/sec/facts?cik=320193&as_of=${encodeURIComponent(asOf.value)}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const facts = Array.isArray(data?.facts) ? data.facts.flatMap(parseFact) : []
    lines.value = describeFacts({ notice: asString(data?.notice), facts })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseFact(value: unknown): SecFactLine[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  const unit = asString(record?.unit)
  const amount = asString(record?.value)
  const filed = asString(record?.filed)
  const accession = asString(record?.accession_no)
  const source = asString(record?.source_url)
  const form = asString(record?.form)
  if (!concept || !unit || !amount || !filed || !accession || !source || !form) {
    return []
  }
  return [
    {
      concept,
      unit,
      value: amount,
      filed,
      accessionNo: accession,
      sourceUrl: source,
      form,
    },
  ]
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
</script>

<template>
  <main class="page">
    <section class="panel">
      <h1>SEC 样例</h1>
      <p class="muted">CIK 0000320193。这是随仓库附带的解析样例，不是本次向 SEC 拉取的数据。</p>
      <form class="form" @submit.prevent="load">
        <label>
          观察日
          <input v-model="asOf" name="sec-as-of" type="date" />
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">查看来源</button>
        <p v-if="phase === 'loading'">正在解析样例</p>
        <p v-else-if="phase === 'error'" role="alert">{{ failure }}</p>
        <ul v-else-if="lines.length" class="provider-list">
          <li v-for="line in lines" :key="line">{{ line }}</li>
        </ul>
      </form>
    </section>
  </main>
</template>
