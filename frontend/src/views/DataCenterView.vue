<script setup lang="ts">
import { onMounted, ref } from "vue"
import { ApiError, authorizedFetch, errorFrom, readJson } from "../api/client"
import { enqueueMessage, healthLabel, jobStatusLabel } from "../api/jobs"
import { providerStatusLabel } from "../api/macro"
import { describeProbeFailure } from "../api/providerProbe"
import { restoreSession, session } from "../stores/auth"

interface ProviderRow {
  code: string
  credentialRequired: boolean
  credentialPresent: boolean
  availability: string
  lastHealthStatus: string | null
}

interface SyncJobRow {
  id: string
  status: string
  label: string
  summary: string
}

const phase = ref<"loading" | "ready" | "denied" | "error">("loading")
const rows = ref<ProviderRow[]>([])
const jobs = ref<SyncJobRow[]>([])
const failure = ref("无法读取数据源状态")
const scenario = ref("timeout")
const probePhase = ref<"idle" | "loading" | "done" | "error">("idle")
const probeText = ref("")
const syncCode = ref("UNRATE")
const syncStart = ref("")
const syncEnd = ref("")
const syncPhase = ref<"idle" | "loading" | "done" | "error">("idle")
const syncText = ref("")
const barSymbol = ref("600519.SH")
const barStart = ref("")
const barEnd = ref("")
const barPhase = ref<"idle" | "loading" | "done" | "error">("idle")
const barText = ref("")

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/providers",
      { method: "GET" },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    if (response.status === 403) {
      phase.value = "denied"
      return
    }
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const providers = Array.isArray(data?.providers) ? data.providers : []
    rows.value = providers.flatMap((item) => {
      const record = asRecord(item)
      const code = asString(record?.code)
      if (!code) {
        return []
      }
      return [
        {
          code,
          credentialRequired: record?.credential_required === true,
          credentialPresent: record?.credential_present === true,
          availability: asString(record?.availability) ?? "unavailable",
          lastHealthStatus: asString(record?.last_health_status),
        },
      ]
    })
    phase.value = "ready"
    await loadJobs()
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function label(row: ProviderRow): string {
  return providerStatusLabel(row)
}

async function syncFred(): Promise<void> {
  syncPhase.value = "loading"
  syncText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/fred-syncs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          series_code: syncCode.value.trim(),
          observation_start: syncStart.value,
          observation_end: syncEnd.value,
        }),
      },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    const written = data?.rows_written
    syncText.value = typeof written === "number" ? `已写入 ${written} 条修订` : "同步完成"
    syncPhase.value = "done"
  } catch (caught) {
    syncText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    syncPhase.value = "error"
  }
}

async function loadJobs(): Promise<void> {
  const response = await authorizedFetch(
    "/api/v1/admin/sync-jobs",
    { method: "GET" },
    session.accessToken,
    async () => {
      const restored = await restoreSession()
      return restored ? session.accessToken : null
    },
  )
  const body = await readJson(response)
  if (!response.ok) {
    return
  }
  const data = asRecord(asRecord(body)?.data)
  const listed = Array.isArray(data?.jobs) ? data.jobs : []
  jobs.value = listed.flatMap((item) => {
    const record = asRecord(item)
    const id = asString(record?.id)
    const status = asString(record?.status)
    if (!id || !status) {
      return []
    }
    const summary = asString(record?.error_summary) ?? jobStatusLabel(status)
    return [{ id, status, label: jobStatusLabel(status), summary }]
  })
}

async function syncBars(): Promise<void> {
  barPhase.value = "loading"
  barText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/sync-jobs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_type: "akshare_daily",
          scope: {
            display_symbol: barSymbol.value,
            observation_start: barStart.value,
            observation_end: barEnd.value,
            adjustment: "none",
          },
        }),
      },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = asRecord(asRecord(body)?.data)
    barText.value = enqueueMessage(data?.created === true)
    barPhase.value = "done"
    await loadJobs()
  } catch (caught) {
    barText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    barPhase.value = "error"
  }
}

async function runProbe(): Promise<void> {
  probePhase.value = "loading"
  probeText.value = ""
  try {
    const response = await authorizedFetch(
      "/api/v1/admin/provider-probes",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario: scenario.value }),
      },
      session.accessToken,
      async () => {
        const restored = await restoreSession()
        return restored ? session.accessToken : null
      },
    )
    const body = await readJson(response)
    probeText.value = describeProbeFailure(body)
    probePhase.value = response.ok ? "done" : "error"
  } catch (caught) {
    probeText.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    probePhase.value = "error"
  }
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取数据源状态</section>
    <section v-else-if="phase === 'denied'" class="panel" role="alert">
      <h1>没有访问权限</h1>
      <p>研究者不能查看管理接口。</p>
    </section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取数据源状态</h1>
      <p>{{ failure }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>
    <section v-else class="panel">
      <p class="nav-links">
        <router-link to="/settings">返回账号设置</router-link>
      </p>
      <h1>数据源配置</h1>
      <p class="muted">这里只显示密钥是否已经配置，不展示密钥，也不表示行情已经同步。</p>
      <ul v-if="rows.length" class="provider-list">
        <li v-for="row in rows" :key="row.code">
          {{ row.code }} · {{ label(row) }} · {{ healthLabel(row.lastHealthStatus) }}
        </li>
      </ul>
      <p v-else>尚未接入</p>
      <form class="form probe-form" @submit.prevent="runProbe">
        <label>
          模拟上游故障
          <select v-model="scenario" name="scenario">
            <option value="status_429">限流</option>
            <option value="status_503">服务不可用</option>
            <option value="timeout">超时</option>
          </select>
        </label>
        <button class="retry" type="submit" :disabled="probePhase === 'loading'">探测</button>
        <p v-if="probePhase === 'loading'">正在探测</p>
        <p v-else-if="probeText" :role="probePhase === 'error' ? 'alert' : 'status'">{{ probeText }}</p>
      </form>
      <form class="form probe-form" @submit.prevent="syncFred">
        <label>
          同步 FRED 序列
          <input v-model="syncCode" name="fred-series" />
        </label>
        <label>
          起始观察日
          <input v-model="syncStart" name="fred-start" type="date" required />
        </label>
        <label>
          结束观察日
          <input v-model="syncEnd" name="fred-end" type="date" required />
        </label>
        <button class="retry" type="submit" :disabled="syncPhase === 'loading'">同步</button>
        <p v-if="syncPhase === 'loading'">正在同步</p>
        <p v-else-if="syncText" :role="syncPhase === 'error' ? 'alert' : 'status'">{{ syncText }}</p>
      </form>
      <form class="form probe-form" @submit.prevent="syncBars">
        <label>
          同步 A 股日线
          <select v-model="barSymbol" name="bar-symbol">
            <option value="600519.SH">600519.SH 贵州茅台</option>
            <option value="601318.SH">601318.SH 中国平安</option>
            <option value="000858.SZ">000858.SZ 五粮液</option>
          </select>
        </label>
        <label>
          开始日期
          <input v-model="barStart" name="bar-sync-start" type="date" required />
        </label>
        <label>
          结束日期
          <input v-model="barEnd" name="bar-sync-end" type="date" required />
        </label>
        <button class="retry" type="submit" :disabled="barPhase === 'loading'">加入队列</button>
        <p v-if="barPhase === 'loading'">正在排队</p>
        <p v-else-if="barText" :role="barPhase === 'error' ? 'alert' : 'status'">{{ barText }}</p>
      </form>
      <h2>同步任务</h2>
      <ul v-if="jobs.length" class="provider-list">
        <li v-for="job in jobs" :key="job.id">
          {{ job.label }}<template v-if="job.summary !== job.label"> · {{ job.summary }}</template>
        </li>
      </ul>
      <p v-else class="muted">还没有同步任务</p>
    </section>
  </main>
</template>
