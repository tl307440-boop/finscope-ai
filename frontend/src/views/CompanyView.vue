<script setup lang="ts">
/**
 * 公司研究（规格书 §1.3 `/company/:symbol`）。
 *
 * CompanyHeader（代码、名称、来源提示）+ 报告期对比 + RatioTable/TrendChart。
 * 每个数字都带申报号与来源链接；没有正式文件时不编造数字。
 */
import { computed, onMounted, ref, watch } from "vue"
import type { EChartsOption } from "echarts"
import VChart from "vue-echarts"
import { useRouter } from "vue-router"
import { authorizedFetch, ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asRecord, asString } from "../api/json"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { modelNotice } from "../api/modelStatus"
import { describeResearch, type ResearchMetric } from "../api/research"
import { restoreSession, useSessionStore } from "../stores/auth"

const props = defineProps<{ symbol?: string }>()
const session = useSessionStore()
const router = useRouter()

const symbol = ref(props.symbol ?? "AAPL")
const asOf = ref("2024-06-01")
const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("无法读取公司")
const periods = ref<string[]>([])
const periodA = ref("")
const periodB = ref("")
const lines = ref<string[]>([])
const metrics = ref<ResearchMetric[]>([])
const instrument = ref<InstrumentHit | null>(null)
const modelLine = ref("正在确认模型状态")
const saveMessage = ref("")
const saving = ref(false)

const heading = computed(() => {
  const item = instrument.value
  if (!item) {
    return symbol.value
  }
  const name = item.nameCn || item.nameEn || item.canonicalCode
  return `${item.canonicalCode} ${name}`
})

/** 只把两侧都能解析成数字的指标画进对比图，其余留在文字清单里。 */
const chartOption = computed<EChartsOption | null>(() => {
  const rows = metrics.value.flatMap((metric) => {
    const before = metric.earlier ? Number(metric.earlier.value) : Number.NaN
    const after = metric.later ? Number(metric.later.value) : Number.NaN
    if (!Number.isFinite(before) || !Number.isFinite(after)) {
      return []
    }
    return [{ concept: metric.concept, before, after }]
  })
  if (!rows.length) {
    return null
  }
  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { data: [periodA.value, periodB.value], textStyle: { color: "#95a6be" } },
    grid: { left: 72, right: 24, top: 40, bottom: 32 },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.concept),
      axisLabel: { color: "#95a6be", rotate: 24 },
      axisLine: { lineStyle: { color: "#95a6be" } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: "#95a6be" },
      splitLine: { lineStyle: { color: "rgba(231,237,248,0.12)" } },
    },
    series: [
      { name: periodA.value, type: "bar", data: rows.map((row) => row.before), itemStyle: { color: "#3b82f6" } },
      { name: periodB.value, type: "bar", data: rows.map((row) => row.after), itemStyle: { color: "#a855f7" } },
    ],
  }
})

async function loadCompany(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  const wanted = symbol.value.trim()
  if (!wanted) {
    phase.value = "error"
    failure.value = "请输入证券代码"
    return
  }
  try {
    const found = await searchInstruments(wanted, undefined, 10)
    const hit =
      found.results.find((row) => row.canonicalCode.toUpperCase() === wanted.toUpperCase()) ??
      found.results[0] ??
      null
    if (!hit) {
      phase.value = "error"
      failure.value = found.emptyMessage ?? "暂无该证券"
      return
    }
    instrument.value = hit
    await loadResearch()
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadResearch(): Promise<void> {
  const item = instrument.value
  if (!item) {
    return
  }
  phase.value = "loading"
  const params = new URLSearchParams({ as_of: asOf.value })
  if (periodA.value && periodB.value && periodA.value !== periodB.value) {
    params.set("period_a", periodA.value)
    params.set("period_b", periodB.value)
  }
  try {
    const response = await fetch(apiUrl(`/api/v1/companies/${item.id}/research?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const available = Array.isArray(data?.available_periods)
      ? data.available_periods.filter((entry): entry is string => typeof entry === "string")
      : []
    periods.value = available
    const hadSelection = Boolean(periodA.value && periodB.value && periodA.value !== periodB.value)
    if (!periodA.value && available[0]) {
      periodA.value = available[0]
    }
    if (!periodB.value && available.length > 1) {
      periodB.value = available[available.length - 1]
    }
    if (!hadSelection && periodA.value && periodB.value && periodA.value !== periodB.value) {
      await loadResearch()
      return
    }
    const parsed = Array.isArray(data?.metrics) ? data.metrics.flatMap(parseMetric) : []
    const unevaluated = Array.isArray(data?.unevaluated)
      ? data.unevaluated.flatMap((entry) => {
          const record = asRecord(entry)
          const ruleId = asString(record?.rule_id)
          return ruleId ? [ruleId] : []
        })
      : []
    metrics.value = parsed
    lines.value = describeResearch({ notice: asString(data?.notice), metrics: parsed, unevaluated })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseMetric(value: unknown): ResearchMetric[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  if (!concept) {
    return []
  }
  return [
    {
      concept,
      earlier: parseSide(record?.earlier),
      later: parseSide(record?.later),
      change: asString(record?.change),
    },
  ]
}

function parseSide(value: unknown): ResearchMetric["earlier"] {
  const record = asRecord(value)
  const amount = asString(record?.value)
  const accession = asString(record?.accession_no)
  const source = asString(record?.source_url)
  const filed = asString(record?.filed)
  if (!amount || !accession || !source || !filed) {
    return null
  }
  return { value: amount, accessionNo: accession, sourceUrl: source, filed }
}

async function saveReport(): Promise<void> {
  saveMessage.value = ""
  const item = instrument.value
  if (!session.accessToken) {
    saveMessage.value = "请先登录后再保存"
    return
  }
  if (!item || !periodA.value || !periodB.value || periodA.value === periodB.value) {
    saveMessage.value = "请选择两个不同的报告期"
    return
  }
  saving.value = true
  try {
    const response = await authorizedFetch(
      "/api/v1/analysis/filing",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instrument_id: item.id,
          as_of: asOf.value,
          period_a: periodA.value,
          period_b: periodB.value,
        }),
      },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const payload = await readJson(response)
    const data = asRecord(asRecord(payload)?.data)
    const runId = asString(data?.run_id)
    if (!response.ok || !runId) {
      const error = asRecord(asRecord(payload)?.error)
      saveMessage.value = asString(error?.message) ?? "无法保存研究记录"
      return
    }
    await router.push(`/reports/${runId}`)
  } catch {
    saveMessage.value = "无法连接接口"
  } finally {
    saving.value = false
  }
}

async function loadModel(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/analysis/model-status"))
    const body = await readJson(response)
    const data = asRecord(asRecord(body)?.data)
    modelLine.value = modelNotice(asString(data?.message), data?.available === true)
  } catch {
    modelLine.value = modelNotice(null, false)
  }
}

function openFilings(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}/filings`)
}

onMounted(() => {
  void loadCompany()
  void loadModel()
})

watch(
  () => props.symbol,
  (next) => {
    if (next && next !== symbol.value) {
      symbol.value = next
      void loadCompany()
    }
  },
)
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取公司研究</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取公司研究</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="loadCompany">重试</button>
    </section>
    <section v-else class="panel">
      <header class="company-header">
        <h1>{{ heading }}</h1>
        <p class="muted">
          每个数字都带申报号与来源链接。没有正式文件时不编造数字；SEC 样例为本地夹具，不是公司真实收入。
        </p>
        <p class="muted">{{ modelLine }}</p>
      </header>

      <form class="form" @submit.prevent="loadCompany">
        <label>
          证券代码
          <input v-model="symbol" name="company-symbol" placeholder="AAPL 或 600519.SH" />
        </label>
        <label>
          观察日
          <input v-model="asOf" name="company-as-of" type="date" />
        </label>
        <button class="retry" type="submit">读取公司</button>
        <button class="retry" type="button" @click="openFilings">财报资料</button>
      </form>

      <form class="form" @submit.prevent="loadResearch">
        <label>
          报告期甲
          <select v-model="periodA" name="period-a">
            <option v-for="period in periods" :key="`a-${period}`" :value="period">{{ period }}</option>
          </select>
        </label>
        <label>
          报告期乙
          <select v-model="periodB" name="period-b">
            <option v-for="period in periods" :key="`b-${period}`" :value="period">{{ period }}</option>
          </select>
        </label>
        <button class="retry" type="submit">对比</button>
        <button class="retry" type="button" :disabled="saving" @click="saveReport">保存研究记录</button>
        <p v-if="!session.accessToken" class="muted">
          <router-link :to="`/login?redirect=${encodeURIComponent(`/company/${symbol}`)}`">
            请先登录后再保存
          </router-link>
        </p>
        <p v-else-if="saveMessage" role="status">{{ saveMessage }}</p>
      </form>

      <VChart
        v-if="chartOption"
        class="ratio-chart"
        :option="chartOption"
        autoresize
        aria-label="报告期指标对比"
      />

      <ul v-if="lines.length" class="provider-list">
        <li v-for="line in lines" :key="line">{{ line }}</li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
.ratio-chart {
  width: 100%;
  height: 320px;
  margin-top: 12px;
}
</style>
