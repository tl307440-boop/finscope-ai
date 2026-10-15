<script setup lang="ts">
import { ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"

const phase = ref<"idle" | "ready" | "error">("idle")
const failure = ref("")
const lines = ref<string[]>([])

async function calculate(): Promise<void> {
  phase.value = "idle"
  failure.value = ""
  lines.value = []
  try {
    const response = await fetch(apiUrl("/api/v1/metrics/evaluations"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        statement: "income",
        q1: "80",
        q2: "200",
        market_cap: "100",
        profit: "-5",
      }),
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const quarters = Array.isArray(data?.quarters) ? data.quarters : []
    const q2 = quarters.map(asRecord).find((item) => item?.quarter === "Q2")
    lines.value = [
      asString(data?.notice) ?? "计算样例，不是公司真实财报。",
      `Q2 单季 ${asString(q2?.value) ?? "无法计算"}`,
      `市盈率 ${asString(data?.pe_display) ?? asString(data?.pe_value) ?? "无法计算"}`,
    ]
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
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
</script>

<template>
  <main class="page">
    <section class="panel">
      <h1>财务指标试算</h1>
      <p class="muted">
        一季报累计 80，中报累计 200。单季应是 120。利润为负时市盈率显示 N/A。这些数字是计算样例，不是公司真实财报。
      </p>
      <button class="retry" type="button" @click="calculate">计算样例</button>
      <p v-if="phase === 'error'" role="alert">{{ failure }}</p>
      <ul v-else-if="lines.length" class="provider-list">
        <li v-for="line in lines" :key="line">{{ line }}</li>
      </ul>
    </section>
  </main>
</template>
