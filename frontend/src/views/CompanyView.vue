<script setup lang="ts">
import { onMounted, ref } from "vue"
import { describeResearch, type ResearchMetric } from "../api/research"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("无法读取公司")
const periods = ref<string[]>([])
const periodA = ref("")
const periodB = ref("")
const lines = ref<string[]>([])
const instrumentId = ref("")

async function loadCompany(): Promise<void> {
  phase.value = "loading"
  try {
    const found = await fetch(apiUrl("/api/v1/instruments/search?q=AAPL"))
    const foundBody = await readJson(found)
    if (!found.ok) {
      throw errorFrom(foundBody, found.headers.get("X-Request-ID"))
    }
    const results = asRecord(asRecord(foundBody)?.data)?.results
    const hit = Array.isArray(results) ? asRecord(results[0]) : null
    const id = asString(hit?.id)
    if (!id) {
      phase.value = "error"
      failure.value = "暂无该证券"
      return
    }
    instrumentId.value = id
    await loadResearch()
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadResearch(): Promise<void> {
  if (!instrumentId.value) {
    return
  }
  phase.value = "loading"
  const params = new URLSearchParams({ as_of: "2024-06-01" })
  if (periodA.value && periodB.value && periodA.value !== periodB.value) {
    params.set("period_a", periodA.value)
    params.set("period_b", periodB.value)
  }
  try {
    const response = await fetch(
      apiUrl(`/api/v1/companies/${instrumentId.value}/research?${params}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const available = Array.isArray(data?.available_periods)
      ? data.available_periods.filter((item): item is string => typeof item === "string")
      : []
    periods.value = available
    const hadSelection = Boolean(periodA.value && periodB.value && periodA.value !== periodB.value)
    if (!periodA.value && available[0]) {
      periodA.value = available[0]
    }
    if (!periodB.value && available.length > 1) {
      periodB.value = available[available.length - 1]
    }
    if (!hadSelection && periodA.value && periodB.value && periodA.value !== periodB.value) {
      await loadResearch()
      return
    }
    const metrics = Array.isArray(data?.metrics) ? data.metrics.flatMap(parseMetric) : []
    const unevaluated = Array.isArray(data?.unevaluated)
      ? data.unevaluated.flatMap((item) => {
          const record = asRecord(item)
          return asString(record?.rule_id) ? [asString(record?.rule_id) as string] : []
        })
      : []
    lines.value = describeResearch({
      notice: asString(data?.notice),
      metrics,
      unevaluated,
    })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseMetric(value: unknown): ResearchMetric[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  if (!concept) {
    return []
  }
  return [
    {
      concept,
      earlier: parseSide(record?.earlier),
      later: parseSide(record?.later),
      change: asString(record?.change),
    },
  ]
}

function parseSide(value: unknown): ResearchMetric["earlier"] {
  const record = asRecord(value)
  const amount = asString(record?.value)
  const accession = asString(record?.accession_no)
  const source = asString(record?.source_url)
  const filed = asString(record?.filed)
  if (!amount || !accession || !source || !filed) {
    return null
  }
  return { value: amount, accessionNo: accession, sourceUrl: source, filed }
}

onMounted(() => {
  void loadCompany()
})

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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取公司研究</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取公司研究</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="loadCompany">重试</button>
    </section>
    <section v-else class="panel">
      <h1>公司研究</h1>
      <p class="muted">苹果样例。选择两个不同的报告期后，每个数字都附带来源。没有 AI。</p>
      <form class="form" @submit.prevent="loadResearch">
        <label>
          报告期甲
          <select v-model="periodA" name="period-a">
            <option v-for="period in periods" :key="`a-${period}`" :value="period">{{ period }}</option>
          </select>
        </label>
        <label>
          报告期乙
          <select v-model="periodB" name="period-b">
            <option v-for="period in periods" :key="`b-${period}`" :value="period">{{ period }}</option>
          </select>
        </label>
        <button class="retry" type="submit">对比</button>
      </form>
      <ul v-if="lines.length" class="provider-list">
        <li v-for="line in lines" :key="line">{{ line }}</li>
      </ul>
    </section>
  </main>
</template>
