<script setup lang="ts">
import { ref } from "vue"
import { summarizeBars, type DailyBar } from "../api/bars"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"

const options = [
  { code: "600519.SH", name: "贵州茅台" },
  { code: "601318.SH", name: "中国平安" },
  { code: "000858.SZ", name: "五粮液" },
]

const symbol = ref(options[0].code)
const start = ref("")
const end = ref("")
const asOf = ref("")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")
const lines = ref<string[]>([])
const heading = ref("")

async function query(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  lines.value = []
  heading.value = ""
  try {
    const found = await fetch(apiUrl(`/api/v1/instruments/search?q=${encodeURIComponent(symbol.value)}`))
    const foundBody = await readJson(found)
    if (!found.ok) {
      throw errorFrom(foundBody, found.headers.get("X-Request-ID"))
    }
    const results = asRecord(asRecord(foundBody)?.data)?.results
    const hits = Array.isArray(results) ? results.map(asRecord).filter(isRecord) : []
    if (hits.length !== 1) {
      phase.value = "error"
      failure.value = "请改用带交易所后缀的代码"
      return
    }
    const instrumentId = asString(hits[0].id)
    const name = asString(hits[0].name_cn) ?? symbol.value
    if (!instrumentId) {
      phase.value = "error"
      failure.value = "暂无该证券"
      return
    }
    const params = new URLSearchParams({ adjustment: "none", timeframe: "1d" })
    if (start.value) {
      params.set("start", start.value)
    }
    if (end.value) {
      params.set("end", end.value)
    }
    if (asOf.value) {
      params.set("as_of", asOf.value)
    }
    const response = await fetch(apiUrl(`/api/v1/market/bars/${instrumentId}?${params}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const bars = Array.isArray(data?.bars) ? data.bars.flatMap(parseBar) : []
    heading.value = `${symbol.value} ${name}`
    lines.value = summarizeBars({
      emptyMessage: asString(data?.empty_message),
      bars,
    })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseBar(value: unknown): DailyBar[] {
  const record = asRecord(value)
  const tradeDate = asString(record?.trade_date)
  const open = asString(record?.open)
  const high = asString(record?.high)
  const low = asString(record?.low)
  const close = asString(record?.close)
  const volume = asString(record?.volume)
  const currency = asString(record?.currency)
  const volumeUnit = asString(record?.volume_unit)
  if (!tradeDate || !open || !high || !low || !close || !volume || !currency || !volumeUnit) {
    return []
  }
  return [
    {
      trade_date: tradeDate,
      open,
      high,
      low,
      close,
      volume,
      currency,
      volume_unit: volumeUnit,
    },
  ]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

function isRecord(value: Record<string, unknown> | null): value is Record<string, unknown> {
  return value !== null
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value ? value : null
}
</script>

<template>
  <main class="page">
    <section class="panel">
      <p class="nav-links">
        <a href="/">返回首页</a>
      </p>
      <h1>A 股日线</h1>
      <p class="muted">只查询贵州茅台、中国平安和五粮液。没有同步到的日期不会显示价格。</p>
      <form class="form" @submit.prevent="query">
        <label>
          证券
          <select v-model="symbol" name="bar-symbol">
            <option v-for="item in options" :key="item.code" :value="item.code">
              {{ item.code }} {{ item.name }}
            </option>
          </select>
        </label>
        <label>
          开始日期
          <input v-model="start" name="bar-start" type="date" />
        </label>
        <label>
          结束日期
          <input v-model="end" name="bar-end" type="date" />
        </label>
        <label>
          观察日
          <input v-model="asOf" name="bar-as-of" type="date" />
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">查看日线</button>
        <p v-if="phase === 'loading'">正在查询</p>
        <p v-else-if="phase === 'error'" role="alert">{{ failure }}</p>
        <template v-else-if="phase === 'ready'">
          <p>{{ heading }}</p>
          <ul class="provider-list">
            <li v-for="line in lines" :key="line">{{ line }}</li>
          </ul>
        </template>
      </form>
    </section>
  </main>
</template>
