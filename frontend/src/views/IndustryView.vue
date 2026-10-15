<script setup lang="ts">
import { onMounted, ref } from "vue"
import { apiUrl, authorizedFetch, readJson } from "../api/client"
import { restoreSession, useSessionStore } from "../stores/auth"

const session = useSessionStore()

interface IndustryRow {
  id: string
  name: string
  description: string
}

interface FactRow {
  id: string
  quote: string
}

interface EvidenceRow {
  stance: string
  quote: string
}

const phase = ref<"loading" | "ready" | "error">("loading")
const message = ref("")
const industries = ref<IndustryRow[]>([])
const industryId = ref("")
const facts = ref<FactRow[]>([])
const title = ref("样例收入比较")
const hypothesis = ref("截止日可见的样例里，后期美元收入高于早期。")
const supportCondition = ref("能指出后期美元收入及其申报号")
const challengeCondition = ref("股数行不能当作收入证据")
const thesisId = ref("")
const statusText = ref("")
const rationale = ref("")
const evidence = ref<EvidenceRow[]>([])

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  try {
    const response = await fetch(apiUrl("/api/v1/industries?q=信息技术"))
    const payload = await readJson(response)
    const rows = asRecord(asRecord(payload)?.data)?.industries
    industries.value = Array.isArray(rows) ? rows.flatMap(readIndustry) : []
    industryId.value = industries.value[0]?.id ?? ""
    if (industryId.value && session.accessToken) {
      await loadFacts()
    }
    phase.value = industries.value.length ? "ready" : "error"
    if (!industries.value.length) {
      message.value = "还没有行业分类"
    }
  } catch {
    message.value = "无法连接接口"
    phase.value = "error"
  }
}

async function loadFacts(): Promise<void> {
  const response = await fetch(
    apiUrl(`/api/v1/industries/${industryId.value}/facts?as_of=2024-06-01`),
  )
  const payload = await readJson(response)
  const rows = asRecord(asRecord(payload)?.data)?.facts
  facts.value = Array.isArray(rows) ? rows.flatMap(readFact) : []
}

async function createThesis(): Promise<void> {
  if (!session.accessToken) {
    message.value = "请先登录后再写下假设"
    return
  }
  const response = await authorizedFetch(
    `/api/v1/industries/${industryId.value}/theses`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: title.value,
        hypothesis: hypothesis.value,
        support_condition: supportCondition.value,
        challenge_condition: challengeCondition.value,
      }),
    },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  const data = asRecord(asRecord(payload)?.data)
  thesisId.value = asString(data?.id) ?? ""
  statusText.value = asString(data?.status) ?? ""
  rationale.value = asString(data?.rationale) ?? ""
  if (!response.ok) {
    message.value = "无法保存假设"
  }
}

async function attach(factId: string, stance: "support" | "challenge"): Promise<void> {
  if (!session.accessToken || !thesisId.value) {
    return
  }
  const response = await authorizedFetch(
    `/api/v1/theses/${thesisId.value}/evidence`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fact_id: factId, stance }),
    },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  applyThesis(asRecord(asRecord(payload)?.data))
}

async function assess(): Promise<void> {
  if (!session.accessToken || !thesisId.value) {
    return
  }
  const response = await authorizedFetch(
    `/api/v1/theses/${thesisId.value}/assessments`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: "2024-06-01" }),
    },
    session.accessToken,
    refreshToken,
  )
  const payload = await readJson(response)
  applyThesis(asRecord(asRecord(payload)?.data))
}

function applyThesis(data: Record<string, unknown> | null): void {
  if (!data) {
    return
  }
  statusText.value = asString(data.status) ?? statusText.value
  rationale.value = asString(data.rationale) ?? ""
  const rows = data.evidence
  evidence.value = Array.isArray(rows) ? rows.flatMap(readEvidence) : []
}

function readIndustry(value: unknown): IndustryRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  const name = asString(row?.name)
  const description = asString(row?.description) ?? ""
  if (!id || !name) {
    return []
  }
  return [{ id, name, description }]
}

function readFact(value: unknown): FactRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  const quote = asString(row?.quote)
  if (!id || !quote) {
    return []
  }
  return [{ id, quote }]
}

function readEvidence(value: unknown): EvidenceRow[] {
  const row = asRecord(value)
  const stance = asString(row?.stance)
  const quote = asString(row?.quote)
  if (!stance || !quote) {
    return []
  }
  return [{ stance, quote }]
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

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取行业</section>
    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取行业</h1>
      <p>{{ message }}</p>
    </section>
    <section v-else class="panel">
      <h1>行业假设</h1>
      <p class="muted">{{ industries[0]?.description }}</p>
      <p v-if="!session.accessToken" class="muted">
        <a href="/login?redirect=/industries">请先登录后再写下假设</a>
      </p>
      <form class="form" @submit.prevent="createThesis">
        <label>
          标题
          <input name="thesis-title" v-model="title" />
        </label>
        <label>
          假设
          <input name="thesis-hypothesis" v-model="hypothesis" />
        </label>
        <label>
          什么情况算支持
          <input name="support-condition" v-model="supportCondition" />
        </label>
        <label>
          什么情况算反证
          <input name="challenge-condition" v-model="challengeCondition" />
        </label>
        <button class="retry" type="submit">保存假设</button>
      </form>
      <p v-if="statusText" role="status">状态 {{ statusText }}。{{ rationale }}</p>
      <ul v-if="evidence.length">
        <li v-for="item in evidence" :key="`${item.stance}-${item.quote}`">
          {{ item.stance === "support" ? "支持" : "反证" }}：{{ item.quote }}
        </li>
      </ul>
      <h2>已入库事实</h2>
      <p v-if="!facts.length" class="muted">登录后可以给假设附上样例事实。</p>
      <ul v-else>
        <li v-for="fact in facts" :key="fact.id">
          {{ fact.quote }}
          <button type="button" @click="attach(fact.id, 'support')">用作支持</button>
          <button type="button" @click="attach(fact.id, 'challenge')">用作反证</button>
        </li>
      </ul>
      <button v-if="thesisId" class="retry" type="button" @click="assess">按 2024-06-01 更新状态</button>
      <p v-if="message" role="alert">{{ message }}</p>
    </section>
  </main>
</template>
