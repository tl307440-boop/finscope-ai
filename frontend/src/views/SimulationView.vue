<script setup lang="ts">
/**
 * 研究推演（`/simulation/mvp`）。
 *
 * 只有两个真实调用，顺序执行：
 * 1. `POST /api/v1/simulation/runs` —— 跑一次推演，返回 rating / decision / bull / bear / nodes / run_id；
 * 2. `POST /api/v1/simulation/runs/{run_id}/backtest` —— 按入库收盘价核算区间，
 *    样本不足时 quality_flag=insufficient 且 note 写明没有写入收益率。
 * 评级为空就写「无评级」，不把缺失的评级写成 Hold；没有下单、没有建议。
 */
import { computed, ref } from "vue"
import { authorizedFetch, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

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

const qualityLabel = computed(() => {
  if (qualityFlag.value === "insufficient") {
    return "样本不足"
  }
  if (qualityFlag.value === "measured") {
    return "已用入库收盘价"
  }
  return ""
})

const qualityTone = computed<"ok" | "warn" | "muted">(() => {
  if (qualityFlag.value === "measured") {
    return "ok"
  }
  if (qualityFlag.value === "insufficient") {
    return "warn"
  }
  return "muted"
})

async function run(): Promise<void> {
  if (!session.accessToken) {
    phase.value = "error"
    message.value = "请先登录"
    return
  }
  phase.value = "loading"
  message.value = ""
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
  } catch (caught) {
    message.value = caught instanceof Error && caught.message ? caught.message : "无法连接接口"
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
</script>

<template>
  <main class="page">
    <PageHeader
      title="研究推演"
      subtitle="Mock 回放已核对的报表披露日 · 不下单、不给建议、不把缺失评级写成 Hold"
    >
      <template #actions>
        <button class="retry" type="button" :disabled="phase === 'loading'" @click="run">重新推演</button>
      </template>
    </PageHeader>

    <div class="grid-1-2">
      <PanelCard title="推演输入" info="POST /api/v1/simulation/runs">
        <form class="form" @submit.prevent="run">
          <label class="field">
            标的
            <input value="600519.SH" name="simulation-symbol" readonly />
          </label>
          <label class="field">
            截止日
            <input v-model="asOf" name="simulation-as-of" placeholder="YYYY-MM-DD" />
          </label>
          <button class="retry" type="submit" :disabled="phase === 'loading'">
            {{ phase === "loading" ? "推演中…" : "开始" }}
          </button>
        </form>
        <p class="muted">
          {{ session.accessToken ? "已登录：" + (session.username ?? "当前账号") : "未登录时不会发起请求，先登录再推演。" }}
        </p>
      </PanelCard>

      <PanelCard title="推演口径" info="后端 Mock 的边界">
        <ul class="line-list">
          <li>只回放已核对的报表披露日，不用未来信息补推演。</li>
          <li>评级缺失就写「无评级」，不默认成 Hold。</li>
          <li>区间收益只在有入库收盘价时核算，样本不足则明确写「没有写入收益率」。</li>
          <li>本页不下单、不输出建议；结果是研究记录，不是交易指令。</li>
        </ul>
      </PanelCard>
    </div>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      :message="message || '无法完成推演'"
      @retry="run"
    />

    <StateBlock v-else-if="phase === 'idle'" state="empty" message="填好截止日后点「开始」，推演需要已登录。" />

    <template v-else>
      <div class="grid-1-2">
        <PanelCard title="评级与结论" :info="runId ? `运行号 ${runId}` : '后端未返回运行号'">
          <div class="rating-row">
            <p class="rating tabular">{{ rating || "无评级" }}</p>
            <StatusPill
              :tone="rating ? 'accent' : 'warn'"
              :text="rating ? '已给出评级' : '无评级'"
            />
          </div>
          <p class="decision">{{ decision || "后端未给出决策说明。" }}</p>
        </PanelCard>

        <PanelCard title="推演节点" info="按后端返回顺序展示，不做连线推断">
          <p v-if="!nodes.length" class="muted">后端没有返回推演节点。</p>
          <ol v-else class="topology">
            <li v-for="(node, index) in nodes" :key="`${index}-${node}`">
              <span class="step tabular">{{ index + 1 }}</span>
              <span>{{ node }}</span>
            </li>
          </ol>
        </PanelCard>
      </div>

      <div class="grid-2">
        <PanelCard title="多头论据" info="后端 bull 原文">
          <p class="debate">{{ bull || "无" }}</p>
        </PanelCard>

        <PanelCard title="空头论据" info="后端 bear 原文">
          <p class="debate">{{ bear || "无" }}</p>
        </PanelCard>
      </div>

      <PanelCard title="区间回测" info="POST /api/v1/simulation/runs/{run_id}/backtest">
        <div class="backtest-head">
          <StatusPill :tone="qualityTone" :text="qualityLabel || '没有核算结果'" />
          <span class="muted">{{ qualityFlag || "后端未给出质量标记" }}</span>
        </div>
        <p class="debate">{{ backtestNote || "还没有核算" }}</p>
      </PanelCard>

      <SourceFooter
        :as-of="asOf || '—'"
        :sources="['POST /api/v1/simulation/runs', '已入库日线收盘价']"
        :note="'推演为 Mock 回放，不构成投资建议；样本不足时明确不写入收益率'"
      />
    </template>
  </main>
</template>

<style scoped>
.form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 12px;
}

.field {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}

.field input {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
}

.form .retry {
  grid-column: 1 / -1;
  justify-self: start;
}

.rating-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.rating {
  margin: 0;
  font-size: 32px;
  font-weight: 700;
}

.decision,
.debate {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}

.topology {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
  counter-reset: step;
}

.topology li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}

.step {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
}

.backtest-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.line-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 8px;
  font-size: 13px;
}
</style>
