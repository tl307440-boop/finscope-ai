<script setup lang="ts">
/**
 * 公司财报资料（/company/:symbol/filings）。视觉沿用设计规范（PageHeader + PanelCard + SectionTabs + StateBlock）。
 *
 * 数据全部来自后端已入库样例，一个调用都没有改动：
 *  - GET /api/v1/instruments/search     把路由参数 symbol 换成 instrument id 与 CIK
 *  - GET /api/v1/sec/filings?cik=       申报目录
 *  - GET /api/v1/sec/facts?cik=&as_of=  指定申报日的 XBRL 事实
 *  - GET /api/v1/documents              登录用户自己的上传文档（需 Bearer）
 *
 * 后端在 data.notice 与 meta.source / meta.data_mode / meta.as_of 里如实标注了来源，
 * 本页原样转发；缺数据时按各自原因走 StateBlock 空态，不补数字。
 */
import { computed, onMounted, ref } from "vue"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import {
  asNumber,
  asRecord,
  asString,
  dataOf,
  formatMoment,
  formatNumber,
  listOf,
} from "../api/json"
import { restoreSession, useSessionStore } from "../stores/auth"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"

const props = defineProps<{ symbol: string }>()
const session = useSessionStore()
const router = useRouter()

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
const activeTab = ref("filings")

/** SEC 样例事实的观察日；默认与仓库既有页面保持一致的可复现日期。 */
const asOf = ref("2024-06-01")

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const cik = computed<string>(() => instrument.value?.cik ?? "")

/** 公司确实没有 CIK：SEC 申报本身不存在，不是接口故障。 */
const nonSecCompany = computed<boolean>(() => instrument.value !== null && !instrument.value.cik)

const heading = computed<string>(
  () => `${instrument.value?.nameCn || instrument.value?.nameEn || symbol.value} 财报资料`,
)

const subtitle = computed<string>(() => {
  const item = instrument.value
  const parts = [item?.displaySymbol ?? symbol.value, item?.exchange, item?.market]
    .filter((part): part is string => Boolean(part))
  if (cik.value) {
    parts.push(`CIK ${cik.value}`)
  }
  if (metaDataMode.value) {
    parts.push(`数据模式 ${metaDataMode.value}`)
  }
  return parts.join(" · ")
})

const sources = computed<string[]>(() => {
  const list = ["/api/v1/instruments/search", "/api/v1/sec/filings", "/api/v1/sec/facts"]
  if (session.accessToken) {
    list.push("/api/v1/documents")
  }
  return list
})

const tabs = computed(() => [
  { key: "filings", label: `申报目录（${filings.value.length}）` },
  { key: "facts", label: `XBRL 事实（${facts.value.length}）` },
  { key: "documents", label: `我的上传文档（${documents.value.length}）` },
])

const filingsEmptyReason = computed<string>(() => {
  if (nonSecCompany.value) {
    return "该公司没有 CIK，样例里没有对应的 SEC 申报。"
  }
  return "这批样例里没有该公司的申报记录。"
})

const factsEmptyReason = computed<string>(() => {
  if (nonSecCompany.value) {
    return "该公司没有 CIK，样例里没有对应的 SEC 事实。"
  }
  return `申报日 ${asOf.value} 之前没有可引用的事实。未申报的日期不会用演示数字填补。`
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

function goCompany(): void {
  void router.push(`/company/${encodeURIComponent(symbol.value)}`)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page stack">
    <nav class="crumb" aria-label="面包屑">
      <router-link :to="`/company/${encodeURIComponent(symbol)}`">公司研究</router-link>
      <i aria-hidden="true">›</i>
      <strong>原始财报</strong>
    </nav>

    <PageHeader :title="heading" :subtitle="subtitle">
      <template #actions>
        <el-button size="small" @click="goCompany">返回公司详情</el-button>
      </template>
      <template #tabs>
        <SectionTabs :tabs="tabs" v-model="activeTab" />
      </template>
    </PageHeader>

    <el-alert v-if="notice" type="warning" :closable="false" show-icon>
      <template #title>{{ notice }}</template>
    </el-alert>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取财报资料"
      :message="`${failure}（证券代码 ${symbol}）`"
      @retry="load"
    />

    <template v-else>
      <PanelCard v-if="activeTab === 'filings'" title="申报目录" info="GET /api/v1/sec/filings?cik=">
        <template #icon>▤</template>
        <StateBlock
          v-if="!filings.length"
          state="empty"
          :title="nonSecCompany ? '该公司没有 CIK' : '没有申报记录'"
          :message="filingsEmptyReason"
        />
        <el-table v-else :data="filings" stripe>
          <el-table-column prop="filingType" label="类型" width="110" />
          <el-table-column prop="accessionNo" label="申报号" min-width="190" class-name="tabular" />
          <el-table-column prop="filed" label="申报日" width="120" class-name="tabular" />
          <el-table-column label="报告期" width="130">
            <template #default="scope">
              <span class="tabular">
                {{ formatNumber(scope.row.fiscalYear, 0) }} {{ scope.row.fiscalPeriod }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="样例状态" width="120">
            <template #default="scope">
              <el-tag v-if="scope.row.status" type="info" size="small">{{ scope.row.status }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="来源" min-width="140">
            <template #default="scope">
              <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" rel="noreferrer noopener" target="_blank">
                原始链接
              </a>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <PanelCard v-else-if="activeTab === 'facts'" title="XBRL 事实" info="GET /api/v1/sec/facts?cik=&as_of=">
        <template #icon>▦</template>
        <template #actions>
          <form class="asof" @submit.prevent="reloadFacts">
            <label for="facts-as-of">申报日</label>
            <el-input id="facts-as-of" v-model="asOf" type="date" size="small" style="width: 150px" />
            <el-button size="small" type="primary" native-type="submit" :disabled="!cik">
              查看该日可用事实
            </el-button>
          </form>
        </template>

        <StateBlock
          v-if="nonSecCompany"
          state="empty"
          title="该公司没有 CIK"
          message="样例里没有对应的 SEC 事实，因此没有可展示的科目。"
        />
        <StateBlock
          v-else-if="factsFailure"
          state="error"
          title="无法读取 XBRL 事实"
          :message="factsFailure"
          @retry="reloadFacts"
        />
        <StateBlock
          v-else-if="factsLoaded && !facts.length"
          state="empty"
          title="该申报日没有可引用的事实"
          :message="factsEmptyReason"
        />
        <StateBlock v-else-if="!factsLoaded" state="loading" :rows="3" />
        <el-table v-else :data="facts" stripe>
          <el-table-column prop="concept" label="概念" min-width="200" />
          <el-table-column prop="unit" label="单位" width="90" />
          <el-table-column prop="value" label="数值" width="130" class-name="tabular" />
          <el-table-column label="区间" min-width="200">
            <template #default="scope"><span class="tabular">{{ span(scope.row) }}</span></template>
          </el-table-column>
          <el-table-column prop="form" label="表单" width="100" />
          <el-table-column prop="filed" label="申报日" width="120" class-name="tabular" />
          <el-table-column prop="accessionNo" label="申报号" min-width="190" class-name="tabular" />
          <el-table-column label="来源" width="110">
            <template #default="scope">
              <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" rel="noreferrer noopener" target="_blank">
                原始链接
              </a>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <PanelCard v-else title="我的上传文档" info="GET /api/v1/documents（需登录，只返回当前用户的文档）">
        <template #icon>▧</template>
        <StateBlock
          v-if="documentsDenied && !session.accessToken"
          state="denied"
          message="登录后可以查看自己的上传文档"
        />
        <StateBlock
          v-else-if="documentsDenied"
          state="error"
          title="无法读取上传文档"
          message="接口没有返回文档列表，稍后可重试。"
          @retry="load"
        />
        <StateBlock
          v-else-if="!documents.length"
          state="empty"
          title="还没有上传过文档"
          message="在 /company/{symbol}/uploads 上传 PDF 后，AI 报告会逐页引用原文。"
        />
        <el-table v-else :data="documents" stripe>
          <el-table-column prop="filename" label="文件" min-width="200" />
          <el-table-column prop="status" label="状态" width="120">
            <template #default="scope">
              <el-tag v-if="scope.row.status" type="info" size="small">{{ scope.row.status }}</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" min-width="160" class-name="tabular">
            <template #default="scope">{{ formatMoment(scope.row.createdAt) }}</template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <SourceFooter
        :as-of="metaAsOf"
        :sources="sources"
        :note="`数据来源 ${metaSource || '接口未标注'}；${notice || '接口未提供来源说明'}`"
      />
    </template>
  </main>
</template>

<style scoped>
.crumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 13px;
}

.crumb a {
  color: var(--muted);
  text-decoration: none;
}

.crumb a:hover {
  color: var(--accent);
}

.crumb strong {
  color: var(--text);
  font-weight: 600;
}

.el-alert {
  margin: 0;
}

.asof {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
}

.asof :deep(.el-input__wrapper) {
  padding: 0 8px;
}
</style>
