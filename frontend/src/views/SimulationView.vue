<script setup lang="ts">
import { computed, ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

const asOf = ref("2026-08-16")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const rating = ref("")
const decision = ref("")
const nodes = ref<string[]>([])
const bull = ref("")
const bear = ref("")
const runId = ref("")
const backtestNote = ref("")
const qualityFlag = ref("")
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
    bull.value = asString(data.bull) ?? ""
    bear.value = asString(data.bear) ?? ""
    nodes.value = Array.isArray(data.nodes) ? data.nodes.filter((item): item is string => typeof item === "string") : []
    runId.value = asString(data.run_id) ?? ""
    await loadBacktest(runId.value)
    phase.value = "ready"
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadBacktest(id: string): Promise<void> {
  backtestNote.value = ""
  qualityFlag.value = ""
  if (!id || !session.accessToken) {
    return
  }
  const response = await authorizedFetch(
    `/api/v1/simulation/runs/${id}/backtest`,
    { method: "POST" },
    session.accessToken,
    async () => {
      const restored = await restoreSession()
      return restored ? session.accessToken : null
    },
  )
  const payload = await readJson(response)
  const data = asRecord(asRecord(payload)?.data)
  if (!response.ok || !data) {
    backtestNote.value = "无法核算区间收益"
    return
  }
  qualityFlag.value = asString(data.quality_flag) ?? ""
  backtestNote.value = asString(data.note) ?? "没有写入收益率"
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

const qualityLabel = computed(() => {
  if (qualityFlag.value === "insufficient") {
    return "样本不足"
  }
  if (qualityFlag.value === "measured") {
    return "已用入库收盘价"
  }
  return ""
})
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
        <svg
          class="topology"
          :viewBox="`0 0 360 ${Math.max(nodes.length, 1) * 40 + 24}`"
          role="img"
          aria-label="推演节点"
        >
          <line
            v-for="(node, index) in nodes.slice(0, -1)"
            :key="`edge-${node}`"
            x1="40"
            :y1="28 + index * 36"
            x2="40"
            :y2="52 + index * 36"
            stroke="currentColor"
          />
          <g v-for="(node, index) in nodes" :key="node">
            <circle cx="40" :cy="24 + index * 36" r="8" />
            <text x="58" :y="28 + index * 36">{{ node }}</text>
          </g>
        </svg>
        <h2>多空</h2>
        <p>多头 {{ bull || "无" }}</p>
        <p>空头 {{ bear || "无" }}</p>
        <h2>区间回测</h2>
        <p>{{ qualityLabel }}</p>
        <p>{{ backtestNote || "还没有核算" }}</p>
      </template>
    </section>
  </main>
</template>

<style scoped>
.topology {
  width: min(100%, 520px);
  height: auto;
  color: var(--text);
}

.topology text {
  font-size: 13px;
  fill: currentColor;
}

.topology circle {
  fill: currentColor;
}
</style>
