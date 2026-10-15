<script setup lang="ts">
import { ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { restoreSession, session } from "../stores/auth"

const asOf = ref("2026-08-16")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const rating = ref("")
const decision = ref("")
const nodes = ref<string[]>([])
const message = ref("")

async function run(): Promise<void> {
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录"
    return
  }
  phase.value = "loading"
  try {
    const response = await authorizedFetch(
      "/api/v1/simulation/runs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: "600519.SH", as_of: asOf.value, max_debate_rounds: 1 }),
      },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const payload = await readJson(response)
    const data = asRecord(asRecord(payload)?.data)
    if (!response.ok || !data) {
      message.value = "无法完成推演"
      phase.value = "error"
      return
    }
    rating.value = asString(data.rating) ?? ""
    decision.value = asString(data.decision) ?? ""
    nodes.value = Array.isArray(data.nodes) ? data.nodes.filter((item): item is string => typeof item === "string") : []
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
</script>

<template>
  <main class="page">
    <section class="panel">
      <h1>研究推演</h1>
      <p class="muted">Mock 回放已核对的报表披露日。不会下单，也不会把缺少的评级写成 Hold。</p>
      <form class="form" @submit.prevent="run">
        <label>
          截止日
          <input name="simulation-as-of" v-model="asOf" />
        </label>
        <button class="retry" type="submit" :disabled="phase === 'loading'">开始</button>
      </form>
      <p v-if="phase === 'loading'">正在推演</p>
      <p v-else-if="phase === 'error'" role="alert">{{ message }}</p>
      <template v-else-if="phase === 'ready'">
        <p role="status">评级 {{ rating }}</p>
        <p>{{ decision }}</p>
        <p class="muted">当前节点 {{ nodes[nodes.length - 1] }}</p>
        <ul>
          <li v-for="node in nodes" :key="node">{{ node }}</li>
        </ul>
      </template>
    </section>
  </main>
</template>
