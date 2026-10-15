<script setup lang="ts">
/**
 * AI 财报报告（/company/:symbol/analysis/:runId）。
 *
 *  - GET /api/v1/analysis/runs/{run_id}   报告正文（需 Bearer）
 *  - GET /api/v1/analysis/model-status    模型是否已配置
 *  - GET /api/v1/instruments/search       路由 symbol → 证券元数据
 *
 * 后端返回的报告正文是纯文本字段 data.body（不是 markdown 渲染库能识别的富文本），
 * 这里按需求用等宽 pre + 保留换行展示，不引入任何渲染库。
 * 模型未配置时后端会给出 available=false 与真实 message，本页必须显式提示
 * 「未配置大模型，报告为规则生成」，不隐藏该状态。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, authorizedFetch, readJson } from "../api/client"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { asBool, asString, dataOf, formatMoment } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"

const props = defineProps<{ symbol: string; runId: string }>()
const session = useSessionStore()

interface RunReport {
  runId: string
  reportId: string
  status: string
  title: string
  message: string
  body: string
  notice: string
}

type Phase = "loading" | "ready" | "error"

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
const report = ref<RunReport | null>(null)
const instrument = ref<InstrumentHit | null>(null)
const modelAvailable = ref<boolean | null>(null)
const modelMessage = ref("")
const generatedAt = ref("")
const copyState = ref("")

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const runId = computed<string>(() => (Array.isArray(props.runId) ? props.runId[0] ?? "" : props.runId))
const bodyText = computed<string>(() => report.value?.body ?? "")
const statusTag = computed<string>(() => {
  const status = report.value?.status ?? ""
  if (status === "succeeded") {
    return "success"
  }
  if (status === "failed") {
    return "danger"
  }
  return "info"
})

/** 规则生成的明确提示：模型不可用时不得含糊。 */
const ruleBasedNotice = computed<string>(() => {
  if (modelAvailable.value === false) {
    return `未配置大模型，报告为规则生成。${modelMessage.value}`
  }
  if (modelAvailable.value === null) {
    return "未能确认模型状态，请以报告内的来源标注为准。"
  }
  return modelMessage.value
})

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function loadModelStatus(): Promise<void> {
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/model-status",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const body = await readJson(response)
    if (!response.ok) {
      modelAvailable.value = null
      return
    }
    const data = dataOf(body)
    modelAvailable.value = asBool(data?.available)
    modelMessage.value = asString(data?.message) ?? ""
  } catch {
    modelAvailable.value = null
    modelMessage.value = ""
  }
}

async function loadInstrument(): Promise<void> {
  const code = symbol.value.trim()
  if (!code) {
    return
  }
  try {
    const search = await searchInstruments(code, undefined, 10)
    instrument.value =
      search.results.find((hit) => hit.displaySymbol === code) ??
      search.results.find((hit) => hit.canonicalCode === code) ??
      search.results[0] ??
      null
  } catch {
    // 证券元数据只是抬头，取不到不影响报告正文。
    instrument.value = null
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  copyState.value = ""
  report.value = null
  generatedAt.value = ""

  if (!session.accessToken) {
    const restored = await restoreSession()
    if (!restored) {
      phase.value = "error"
      retryable.value = true
      failure.value = "请先登录后再查看分析报告"
      return
    }
  }

  const id = runId.value.trim()
  if (!id) {
    phase.value = "error"
    retryable.value = false
    failure.value = "路由里没有运行标识 runId"
    return
  }

  try {
    const response = await authorizedFetch(
      `/api/v1/analysis/runs/${encodeURIComponent(id)}`,
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    const payload = await readJson(response)
    if (!response.ok) {
      const error = asRecord(asRecord(payload)?.error)
      failure.value = asString(error?.message) ?? "找不到这份研究记录"
      retryable.value = response.status !== 404
      phase.value = "error"
      return
    }
    const data = dataOf(payload)
    if (!data) {
      phase.value = "error"
      retryable.value = false
      failure.value = "接口返回里没有报告数据"
      return
    }
    report.value = {
      runId: asString(data.run_id) ?? id,
      reportId: asString(data.report_id) ?? "",
      status: asString(data.status) ?? "",
      title: asString(data.title) ?? "研究记录",
      message: asString(data.message) ?? "",
      body: typeof data.body === "string" ? data.body : "",
      notice: asString(data.notice) ?? "",
    }
    generatedAt.value = asString(asRecord(asRecord(payload)?.meta)?.as_of) ?? ""
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function copyBody(): Promise<void> {
  const text = bodyText.value
  if (!text) {
    copyState.value = "没有可复制的正文"
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    copyState.value = "已复制正文"
  } catch {
    copyState.value = "浏览器拒绝访问剪贴板，请手动选中正文复制"
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

onMounted(() => {
  void load()
  void loadInstrument()
  void loadModelStatus()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取分析报告</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取分析报告</h1>
      <p>{{ failure }}</p>
      <button v-if="retryable" class="retry" type="button" @click="load">重试</button>
      <p class="muted">证券代码 {{ symbol }} · 运行 {{ runId }}</p>
    </section>

    <template v-else-if="report">
      <header class="panel">
        <h1>{{ report.title }}</h1>
        <p class="muted">
          <span>{{ instrument?.nameCn || instrument?.nameEn || symbol }}</span>
          · {{ symbol }}
          <template v-if="report.status">
            · 状态
            <el-tag :type="statusTag" size="small">{{ report.status }}</el-tag>
          </template>
        </p>
        <p class="muted">
          运行 {{ report.runId }}
          <template v-if="report.reportId"> · 记录 {{ report.reportId }}</template>
          <template v-if="generatedAt"> · 服务端截止 {{ formatMoment(generatedAt) }}</template>
        </p>
      </header>

      <section class="panel">
        <el-alert
          :type="modelAvailable === false ? 'warning' : 'info'"
          :closable="false"
          show-icon
        >
          <template #title>{{ ruleBasedNotice }}</template>
        </el-alert>
        <p v-if="report.message" class="muted">{{ report.message }}</p>
        <p v-if="report.notice" class="muted">{{ report.notice }}</p>
        <div class="action-row">
          <button class="retry" type="button" :disabled="!bodyText" @click="copyBody">
            复制正文
          </button>
          <button class="retry" type="button" @click="load">重新读取</button>
        </div>
        <p v-if="copyState" role="status" class="muted">{{ copyState }}</p>
      </section>

      <section class="panel">
        <h2>报告正文</h2>
        <pre v-if="bodyText" class="report-body">{{ bodyText }}</pre>
        <el-empty v-else description="这份记录没有报告正文" />
      </section>
    </template>
  </main>
</template>

<style scoped>
.panel {
  margin-bottom: 16px;
}

.report-body {
  margin: 12px 0 0;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--bg);
  color: var(--text);
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
  max-height: 70vh;
}

.el-alert {
  margin-bottom: 12px;
}
</style>
