<script setup lang="ts">
import { onMounted, ref } from "vue"
import { apiUrl, readJson } from "../api/client"

const phase = ref<"loading" | "ready" | "error">("loading")
const label = ref("")
const coverage = ref("")
const notice = ref("")
const sectors = ref<string[]>([])

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await fetch(apiUrl("/api/v1/market/temperature"))
    const payload = await readJson(response)
    const data = asRecord(asRecord(payload)?.data)
    label.value = asString(data?.label) ?? "无法判断"
    coverage.value = asString(data?.coverage) ?? ""
    notice.value = asString(data?.notice) ?? ""
    const sectorResponse = await fetch(apiUrl("/api/v1/market/sectors"))
    const sectorPayload = await readJson(sectorResponse)
    const rows = asRecord(asRecord(sectorPayload)?.data)?.sectors
    sectors.value = Array.isArray(rows)
      ? rows.flatMap((item) => {
          const row = asRecord(item)
          const name = asString(row?.name)
          const text = asString(row?.label)
          return name && text ? [`${name}：${text}`] : []
        })
      : []
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在计算市场温度</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法计算市场温度</h1>
    </section>
    <section v-else class="panel">
      <h1>市场温度</h1>
      <p role="status">{{ label }}</p>
      <p class="muted">{{ coverage }}</p>
      <p class="muted">{{ notice }}</p>
      <h2>板块</h2>
      <p v-if="!sectors.length" class="muted">还没有可计算的行业成分。</p>
      <ul v-else>
        <li v-for="item in sectors" :key="item">{{ item }}</li>
      </ul>
    </section>
  </main>
</template>
