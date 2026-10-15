<script setup lang="ts">
import { ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../../api/client"
import { summarizeSearch, type SearchPayload } from "../../api/instrumentSearch"

const query = ref("")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const lines = ref<string[]>([])
const failure = ref("")

async function search(): Promise<void> {
  const q = query.value.trim()
  if (!q) {
    phase.value = "error"
    failure.value = "请输入代码或名称"
    lines.value = []
    return
  }
  phase.value = "loading"
  failure.value = ""
  try {
    const response = await fetch(`${apiUrl("/api/v1/instruments/search")}?q=${encodeURIComponent(q)}`)
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const results = Array.isArray(data?.results) ? data.results.flatMap(parseHit) : []
    lines.value = summarizeSearch({
      unique: data?.unique === true,
      empty_message: asString(data?.empty_message),
      results,
    })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseHit(value: unknown): SearchPayload["results"] {
  const record = asRecord(value)
  const code = asString(record?.canonical_code)
  const name = asString(record?.name_cn)
  const kind = asString(record?.instrument_type)
  const display = asString(record?.display_symbol)
  if (!code || !name || !kind || !display) {
    return []
  }
  return [{ canonical_code: code, name_cn: name, instrument_type: kind, display_symbol: display }]
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
  <form class="form instrument-search" @submit.prevent="search">
    <label>
      搜索证券
      <input v-model="query" name="instrument-query" placeholder="代码或名称，如 600519.SH" />
    </label>
    <button class="retry" type="submit" :disabled="phase === 'loading'">搜索</button>
    <p v-if="phase === 'loading'">正在搜索</p>
    <p v-else-if="phase === 'error'" role="alert">{{ failure }}</p>
    <ul v-else-if="lines.length" class="provider-list">
      <li v-for="line in lines" :key="line">{{ line }}</li>
    </ul>
  </form>
</template>
