<script setup lang="ts">
import { onMounted, ref } from "vue"
import { describeObservations, type MacroCatalog, type MacroObservation } from "../api/macro"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"

const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("无法读取宏观序列")
const catalog = ref<MacroCatalog | null>(null)
const seriesCode = ref("UNRATE")
const asOf = ref("")
const resultPhase = ref<"idle" | "loading" | "ready" | "error">("idle")
const lines = ref<string[]>([])
const resultError = ref("")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await fetch(apiUrl("/api/v1/macro/series"))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    catalog.value = parseCatalog(asRecord(asRecord(body)?.data))
    const first = catalog.value.series[0]
    if (first) {
      seriesCode.value = first.series_code
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function query(): Promise<void> {
  if (!asOf.value) {
    resultPhase.value = "error"
    resultError.value = "请选择日期"
    lines.value = []
    return
  }
  resultPhase.value = "loading"
  resultError.value = ""
  lines.value = []
  try {
    const params = new URLSearchParams({ as_of: asOf.value, limit: "100" })
    const response = await fetch(
      apiUrl(`/api/v1/macro/series/${encodeURIComponent(seriesCode.value)}/observations?${params}`),
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const observations = Array.isArray(data?.observations)
      ? data.observations.flatMap(parseObservation)
      : []
    lines.value = describeObservations({
      emptyMessage: asString(data?.empty_message),
      observations,
    })
    resultPhase.value = "ready"
  } catch (caught) {
    resultError.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    resultPhase.value = "error"
  }
}

function parseCatalog(data: Record<string, unknown> | null): MacroCatalog {
  const series = Array.isArray(data?.series) ? data.series.flatMap(parseSeries) : []
  const availability = data?.availability === "configured" ? "configured" : "unavailable"
  return {
    availability,
    notice: asString(data?.notice) ?? "这里只列出序列定义。没有同步到的观测值不会用演示数字填补。",
    series,
  }
}

function parseSeries(value: unknown): MacroCatalog["series"] {
  const record = asRecord(value)
  const seriesCodeValue = asString(record?.series_code)
  const nameCn = asString(record?.name_cn)
  const nameEn = asString(record?.name_en)
  const frequency = asString(record?.frequency)
  const baseUnit = asString(record?.base_unit)
  const seasonal = asString(record?.seasonal_adjustment)
  if (!seriesCodeValue || !nameCn || !nameEn || !frequency || !baseUnit || !seasonal) {
    return []
  }
  return [
    {
      series_code: seriesCodeValue,
      name_cn: nameCn,
      name_en: nameEn,
      frequency,
      base_unit: baseUnit,
      seasonal_adjustment: seasonal,
    },
  ]
}

function parseObservation(value: unknown): MacroObservation[] {
  const record = asRecord(value)
  const observationDate = asString(record?.observation_date)
  const amount = asString(record?.value)
  const unit = asString(record?.unit)
  const realtimeStart = asString(record?.realtime_start)
  const revision = record?.revision_no
  if (!observationDate || !amount || !unit || !realtimeStart || typeof revision !== "number") {
    return []
  }
  return [
    {
      observation_date: observationDate,
      value: amount,
      unit,
      realtime_start: realtimeStart,
      revision_no: revision,
    },
  ]
}

onMounted(() => {
  void load()
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取宏观序列</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取宏观序列</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>
    <section v-else-if="catalog" class="panel">
      <p class="nav-links">
        <a href="/">返回首页</a>
      </p>
      <h1>美国宏观</h1>
      <p v-if="catalog.availability === 'unavailable'" role="status">数据源不可用</p>
      <p class="muted">{{ catalog.notice }}</p>
      <ul class="provider-list">
        <li v-for="item in catalog.series" :key="item.series_code">
          {{ item.series_code }} · {{ item.name_cn }} · {{ item.frequency }} · {{ item.base_unit }} ·
          {{ item.seasonal_adjustment }}
        </li>
      </ul>
      <form class="form" @submit.prevent="query">
        <label>
          序列
          <select v-model="seriesCode" name="series-code">
            <option v-for="item in catalog.series" :key="item.series_code" :value="item.series_code">
              {{ item.series_code }} {{ item.name_cn }}
            </option>
          </select>
        </label>
        <label>
          观察日
          <input v-model="asOf" name="as-of" type="date" />
        </label>
        <button class="retry" type="submit" :disabled="resultPhase === 'loading'">查看当时可见的数据</button>
        <p v-if="resultPhase === 'loading'">正在查询</p>
        <p v-else-if="resultPhase === 'error'" role="alert">{{ resultError }}</p>
        <ul v-else-if="lines.length" class="provider-list">
          <li v-for="line in lines" :key="line">{{ line }}</li>
        </ul>
      </form>
    </section>
  </main>
</template>
