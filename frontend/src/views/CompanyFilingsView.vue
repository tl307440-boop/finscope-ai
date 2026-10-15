<script setup lang="ts">
/**
 * 公司财报资料（/company/:symbol/filings）。
 *
 * 数据全部来自后端已入库样例：
 *  - GET /api/v1/instruments/search  把路由参数 symbol 换成 instrument id 与 CIK
 *  - GET /api/v1/sec/filings?cik=    申报目录
 *  - GET /api/v1/sec/facts?cik=&as_of=  指定申报日的 XBRL 事实
 *  - GET /api/v1/documents           登录用户自己的上传文档（需 Bearer）
 *
 * 后端在 data.notice 与 meta.source / meta.data_mode 里如实标注了数据来源，
 * 本页原样转发，不自行推断，也不在缺数据时编造任何数字。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { asNumber, asString, dataOf, formatNumber, formatMoment, listOf } from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"

const props = defineProps<{ symbol: string }>()
const session = useSessionStore()

interface FilingRow {
  id: string
  filingType: string
  accessionNo: string
  filed: string
  fiscalYear: number | null
  fiscalPeriod: string
  sourceUrl: string
  status: string
}

interface FactRow {
  concept: string
  unit: string
  value: string
  periodStart: string
  periodEnd: string
  form: string
  fiscalYear: number | null
  fiscalPeriod: string
  filed: string
  accessionNo: string
  sourceUrl: string
}

interface DocumentRow {
  id: string
  filename: string
  status: string
  createdAt: string
  instrumentId: string
}

type Phase = "loading" | "ready" | "error"

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
const notice = ref("")
const metaSource = ref("")
const metaDataMode = ref("")
const metaAsOf = ref("")
const instrument = ref<InstrumentHit | null>(null)
const filings = ref<FilingRow[]>([])
const facts = ref<FactRow[]>([])
const factsLoaded = ref(false)
const factsFailure = ref("")
const documents = ref<DocumentRow[]>([])
const documentsDenied = ref(false)

/** SEC 样例事实的观察日；默认与仓库既有页面保持一致的可复现日期。 */
const asOf = ref("2024-06-01")

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const cik = computed<string>(() => instrument.value?.cik ?? "")

/** 公司确实没有 CIK：SEC 申报本身不存在，不是接口故障。 */
const nonSecCompany = computed<boolean>(() => instrument.value !== null && !instrument.value.cik)

const sourceLine = computed<string>(() => {
  const parts: string[] = []
  if (metaSource.value) {
    parts.push(`数据来源 ${metaSource.value}`)
  }
  if (metaDataMode.value) {
    parts.push(`数据模式 ${metaDataMode.value}`)
  }
  if (metaAsOf.value) {
    parts.push(`服务端截止 ${formatMoment(metaAsOf.value)}`)
  }
  return parts.join(" · ")
})

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

function readMeta(value: unknown): void {
  const meta = asRecord(asRecord(value)?.meta)
  metaSource.value = asString(meta?.source) ?? ""
  metaDataMode.value = asString(meta?.data_mode) ?? ""
  metaAsOf.value = asString(meta?.as_of) ?? ""
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  notice.value = ""
  filings.value = []
  facts.value = []
  factsLoaded.value = false
  factsFailure.value = ""
  documents.value = []
  documentsDenied.value = false
  instrument.value = null

  const code = symbol.value.trim()
  if (!code) {
    phase.value = "error"
    retryable.value = false
    failure.value = "路由里没有证券代码，无法定位公司"
    return
  }

  try {
    const search = await searchInstruments(code, undefined, 10)
    const exact =
      search.results.find((hit) => hit.displaySymbol === code) ??
      search.results.find((hit) => hit.canonicalCode === code) ??
      search.results[0] ??
      null
    if (!exact) {
      phase.value = "error"
      retryable.value = false
      failure.value = search.emptyMessage ?? `暂无与 ${code} 匹配的证券`
      return
    }
    instrument.value = exact

    if (exact.cik) {
      await loadFilings(exact.cik)
      await loadFacts(exact.cik)
    }
    await loadDocuments()
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

async function loadFilings(targetCik: string): Promise<void> {
  const response = await fetch(apiUrl(`/api/v1/sec/filings?cik=${encodeURIComponent(targetCik)}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  readMeta(body)
  const data = dataOf(body)
  notice.value = asString(data?.notice) ?? ""
  filings.value = listOf(data?.filings).flatMap(parseFiling)
}

async function loadFacts(targetCik: string): Promise<void> {
  const day = asOf.value.trim()
  if (!day) {
    return
  }
  const params = new URLSearchParams({ cik: targetCik, as_of: day })
  const response = await fetch(apiUrl(`/api/v1/sec/facts?${params.toString()}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  readMeta(body)
  const data = dataOf(body)
  if (!notice.value) {
    notice.value = asString(data?.notice) ?? ""
  }
  facts.value = listOf(data?.facts).flatMap(parseFact)
  factsLoaded.value = true
}

/** 上传文档属于登录用户；未登录只展示提示，不当作错误。 */
async function loadDocuments(): Promise<void> {
  if (!session.accessToken) {
    documentsDenied.value = true
    return
  }
  try {
    const response = await authorizedFetch(
      "/api/v1/documents",
      { method: "GET" },
      session.accessToken,
      refreshToken,
    )
    if (response.status === 401) {
      documentsDenied.value = true
      return
    }
    const body = await readJson(response)
    if (!response.ok) {
      return
    }
    documents.value = listOf(dataOf(body)?.documents).flatMap(parseDocument)
  } catch {
    // 文档列表失败不影响申报目录，静默降级并保持提示。
    documentsDenied.value = true
  }
}

async function reloadFacts(): Promise<void> {
  const targetCik = cik.value
  if (!targetCik) {
    return
  }
  factsFailure.value = ""
  try {
    await loadFacts(targetCik)
  } catch (caught) {
    facts.value = []
    factsLoaded.value = false
    factsFailure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
  }
}

function parseFiling(value: unknown): FilingRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      filingType: asString(row?.filing_type) ?? "—",
      accessionNo: asString(row?.accession_no) ?? "—",
      filed: asString(row?.filed) ?? "—",
      fiscalYear: asNumber(row?.fiscal_year),
      fiscalPeriod: asString(row?.fiscal_period) ?? "—",
      sourceUrl: asString(row?.source_url) ?? "",
      status: asString(row?.status) ?? "",
    },
  ]
}

function parseFact(value: unknown): FactRow[] {
  const row = asRecord(value)
  const concept = asString(row?.concept)
  const amount = asString(row?.value)
  if (!concept || !amount) {
    return []
  }
  return [
    {
      concept,
      unit: asString(row?.unit) ?? "—",
      value: amount,
      periodStart: asString(row?.period_start) ?? "",
      periodEnd: asString(row?.period_end) ?? "—",
      form: asString(row?.form) ?? "—",
      fiscalYear: asNumber(row?.fiscal_year),
      fiscalPeriod: asString(row?.fiscal_period) ?? "—",
      filed: asString(row?.filed) ?? "—",
      accessionNo: asString(row?.accession_no) ?? "—",
      sourceUrl: asString(row?.source_url) ?? "",
    },
  ]
}

function parseDocument(value: unknown): DocumentRow[] {
  const row = asRecord(value)
  const id = asString(row?.id) ?? asString(row?.document_id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      filename: asString(row?.filename) ?? asString(row?.title) ?? "未命名文件",
      status: asString(row?.status) ?? "—",
      createdAt: asString(row?.created_at) ?? "",
      instrumentId: asString(row?.instrument_id) ?? "",
    },
  ]
}

function span(row: FactRow): string {
  if (row.periodStart) {
    return `${row.periodStart} ~ ${row.periodEnd}`
  }
  return row.periodEnd
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
    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在读取财报资料</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法读取财报资料</h1>
      <p>{{ failure }}</p>
      <button v-if="retryable" class="retry" type="button" @click="load">重试</button>
      <p class="muted">证券代码 {{ symbol }}</p>
    </section>

    <template v-else>
      <header class="panel">
        <h1>{{ instrument?.nameCn || instrument?.nameEn || symbol }} 财报资料</h1>
        <p class="muted">
          {{ instrument?.displaySymbol }} · {{ instrument?.exchange }} · {{ instrument?.market }}
          <template v-if="cik"> · CIK {{ cik }}</template>
        </p>
        <p v-if="sourceLine" class="muted">{{ sourceLine }}</p>
        <el-alert v-if="notice" type="warning" :closable="false" show-icon>
          <template #title>{{ notice }}</template>
        </el-alert>
      </header>

      <el-tabs>
        <el-tab-pane label="申报目录">
          <div v-if="nonSecCompany" class="panel">
            <el-empty description="该公司没有 CIK，样例里没有对应的 SEC 申报" />
          </div>
          <div v-else-if="!filings.length" class="panel">
            <el-empty description="这批样例里没有该公司的申报记录" />
          </div>
          <div v-else class="panel">
            <el-table :data="filings" stripe>
              <el-table-column prop="filingType" label="类型" width="110" />
              <el-table-column prop="accessionNo" label="申报号" min-width="190" />
              <el-table-column prop="filed" label="申报日" width="120" />
              <el-table-column label="报告期" width="130">
                <template #default="scope">
                  {{ formatNumber(scope.row.fiscalYear, 0) }} {{ scope.row.fiscalPeriod }}
                </template>
              </el-table-column>
              <el-table-column label="样例状态" width="110">
                <template #default="scope">
                  <el-tag v-if="scope.row.status" type="info" size="small">
                    {{ scope.row.status }}
                  </el-tag>
                  <span v-else>—</span>
                </template>
              </el-table-column>
              <el-table-column label="来源" min-width="140">
                <template #default="scope">
                  <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" rel="noreferrer noopener">
                    原始链接
                  </a>
                  <span v-else class="muted">—</span>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane label="XBRL 事实">
          <div class="panel">
            <form class="form" @submit.prevent="reloadFacts">
              <label>
                申报日
                <input v-model="asOf" name="facts-as-of" type="date" />
              </label>
              <button class="retry" type="submit" :disabled="!cik">查看该日可用事实</button>
            </form>
            <p v-if="nonSecCompany" class="muted">该公司没有 CIK，样例里没有对应的 SEC 事实。</p>
            <p v-else-if="factsFailure" role="alert">{{ factsFailure }}</p>
            <el-empty v-else-if="factsLoaded && !facts.length" description="该申报日没有可引用的事实" />
            <el-table v-else-if="facts.length" :data="facts" stripe>
              <el-table-column prop="concept" label="概念" min-width="180" />
              <el-table-column prop="unit" label="单位" width="90" />
              <el-table-column prop="value" label="数值" width="120" />
              <el-table-column label="区间" min-width="190">
                <template #default="scope">{{ span(scope.row) }}</template>
              </el-table-column>
              <el-table-column prop="form" label="表单" width="100" />
              <el-table-column prop="filed" label="申报日" width="120" />
              <el-table-column prop="accessionNo" label="申报号" min-width="190" />
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane label="我的上传文档">
          <div class="panel">
            <el-alert v-if="documentsDenied && !session.accessToken" type="info" :closable="false">
              <template #title>登录后可以查看自己的上传文档</template>
            </el-alert>
            <p v-else-if="documentsDenied" class="muted">无法读取上传文档，稍后可重试。</p>
            <el-empty v-else-if="!documents.length" description="还没有上传过文档" />
            <el-table v-else :data="documents" stripe>
              <el-table-column prop="filename" label="文件" min-width="200" />
              <el-table-column prop="status" label="状态" width="120" />
              <el-table-column label="上传时间" min-width="160">
                <template #default="scope">{{ formatMoment(scope.row.createdAt) }}</template>
              </el-table-column>
            </el-table>
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

.el-alert {
  margin-top: 12px;
}

.el-table {
  margin-top: 12px;
  --el-table-border-color: var(--line);
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: color-mix(in srgb, var(--text) 6%, transparent);
  --el-table-text-color: var(--text);
  --el-table-header-text-color: var(--muted);
  --el-table-row-hover-bg-color: color-mix(in srgb, var(--accent) 12%, transparent);
}
</style>
