<script setup lang="ts">
/**
 * 公司原始财报（/company/:symbol/filings）。
 *
 *  - GET /api/v1/instruments/search     路由 symbol → instrument id 与 CIK
 *  - GET /api/v1/sec/filings?cik=       SEC 申报目录（status=edgar 表示来自 EDGAR 官方）
 *  - GET /api/v1/sec/facts?cik=&as_of=  观察日之前已申报的 XBRL 事实
 *  - GET /api/v1/market/hotspots        合规公告流，按公司简称筛出本公司公告（巨潮 / SEC）
 *  - GET /api/v1/documents              登录用户自己的上传文档（需 Bearer）
 *
 * 缺数据时按各自原因走空态，不补数字。
 */
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { ApiError, apiUrl, authorizedFetch, errorFrom, readJson } from "../api/client"
import { searchInstruments, type InstrumentHit } from "../api/instruments"
import { asNumber, asRecord, asString, dataOf, formatMoment, listOf } from "../api/json"
import {
  conceptLabel,
  conceptShortName,
  filingStatusLabel,
  formatFact,
  formLabel,
  parseFactPoint,
  periodKind,
  periodKindLabel,
  shiftDays,
  todayText,
  type FactPoint,
  type PeriodKind,
} from "../api/secFacts"
import { restoreSession, useSessionStore } from "../stores/auth"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const props = defineProps<{ symbol: string }>()
const session = useSessionStore()
const router = useRouter()

const PAGE_SIZE = 20

const DOCUMENT_STATUS: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" }> = {
  parsed: { label: "已解析", tone: "ok" },
  queued: { label: "等待解析", tone: "accent" },
  parsing: { label: "解析中", tone: "accent" },
  failed: { label: "解析失败", tone: "danger" },
  ocr_required: { label: "扫描件，需 OCR", tone: "warn" },
}

const KIND_OPTIONS: { value: PeriodKind | ""; label: string }[] = [
  { value: "", label: "全部口径" },
  { value: "quarter", label: "单季" },
  { value: "ytd", label: "年初至今累计" },
  { value: "annual", label: "全年" },
  { value: "instant", label: "期末时点" },
]

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

interface DocumentRow {
  id: string
  title: string
  status: string
  pageCount: number | null
  error: string
  uploadedAt: string
}

interface Announcement {
  id: string
  title: string
  url: string
  typeLabel: string
  sourceBadge: string
  day: string
}

type Phase = "loading" | "ready" | "error"

const phase = ref<Phase>("loading")
const failure = ref("")
const retryable = ref(true)
const metaAsOf = ref("")
const instrument = ref<InstrumentHit | null>(null)
const filings = ref<FilingRow[]>([])
const facts = ref<FactPoint[]>([])
const factsLoaded = ref(false)
const factsFailure = ref("")
const announcements = ref<Announcement[]>([])
const announcementsFailed = ref(false)
const documents = ref<DocumentRow[]>([])
const documentsState = ref<"idle" | "denied" | "error" | "ready">("idle")
const activeTab = ref("filings")
const asOf = ref(todayText())
const conceptFilter = ref("")
const kindFilter = ref<PeriodKind | "">("")
const factsPage = ref(1)

const symbol = computed<string>(() => (Array.isArray(props.symbol) ? props.symbol[0] ?? "" : props.symbol))
const cik = computed<string>(() => instrument.value?.cik ?? "")
const hasSec = computed<boolean>(() => Boolean(cik.value))
const companyName = computed<string>(() => instrument.value?.nameCn || instrument.value?.nameEn || symbol.value)

const heading = computed<string>(() => `${companyName.value} 原始财报`)

const subtitle = computed<string>(() => {
  const item = instrument.value
  const parts = [item?.displaySymbol ?? symbol.value, item?.nameEn]
  if (cik.value) {
    parts.push(`CIK ${cik.value}`)
  }
  return parts.filter(Boolean).join(" · ")
})

const fromEdgar = computed<boolean>(() => filings.value.some((row) => row.status.toLowerCase() === "edgar"))

const sourceLine = computed<string>(() => {
  if (!hasSec.value) {
    return "A 股财报数据暂未接入；定期报告与临时公告来自巨潮资讯，可在「交易所公告」中打开原文。"
  }
  return fromEdgar.value
    ? "申报目录与财务事实来自 SEC EDGAR 官方申报（XBRL companyfacts），按申报日入库。"
    : "申报目录与财务事实为已入库的 SEC 申报数据。"
})

const sortedFilings = computed<FilingRow[]>(() =>
  [...filings.value].sort((left, right) => right.filed.localeCompare(left.filed)),
)

const tabs = computed(() => {
  const list = [
    { key: "filings", label: `申报目录（${filings.value.length}）` },
    { key: "facts", label: `财务事实（${facts.value.length}）` },
    { key: "news", label: `交易所公告（${announcements.value.length}）` },
    { key: "documents", label: `我的上传文档（${documents.value.length}）` },
  ]
  return hasSec.value ? list : [list[2]!, list[0]!, list[1]!, list[3]!]
})

const conceptOptions = computed(() => {
  const concepts = [...new Set(facts.value.map((point) => point.concept))]
  const labelCount = new Map<string, number>()
  for (const concept of concepts) {
    const label = conceptLabel(concept)
    labelCount.set(label, (labelCount.get(label) ?? 0) + 1)
  }
  return concepts
    .map((value) => {
      const label = conceptLabel(value)
      return { value, label: (labelCount.get(label) ?? 0) > 1 ? `${label}（${conceptShortName(value)}）` : label }
    })
    .sort((left, right) => left.label.localeCompare(right.label, "zh-CN"))
})

const filteredFacts = computed<FactPoint[]>(() =>
  facts.value
    .filter((point) => !conceptFilter.value || point.concept === conceptFilter.value)
    .filter((point) => !kindFilter.value || periodKind(point) === kindFilter.value)
    .sort((left, right) => right.periodEnd.localeCompare(left.periodEnd) || left.concept.localeCompare(right.concept)),
)

const pagedFacts = computed(() =>
  filteredFacts.value.slice((factsPage.value - 1) * PAGE_SIZE, factsPage.value * PAGE_SIZE).map((point, index) => ({
    key: `${point.concept}-${point.periodStart}-${point.periodEnd}-${point.accessionNo}-${index}`,
    label: conceptLabel(point.concept),
    concept: conceptShortName(point.concept),
    value: formatFact(point.value, point.unit),
    kind: periodKindLabel(point),
    span: point.periodStart ? `${point.periodStart} 至 ${point.periodEnd}` : point.periodEnd,
    form: point.form,
    filed: point.filed,
    accessionNo: point.accessionNo,
    sourceUrl: point.sourceUrl,
  })),
)

const sources = computed<string[]>(() => {
  const list: string[] = []
  if (hasSec.value) {
    list.push("SEC EDGAR")
  }
  if (announcements.value.some((item) => item.sourceBadge === "巨潮资讯")) {
    list.push("巨潮资讯")
  }
  if (documentsState.value === "ready") {
    list.push("我的上传文档")
  }
  return list
})

watch([conceptFilter, kindFilter], () => {
  factsPage.value = 1
})

async function refreshToken(): Promise<string | null> {
  const restored = await restoreSession()
  return restored ? session.accessToken : null
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  retryable.value = true
  filings.value = []
  facts.value = []
  factsLoaded.value = false
  factsFailure.value = ""
  announcements.value = []
  documents.value = []
  documentsState.value = "idle"
  instrument.value = null

  const code = symbol.value.trim()
  if (!code) {
    phase.value = "error"
    retryable.value = false
    failure.value = "地址里缺少证券代码"
    return
  }

  try {
    const search = await searchInstruments(code, undefined, 10)
    const exact =
      search.results.find((hit) => hit.displaySymbol.toUpperCase() === code.toUpperCase()) ??
      search.results.find((hit) => hit.canonicalCode.toUpperCase() === code.toUpperCase()) ??
      search.results[0] ??
      null
    if (!exact) {
      phase.value = "error"
      retryable.value = false
      failure.value = search.emptyMessage ?? `没有找到 ${code}`
      return
    }
    instrument.value = exact
    activeTab.value = exact.cik ? "filings" : "news"
    const tasks: Promise<void>[] = [loadAnnouncements(), loadDocuments()]
    if (exact.cik) {
      tasks.push(loadFilings(exact.cik), reloadFacts())
    }
    await Promise.all(tasks)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    phase.value = "error"
  }
}

async function loadFilings(targetCik: string): Promise<void> {
  const response = await fetch(apiUrl(`/api/v1/sec/filings?cik=${encodeURIComponent(targetCik)}`))
  const body = await readJson(response)
  if (!response.ok) {
    throw errorFrom(body, response.headers.get("X-Request-ID"))
  }
  metaAsOf.value = formatMoment(asRecord(asRecord(body)?.meta)?.as_of)
  filings.value = listOf(dataOf(body)?.filings).flatMap(parseFiling)
}

async function reloadFacts(): Promise<void> {
  const targetCik = cik.value
  const day = asOf.value.trim()
  if (!targetCik || !day) {
    return
  }
  factsFailure.value = ""
  try {
    const params = new URLSearchParams({ cik: targetCik, as_of: day })
    const response = await fetch(apiUrl(`/api/v1/sec/facts?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    facts.value = listOf(dataOf(body)?.facts).flatMap(parseFactPoint)
    factsLoaded.value = true
    factsPage.value = 1
  } catch (caught) {
    facts.value = []
    factsLoaded.value = false
    factsFailure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
  }
}

async function loadAnnouncements(): Promise<void> {
  const name = instrument.value?.nameCn ?? ""
  announcementsFailed.value = false
  if (!name) {
    return
  }
  try {
    const params = new URLSearchParams({ start: shiftDays(asOf.value, -365), end: asOf.value })
    const response = await fetch(apiUrl(`/api/v1/market/hotspots?${params.toString()}`))
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    announcements.value = listOf(dataOf(body)?.events)
      .flatMap((event) => parseAnnouncement(event, name))
      .sort((left, right) => right.day.localeCompare(left.day))
  } catch {
    announcements.value = []
    announcementsFailed.value = true
  }
}

/** 上传文档属于登录用户；未登录只展示提示，不当作错误。 */
async function loadDocuments(): Promise<void> {
  if (!session.accessToken) {
    documentsState.value = "denied"
    return
  }
  try {
    const response = await authorizedFetch("/api/v1/documents", { method: "GET" }, session.accessToken, refreshToken)
    if (response.status === 401) {
      documentsState.value = "denied"
      return
    }
    const body = await readJson(response)
    if (!response.ok) {
      documentsState.value = "error"
      return
    }
    documents.value = listOf(dataOf(body)?.documents).flatMap(parseDocument)
    documentsState.value = "ready"
  } catch {
    documentsState.value = "error"
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
      filingType: asString(row?.filing_type) ?? "",
      accessionNo: asString(row?.accession_no) ?? "—",
      filed: asString(row?.filed) ?? "—",
      fiscalYear: asNumber(row?.fiscal_year),
      fiscalPeriod: asString(row?.fiscal_period) ?? "",
      sourceUrl: asString(row?.source_url) ?? "",
      status: asString(row?.status) ?? "",
    },
  ]
}

function parseDocument(value: unknown): DocumentRow[] {
  const row = asRecord(value)
  const id = asString(row?.id)
  if (!id) {
    return []
  }
  return [
    {
      id,
      title: asString(row?.title) ?? "未命名文件",
      status: asString(row?.parse_status) ?? "",
      pageCount: asNumber(row?.page_count),
      error: asString(row?.parse_error) ?? "",
      uploadedAt: asString(row?.uploaded_at) ?? "",
    },
  ]
}

function parseAnnouncement(value: unknown, name: string): Announcement[] {
  const record = asRecord(value)
  const title = asString(record?.title)
  const id = asString(record?.id)
  if (!title || !id || !title.startsWith(name)) {
    return []
  }
  const rest = title.slice(name.length).replace(/^[：:\s]+/, "") || title
  return [
    {
      id,
      title: formLabel(rest),
      url: asString(record?.canonical_url) ?? "",
      typeLabel: asString(record?.event_type_label) ?? "公告",
      sourceBadge: asString(record?.source_badge) ?? "",
      day: formatMoment(record?.published_at).slice(0, 10),
    },
  ]
}

function periodText(row: FilingRow): string {
  const year = row.fiscalYear === null ? "" : `FY${row.fiscalYear}`
  return [year, row.fiscalPeriod].filter(Boolean).join(" ") || "—"
}

function documentStatus(status: string): { label: string; tone: "ok" | "warn" | "danger" | "muted" | "accent" } {
  return DOCUMENT_STATUS[status] ?? { label: status ? "状态未知" : "—", tone: "muted" }
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
        <SectionTabs v-model="activeTab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <StateBlock
      v-else-if="phase === 'error'"
      state="error"
      title="无法读取财报资料"
      :message="failure"
      @retry="load"
    />

    <template v-else>
      <p class="source-line">
        <StatusPill :text="hasSec ? (fromEdgar ? 'SEC 官方' : '已入库') : 'A 股'" :tone="hasSec ? 'ok' : 'warn'" dot />
        {{ sourceLine }}
      </p>

      <PanelCard v-if="activeTab === 'filings'" title="申报目录" info="按申报日倒序；财年与期间以公司申报口径为准" flush>
        <template #icon>▤</template>
        <div v-if="!filings.length" class="pad">
          <StateBlock
            state="empty"
            :title="hasSec ? '没有申报记录' : 'A 股定期报告暂未接入'"
            :message="hasSec ? '观察日之前还没有入库的 SEC 申报。' : '请在「交易所公告」中查看巨潮资讯发布的定期报告原文。'"
          />
        </div>
        <el-table v-else :data="sortedFilings" stripe size="small">
          <el-table-column label="类型" min-width="170">
            <template #default="scope">{{ formLabel(scope.row.filingType) || "—" }}</template>
          </el-table-column>
          <el-table-column label="财年 / 期间" width="130">
            <template #default="scope"><span class="tabular">{{ periodText(scope.row) }}</span></template>
          </el-table-column>
          <el-table-column prop="filed" label="申报日" width="120" class-name="tabular" />
          <el-table-column prop="accessionNo" label="申报号" min-width="200" class-name="tabular" />
          <el-table-column label="来源" width="110">
            <template #default="scope">
              <StatusPill v-if="scope.row.status" :text="filingStatusLabel(scope.row.status)" tone="ok" />
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="原始数据" width="120">
            <template #default="scope">
              <a v-if="scope.row.sourceUrl" :href="scope.row.sourceUrl" rel="noreferrer noopener" target="_blank">打开 ›</a>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <PanelCard v-else-if="activeTab === 'facts'" title="财务事实" info="观察日之前已申报的 XBRL 科目；同一期间可能出现在多份申报中，以申报号区分" flush>
        <template #icon>▦</template>
        <template #actions>
          <form v-if="hasSec" class="filters" @submit.prevent="reloadFacts">
            <el-select v-model="conceptFilter" size="small" placeholder="全部指标" clearable class="concept-select" aria-label="指标">
              <el-option v-for="option in conceptOptions" :key="option.value" :label="option.label" :value="option.value" />
            </el-select>
            <el-select v-model="kindFilter" size="small" class="kind-select" aria-label="期间口径">
              <el-option v-for="option in KIND_OPTIONS" :key="option.value || 'all'" :label="option.label" :value="option.value" />
            </el-select>
            <label for="facts-as-of">观察日</label>
            <el-input id="facts-as-of" v-model="asOf" type="date" size="small" class="date-input" />
            <el-button size="small" type="primary" native-type="submit">查询</el-button>
          </form>
        </template>

        <div v-if="!hasSec" class="pad">
          <StateBlock state="empty" title="A 股财报数据暂未接入" message="暂无利润表、资产负债表和现金流科目。" />
        </div>
        <div v-else-if="factsFailure" class="pad">
          <StateBlock state="error" title="无法读取财务事实" :message="factsFailure" @retry="reloadFacts" />
        </div>
        <div v-else-if="!factsLoaded" class="pad">
          <StateBlock state="loading" :rows="3" />
        </div>
        <div v-else-if="!filteredFacts.length" class="pad">
          <StateBlock state="empty" title="没有符合条件的财务事实" :message="`观察日 ${asOf} 之前没有已申报的对应科目，可调整筛选或观察日。`" />
        </div>
        <template v-else>
          <el-table :data="pagedFacts" stripe size="small" row-key="key">
            <el-table-column label="指标" min-width="170">
              <template #default="scope">
                <div class="concept-cell">
                  <span>{{ scope.row.label }}</span>
                  <small v-if="scope.row.label !== scope.row.concept">{{ scope.row.concept }}</small>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="数值" min-width="140" align="right">
              <template #default="scope"><span class="tabular strong">{{ scope.row.value }}</span></template>
            </el-table-column>
            <el-table-column prop="kind" label="口径" width="120" />
            <el-table-column prop="span" label="期间" min-width="200" class-name="tabular" />
            <el-table-column prop="form" label="表单" width="80" />
            <el-table-column prop="filed" label="申报日" width="110" class-name="tabular" />
            <el-table-column prop="accessionNo" label="申报号" min-width="190" class-name="tabular" />
          </el-table>
          <div class="pager">
            <span class="muted">共 {{ filteredFacts.length }} 条</span>
            <el-pagination
              v-model:current-page="factsPage"
              :page-size="PAGE_SIZE"
              :total="filteredFacts.length"
              layout="prev, pager, next"
              size="small"
              background
            />
          </div>
        </template>
      </PanelCard>

      <PanelCard v-else-if="activeTab === 'news'" title="交易所公告" info="近一年收录的本公司公告，点击标题打开原文">
        <template #icon>◈</template>
        <StateBlock
          v-if="!announcements.length"
          :state="announcementsFailed ? 'error' : 'empty'"
          :title="announcementsFailed ? '公告读取失败' : '近一年暂无收录的公司公告'"
          :message="announcementsFailed ? '稍后可重试' : '公告按公司简称匹配，收录范围以数据中心同步为准。'"
          @retry="loadAnnouncements"
        />
        <ul v-else class="news-list">
          <li v-for="item in announcements" :key="item.id">
            <span class="news-day tabular">{{ item.day }}</span>
            <StatusPill :text="item.typeLabel" tone="accent" />
            <a v-if="item.url" :href="item.url" target="_blank" rel="noreferrer noopener">{{ item.title }}</a>
            <span v-else>{{ item.title }}</span>
            <span class="news-source">{{ item.sourceBadge }}</span>
          </li>
        </ul>
      </PanelCard>

      <PanelCard v-else title="我的上传文档" info="只显示当前账号上传的 PDF；解析完成后，财报解读会逐页引用原文" flush>
        <template #icon>▧</template>
        <template #actions>
          <router-link v-if="session.accessToken" class="link" :to="`/company/${encodeURIComponent(symbol)}/uploads`">上传财报 PDF ›</router-link>
        </template>
        <div v-if="documentsState === 'denied'" class="pad">
          <StateBlock state="denied" message="登录后可以查看自己的上传文档" />
        </div>
        <div v-else-if="documentsState === 'error'" class="pad">
          <StateBlock state="error" title="无法读取上传文档" message="稍后可重试" @retry="loadDocuments" />
        </div>
        <div v-else-if="!documents.length" class="pad">
          <StateBlock state="empty" title="还没有上传过文档" message="上传公司财报 PDF 并完成解析后，财报解读会逐页引用原文。" />
        </div>
        <el-table v-else :data="documents" stripe size="small">
          <el-table-column prop="title" label="文件" min-width="220" />
          <el-table-column label="页数" width="80" align="right">
            <template #default="scope"><span class="tabular">{{ scope.row.pageCount ?? "—" }}</span></template>
          </el-table-column>
          <el-table-column label="状态" width="150">
            <template #default="scope">
              <StatusPill :text="documentStatus(scope.row.status).label" :tone="documentStatus(scope.row.status).tone" :title="scope.row.error" />
            </template>
          </el-table-column>
          <el-table-column label="上传时间" min-width="160" class-name="tabular">
            <template #default="scope">{{ formatMoment(scope.row.uploadedAt) }}</template>
          </el-table-column>
        </el-table>
      </PanelCard>

      <SourceFooter :as-of="metaAsOf || asOf" :sources="sources" note="财务事实按申报日入库，观察日之后的申报不会出现" />
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

.source-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.pad {
  padding: 0 16px 10px;
}

.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
}

.concept-select {
  width: 170px;
}

.kind-select {
  width: 130px;
}

.date-input {
  width: 150px;
}

.concept-cell {
  display: grid;
  line-height: 1.4;
}

.concept-cell small {
  color: var(--faint);
  font-size: 12px;
}

.strong {
  color: var(--text-strong);
  font-weight: 600;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px 4px;
}

.news-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.news-list li {
  display: grid;
  grid-template-columns: auto auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 14px;
}

.news-list li:last-child {
  border-bottom: 0;
}

.news-day,
.news-source {
  color: var(--muted);
  font-size: 13px;
}

.news-list a {
  color: var(--text);
  text-decoration: none;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.news-list a:hover {
  color: var(--accent);
}

a,
.link {
  color: var(--accent);
  text-decoration: none;
  font-size: 13px;
}
</style>
