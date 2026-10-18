<script setup lang="ts">
/**
 * AI 宏观解读。点击后才请求模型（避免页面一打开就产生模型费用）。
 *
 * 后端只把观察日已公布的白名单序列交给模型，并逐个核对回答里的数字：
 *  - ok：通过核对，显示模型文字并标注「模型生成」；
 *  - rejected / error / model_unavailable / no_data：不显示任何模型文字，只列出已入库数据。
 */
import { computed, ref, watch } from "vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../../../api/client"
import { asRecord, asString, listOf } from "../../../api/json"
import { restoreSession, useSessionStore } from "../../../stores/auth"
import PanelCard from "../../../components/ui/PanelCard.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const props = defineProps<{
  region: "us" | "cn"
  /** 观察日 YYYY-MM-DD，与页面其余面板一致 */
  asOf: string
}>()

type Phase = "idle" | "loading" | "done" | "error" | "login"

interface FactLine {
  code: string
  label: string
  text: string
}

interface Result {
  status: string
  message: string
  text: string
  model: string
  notice: string
  facts: FactLine[]
}

const session = useSessionStore()
const phase = ref<Phase>("idle")
const failure = ref("")
const result = ref<Result | null>(null)

const regionName = computed<string>(() => (props.region === "cn" ? "中国" : "美国"))

watch(
  () => [props.region, props.asOf],
  () => {
    // 观察日变了，旧解读对应的是别的数据，不能留着。
    phase.value = "idle"
    result.value = null
  },
)

function parseFact(value: Record<string, unknown>): FactLine | null {
  const code = asString(value.code)
  const label = asString(value.label)
  const period = asString(value.period)
  const current = asString(value.value)
  if (!code || !label || !period || !current) {
    return null
  }
  const unit = asString(value.unit) ?? ""
  const previous = asString(value.previous_value)
  const change = asString(value.change)
  const tail = previous ? `；上一期 ${asString(value.previous_period) ?? ""} 为 ${previous}${unit}，较上期变化 ${change ?? "—"}` : ""
  return { code, label, text: `${period} 为 ${current}${unit}${tail}` }
}

function parseResult(body: unknown): Result | null {
  const data = asRecord(asRecord(body)?.data)
  const status = asString(data?.status)
  if (!data || !status) {
    return null
  }
  return {
    status,
    message: asString(data.message) ?? "",
    text: asString(data.text) ?? "",
    model: asString(data.model) ?? "",
    notice: asString(data.notice) ?? "",
    facts: listOf(data.facts).flatMap((fact) => {
      const parsed = parseFact(fact)
      return parsed ? [parsed] : []
    }),
  }
}

async function refresh(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function generate(): Promise<void> {
  if (!session.accessToken && !(await restoreSession())) {
    phase.value = "login"
    return
  }
  phase.value = "loading"
  failure.value = ""
  result.value = null
  try {
    const response = await authorizedFetch(
      "/api/v1/macro/interpretation",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ region: props.region, as_of: props.asOf }),
      },
      session.accessToken,
      refresh,
    )
    if (response.status === 401) {
      phase.value = "login"
      return
    }
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const parsed = parseResult(body)
    if (!parsed) {
      throw new ApiError("接口返回了无法识别的内容", "INVALID_RESPONSE", response.headers.get("X-Request-ID"))
    }
    result.value = parsed
    phase.value = "done"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务，请稍后重试"
    phase.value = "error"
  }
}
</script>

<template>
  <PanelCard
    title="AI 宏观解读"
    info="模型只能转述观察日已公布的数据；回答里的每个数字都会与入库数据逐个核对，核对不过的文字不会显示"
    tone="violet"
  >
    <template #icon><span class="panel-icon">✦</span></template>
    <template #actions>
      <button type="button" class="generate" :disabled="phase === 'loading'" @click="generate">
        {{ phase === "done" ? "重新生成" : "生成解读" }}
      </button>
    </template>

    <p v-if="phase === 'idle'" class="hint">
      点击「生成解读」，由已配置的模型转述{{ regionName }}最新已公布数据。需要登录；不会预测，也不给买卖建议。
    </p>
    <StateBlock v-else-if="phase === 'loading'" state="loading" message="正在生成解读" />
    <p v-else-if="phase === 'login'" class="hint">
      登录后才能使用 AI 解读。<router-link to="/login">去登录</router-link>
    </p>
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="generate" />

    <template v-else-if="result">
      <template v-if="result.status === 'ok'">
        <p class="answer">{{ result.text }}</p>
        <p class="badge">模型生成 · {{ result.model }} · {{ result.message }}</p>
      </template>
      <StateBlock v-else state="empty" :message="result.message" />

      <details v-if="result.facts.length" class="facts" :open="result.status !== 'ok'">
        <summary>模型可使用的数据（{{ result.facts.length }} 项）</summary>
        <ul>
          <li v-for="fact in result.facts" :key="fact.code">
            <strong>{{ fact.label }}</strong>：{{ fact.text }}
          </li>
        </ul>
      </details>
      <p class="note">{{ result.notice }}</p>
    </template>
  </PanelCard>
</template>

<style scoped>
.panel-icon {
  color: var(--accent);
  font-size: 16px;
}

.generate {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: color-mix(in srgb, var(--accent) 16%, transparent);
  color: var(--text);
  font-size: 12px;
  line-height: 22px;
  padding: 0 10px;
  cursor: pointer;
}

.generate:disabled {
  opacity: 0.5;
  cursor: wait;
}

.hint,
.note {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

.answer {
  margin: 0 0 6px;
  font-size: 14px;
  line-height: 1.8;
}

.badge {
  margin: 0;
  color: var(--violet);
  font-size: 12px;
}

.facts {
  margin: 10px 0;
  font-size: 13px;
}

.facts summary {
  cursor: pointer;
  color: var(--accent);
}

.facts ul {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--muted);
  line-height: 1.7;
  font-variant-numeric: tabular-nums;
}

.note {
  margin-top: 6px;
  font-size: 12px;
}
</style>
