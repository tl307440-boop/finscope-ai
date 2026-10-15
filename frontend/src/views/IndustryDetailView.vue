<script setup lang="ts">
/**
 * 产业详情（/industry/:id）。
 *
 *  - GET  /api/v1/industries/{industry_id}/facts?as_of=   产业成员的已入库事实（公开）
 *  - GET  /api/v1/industries/{industry_id}/theses?as_of=  我的研究假设（需 Bearer）
 *  - POST /api/v1/industries/{industry_id}/theses         写下可证伪的假设
 *  - POST /api/v1/theses/{thesis_id}/evidence             为假设附上已入库依据
 *  - POST /api/v1/theses/{thesis_id}/assessments          按截止日更新假设状态
 *
 * 写操作都用后端实际声明的请求体：ThesisCreate / EvidenceCreate / AssessmentCreate。
 * 后端目前没有独立的「催化因素 / 风险事件」接口，因此该页签如实说明，
 * 只呈现已入库事实与假设推演，不编造催化或风险条目。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, authorizedFetch, readJson } from "../api/client"
import { asString, dataOf, listOf } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"

const props = defineProps<{ id: string }>()
const session = useSessionStore()

interface IndustryFact {
  id: string
  quote: string
  filed: string
  sourceUrl: string
}

interface ThesisEvidence {
  stance: string
  quote: string
}

interface Thesis {
  id: string
  title: string
  hypothesis: string
  supportCondition: string
  challengeCondition: string
  status: string
  rationale: string
  evidence: ThesisEvidence[]
}

type Phase = "loading" | "ready" | "error"

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
const facts = ref<IndustryFact[]>([])
const factsLoaded = ref(false)
const theses = ref<Thesis[]>([])
const thesesLoaded = ref(false)
const thesesDenied = ref(false)
const asOf = ref("2024-06-01")
const actionError = ref("")
const actionOk = ref("")
const busy = ref(false)

const draftTitle = ref("")
const draftHypothesis = ref("")
const draftSupport = ref("")
const draftChallenge = ref("")
const draftWindow = ref("90")

const evidenceTarget = ref("")
const evidenceFactId = ref("")
const evidenceStance = ref("support")
const assessmentTarget = ref("")
const assessmentAsOf = ref("2024-06-01")

const industryId = computed<string>(() => (Array.isArray(props.id) ? props.id[0] ?? "" : props.id))
const signedIn = computed<boolean>(() => Boolean(session.accessToken))
const loginHref = computed<string>(() => `/login?redirect=${encodeURIComponent(`/industry/${industryId.value}`)}`)
const draftReady = computed<boolean>(
  () =>
    draftTitle.value.trim().length > 0 &&
    draftHypothesis.value.trim().length > 0 &&
    draftSupport.value.trim().length > 0 &&
    draftChallenge.value.trim().length > 0,
)
const factOptions = computed<{ value: string; label: string }[]>(() =>
  facts.value.map((fact) => ({
    value: fact.id,
    label: `${fact.filed} · ${fact.quote.length > 46 ? `${fact.quote.slice(0, 46)}…` : fact.quote}`,
  })),
)
const thesisOptions = computed<{ value: string; label: string }[]>(() =>
  theses.value.map((thesis) => ({ value: thesis.id, label: thesis.title })),
)

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function authed(path: string, init: RequestInit): Promise<{ ok: boolean; status: number; body: unknown }> {
  const response = await authorizedFetch(path, init, session.accessToken, refreshToken)
  const body = await readJson(response)
  return { ok: response.ok, status: response.status, body }
}

function fail(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : "无法连接接口"
}

function serverMessage(body: unknown): string {
  const error = asRecord(asRecord(body)?.error)
  return asString(error?.message) ?? "请求未被接受"
}

async function loadFacts(): Promise<void> {
  const id = industryId.value.trim()
  if (!id) {
    return
  }
  const params = new URLSearchParams({ as_of: asOf.value.trim() })
  const response = await fetch(apiUrl(`/api/v1/industries/${encodeURIComponent(id)}/facts?${params.toString()}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw new Error(serverMessage(body))
  }
  facts.value = listOf(dataOf(body)?.facts).flatMap(parseFact)
  factsLoaded.value = true
}

async function loadTheses(): Promise<void> {
  const id = industryId.value.trim()
  if (!id || !signedIn.value) {
    thesesDenied.value = !signedIn.value
    thesesLoaded.value = false
    return
  }
  const params = new URLSearchParams({ as_of: asOf.value.trim() })
  const result = await authed(`/api/v1/industries/${encodeURIComponent(id)}/theses?${params.toString()}`, {
    method: "GET",
  })
  if (result.status === 401) {
    theses.value = []
    thesesLoaded.value = false
    thesesDenied.value = true
    return
  }
  if (!result.ok) {
    throw new Error(serverMessage(result.body))
  }
  theses.value = listOf(dataOf(result.body)?.theses).flatMap(parseThesis)
  thesesLoaded.value = true
  thesesDenied.value = false
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  facts.value = []
  factsLoaded.value = false
  theses.value = []
  thesesLoaded.value = false
  thesesDenied.value = false
  if (!industryId.value.trim()) {
    phase.value = "error"
    retryable.value = false
    failure.value = "路由里没有产业标识"
    return
  }
  try {
    await loadFacts()
    if (!signedIn.value) {
      const restored = await restoreSession()
      if (restored) {
        await loadTheses()
      } else {
        thesesDenied.value = true
      }
    } else {
      await loadTheses()
    }
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof Error ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function reloadWithDate(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!facts.value.length) {
    factsLoaded.value = false
  }
  try {
    await loadFacts()
    await loadTheses()
  } catch (caught) {
    actionError.value = caught instanceof Error ? caught.message : "无法连接接口"
  }
}

async function createThesis(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!draftReady.value) {
    actionError.value = "标题、假设、支撑条件与反驳条件都必须填写"
    return
  }
  const days = Number.parseInt(draftWindow.value, 10)
  const body = {
    title: draftTitle.value.trim(),
    hypothesis: draftHypothesis.value.trim(),
    support_condition: draftSupport.value.trim(),
    challenge_condition: draftChallenge.value.trim(),
    observation_window_days: Number.isFinite(days) && days >= 1 ? days : 90,
  }
  busy.value = true
  try {
    const result = await authed(`/api/v1/industries/${encodeURIComponent(industryId.value)}/theses`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    draftTitle.value = ""
    draftHypothesis.value = ""
    draftSupport.value = ""
    draftChallenge.value = ""
    draftWindow.value = "90"
    actionOk.value = "假设已保存"
    await loadTheses()
  } catch (caught) {
    actionError.value = fail(caught)
  } finally {
    busy.value = false
  }
}

async function addEvidence(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!evidenceTarget.value || !evidenceFactId.value) {
    actionError.value = "请先选择假设与已入库事实"
    return
  }
  busy.value = true
  try {
    const result = await authed(`/api/v1/theses/${encodeURIComponent(evidenceTarget.value)}/evidence`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fact_id: evidenceFactId.value, stance: evidenceStance.value }),
    })
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    evidenceFactId.value = ""
    actionOk.value = "依据已附到假设上"
    await loadTheses()
  } catch (caught) {
    actionError.value = fail(caught)
  } finally {
    busy.value = false
  }
}

async function assessThesis(): Promise<void> {
  actionError.value = ""
  actionOk.value = ""
  if (!assessmentTarget.value || !assessmentAsOf.value.trim()) {
    actionError.value = "请先选择假设与截止日"
    return
  }
  busy.value = true
  try {
    const result = await authed(`/api/v1/theses/${encodeURIComponent(assessmentTarget.value)}/assessments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ as_of: assessmentAsOf.value.trim() }),
    })
    if (!result.ok) {
      actionError.value = serverMessage(result.body)
      return
    }
    actionOk.value = "假设状态已按截止日更新"
    await loadTheses()
  } catch (caught) {
    actionError.value = fail(caught)
  } finally {
    busy.value = false
  }
}

function parseFact(value: unknown): IndustryFact[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      quote: asString(row?.quote) ?? "—",
      filed: asString(row?.filed) ?? "—",
      sourceUrl: asString(row?.source_url) ?? "",
    },
  ]
}

function parseThesis(value: unknown): Thesis[] {
  const row = asRecord(value)
  const id = asString(row?.id) ?? asString(row?.thesis_id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      title: asString(row?.title) ?? "未命名假设",
      hypothesis: asString(row?.hypothesis) ?? "",
      supportCondition: asString(row?.support_condition) ?? "",
      challengeCondition: asString(row?.challenge_condition) ?? "",
      status: asString(row?.status) ?? "",
      rationale: asString(row?.rationale) ?? "",
      evidence: listOf(row?.evidence).flatMap((item) => {
        const entry = asRecord(item)
        const quote = asString(entry?.quote)
        if (!quote) {
          return []
        }
        return [{ stance: asString(entry?.stance) ?? "", quote }]
      }),
    },
  ]
}

function statusTag(status: string): string {
  if (status === "supported" || status === "validated") {
    return "success"
  }
  if (status === "challenged" || status === "refuted") {
    return "danger"
  }
  return "info"
}

function stanceLabel(stance: string): string {
  if (stance === "support") {
    return "支撑"
  }
  if (stance === "challenge") {
    return "反驳"
  }
  return stance || "未知"
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return null
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取产业数据</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取产业详情</h1>
      <p>{{ failure }}</p>
      <button v-if="retryable" class="retry" type="button" @click="load">重试</button>
      <p class="muted">产业标识 {{ industryId }}</p>
    </section>

    <template v-else>
      <header class="panel">
        <h1>产业详情</h1>
        <p class="muted">产业 {{ industryId }}</p>
        <form class="form" @submit.prevent="reloadWithDate">
          <label>
            截止日
            <input v-model="asOf" name="industry-as-of" type="date" />
          </label>
          <button class="retry" type="submit">按截止日刷新</button>
        </form>
        <p v-if="actionOk" role="status" class="muted">{{ actionOk }}</p>
        <p v-if="actionError" role="alert">{{ actionError }}</p>
      </header>

      <el-tabs>
        <el-tab-pane label="产业链趋势事实">
          <div class="panel">
            <el-empty v-if="factsLoaded && !facts.length" description="该截止日没有已入库事实" />
            <el-table v-else-if="facts.length" :data="facts" stripe>
              <el-table-column prop="quote" label="已入库事实" min-width="300" />
              <el-table-column prop="filed" label="申报日" width="130" />
              <el-table-column label="来源" width="150">
                <template #default="scope">
                  <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" rel="noreferrer noopener">
                    原始链接
                  </a>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
            </el-table>
            <p v-else class="muted">正在等待事实数据</p>
          </div>
        </el-tab-pane>

        <el-tab-pane label="逻辑验证">
          <div class="panel">
            <el-alert v-if="thesesDenied && !signedIn" type="info" :closable="false" show-icon>
              <template #title>
                研究假设属于登录用户自己的记录，<a :href="loginHref">登录后可以写假设并附依据</a>。
              </template>
            </el-alert>
            <template v-else>
              <el-alert v-if="thesesDenied" type="warning" :closable="false" show-icon>
                <template #title>无法读取研究假设（登录可能已失效）</template>
              </el-alert>

              <h2>写下可证伪的假设</h2>
              <form class="form form-grid" @submit.prevent="createThesis">
                <label class="wide">
                  标题
                  <input v-model="draftTitle" name="thesis-title" type="text" maxlength="80" />
                </label>
                <label class="wide">
                  假设
                  <textarea
                    v-model="draftHypothesis"
                    name="thesis-hypothesis"
                    rows="3"
                    maxlength="400"
                  ></textarea>
                </label>
                <label>
                  支撑条件
                  <input v-model="draftSupport" name="thesis-support" type="text" maxlength="200" />
                </label>
                <label>
                  反驳条件
                  <input v-model="draftChallenge" name="thesis-challenge" type="text" maxlength="200" />
                </label>
                <label>
                  观察窗口（天）
                  <input v-model="draftWindow" name="thesis-window" type="number" min="1" max="3650" />
                </label>
                <div class="form-actions">
                  <button class="retry" type="submit" :disabled="busy || !draftReady">保存假设</button>
                </div>
              </form>

              <h2>为假设附上已入库依据</h2>
              <form class="form form-grid" @submit.prevent="addEvidence">
                <label>
                  假设
                  <el-select v-model="evidenceTarget" placeholder="选择假设" filterable>
                    <el-option
                      v-for="option in thesisOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </label>
                <label>
                  事实
                  <el-select v-model="evidenceFactId" placeholder="选择已入库事实" filterable>
                    <el-option
                      v-for="option in factOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </label>
                <label>
                  立场
                  <el-select v-model="evidenceStance">
                    <el-option label="支撑" value="support" />
                    <el-option label="反驳" value="challenge" />
                  </el-select>
                </label>
                <div class="form-actions">
                  <button class="retry" type="submit" :disabled="busy || !thesisOptions.length || !factOptions.length">
                    附上依据
                  </button>
                </div>
              </form>

              <h2>按截止日更新假设状态</h2>
              <form class="form" @submit.prevent="assessThesis">
                <label>
                  假设
                  <el-select v-model="assessmentTarget" placeholder="选择假设" filterable>
                    <el-option
                      v-for="option in thesisOptions"
                      :key="option.value"
                      :label="option.label"
                      :value="option.value"
                    />
                  </el-select>
                </label>
                <label>
                  截止日
                  <input v-model="assessmentAsOf" name="assessment-as-of" type="date" />
                </label>
                <button class="retry" type="submit" :disabled="busy || !assessmentTarget">重新评估</button>
              </form>

              <h2>我的假设</h2>
              <el-empty v-if="thesesLoaded && !theses.length" description="还没有写过研究假设" />
              <div v-else class="thesis-list">
                <article v-for="thesis in theses" :key="thesis.id" class="thesis">
                  <header>
                    <h3>{{ thesis.title }}</h3>
                    <el-tag v-if="thesis.status" :type="statusTag(thesis.status)" size="small">
                      {{ thesis.status }}
                    </el-tag>
                  </header>
                  <p>{{ thesis.hypothesis }}</p>
                  <p class="muted">支撑条件：{{ thesis.supportCondition || "—" }}</p>
                  <p class="muted">反驳条件：{{ thesis.challengeCondition || "—" }}</p>
                  <p v-if="thesis.rationale" class="muted">核对说明：{{ thesis.rationale }}</p>
                  <div v-if="thesis.evidence.length" class="evidence">
                    <p v-for="(item, index) in thesis.evidence" :key="index" class="muted">
                      [{{ stanceLabel(item.stance) }}] {{ item.quote }}
                    </p>
                  </div>
                  <p v-else class="muted">还没有附上任何依据。</p>
                </article>
              </div>
            </template>
          </div>
        </el-tab-pane>

        <el-tab-pane label="催化因素与风险事件">
          <div class="panel">
            <el-alert type="info" :closable="false" show-icon>
              <template #title>
                后端目前没有独立的催化因素 / 风险事件接口，本页不生成这类条目。
              </template>
            </el-alert>
            <p class="muted">
              可核对的依据只有两项：上表「产业链趋势事实」里已入库的事实条目，
              以及「逻辑验证」里针对假设附上的支撑 / 反驳依据。催化与风险请以这两处的事实链为准。
            </p>
          </div>
        </el-tab-pane>
      </el-tabs>
    </template>
  </main>
</template>

<style scoped>
.panel {
  margin-bottom: 16px;
}

h2 {
  margin: 20px 0 8px;
  font-size: 15px;
}

h3 {
  margin: 0;
  font-size: 15px;
}

.el-alert {
  margin-bottom: 12px;
}

.el-table {
  margin-top: 8px;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}

.form-grid label,
.form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}

.form-grid label.wide {
  grid-column: 1 / -1;
}

.form-actions {
  display: flex;
  align-items: flex-end;
}

.form input,
.form textarea,
.form select {
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text);
  font: inherit;
}

.form textarea {
  resize: vertical;
}

.form .el-select {
  width: 100%;
}

.thesis-list {
  display: grid;
  gap: 12px;
}

.thesis {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: color-mix(in srgb, var(--text) 3%, transparent);
}

.thesis header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.thesis p {
  margin: 4px 0;
}

.evidence {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}
</style>
