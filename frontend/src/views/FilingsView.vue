<script setup lang="ts">
/**
 * SEC 样例（`GET /api/v1/sec/facts?cik=320193&as_of=...`）。
 *
 * 固定的 CIK 0000320193，返回的是随仓库附带的解析样例，说明里明确写了
 * 「不是本次向 SEC 拉取的数据」。所以这一页只负责把样例事实逐条列清楚：
 * 概念、金额、申报表、申报日、accession 与来源链接；当天没有已申报事实时，
 * 直接写后端的 notice 和「该日期还看不到已申报的事实」，不换别的日期凑数据。
 */
import { computed, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { describeFacts, type SecFactLine } from "../api/secFacts"
import { asRecord, asString } from "../api/json"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

const CIK = "0000320193"

const asOf = ref("2024-03-01")
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")
const requestId = ref("")
const lines = ref<string[]>([])
const facts = ref<SecFactLine[]>([])

/** 后端 notice 原文始终是 describeFacts 的第一行；缺省时用它的兜底文案。 */
const notice = computed(() => lines.value[0] ?? "本地样例，不是本次从 SEC 拉取。")

/** 事实为空时 describeFacts 会补一句「该日期还看不到已申报的事实」，原样展示。 */
const emptyMessage = computed(() => lines.value.slice(1).join(" ") || "该日期还看不到已申报的事实")

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

async function load(): Promise<void> {
  if (!asOf.value) {
    phase.value = "error"
    failure.value = "请选择日期"
    lines.value = []
    facts.value = []
    return
  }
  phase.value = "loading"
  failure.value = ""
  requestId.value = ""
  lines.value = []
  facts.value = []
  try {
    const response = await fetch(
      apiUrl(`/api/v1/sec/facts?cik=320193&as_of=${encodeURIComponent(asOf.value)}`),
      { headers: { Accept: "application/json" }, credentials: "include" },
    )
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    requestId.value = asString(asRecord(asRecord(body)?.meta)?.request_id) ?? ""
    const data = asRecord(asRecord(body)?.data)
    const rows = Array.isArray(data?.facts) ? data.facts.flatMap(parseFact) : []
    facts.value = rows
    lines.value = describeFacts({ notice: asString(data?.notice), facts: rows })
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接接口"
    phase.value = "error"
  }
}

function parseFact(value: unknown): SecFactLine[] {
  const record = asRecord(value)
  const concept = asString(record?.concept)
  const unit = asString(record?.unit)
  const amount = asString(record?.value)
  const filed = asString(record?.filed)
  const accession = asString(record?.accession_no)
  const source = asString(record?.source_url)
  const form = asString(record?.form)
  if (!concept || !unit || !amount || !filed || !accession || !source || !form) {
    return []
  }
  return [
    {
      concept,
      unit,
      value: amount,
      filed,
      accessionNo: accession,
      sourceUrl: source,
      form,
    },
  ]
}
</script>

<template>
  <main class="page">
    <PageHeader
      title="SEC 样例"
      :subtitle="`CIK ${CIK} · 随仓库附带的解析样例，不是本次向 SEC 拉取的数据`"
    >
      <template #filters>
        <form class="filters-form" @submit.prevent="load">
          <label class="field">
            观察日
            <input v-model="asOf" name="sec-as-of" type="date" />
          </label>
          <button class="retry" type="submit" :disabled="phase === 'loading'">查看来源</button>
        </form>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading' || phase === 'idle'" state="loading" :rows="3" />

    <template v-else-if="phase === 'error'">
      <StateBlock state="error" :message="failure" @retry="load" />
      <p v-if="requestId" class="muted request-line">请求号 {{ requestId }}</p>
    </template>

    <template v-else>
      <PanelCard title="样例说明" :info="`as_of ${asOf || '—'}`">
        <p class="notice">{{ notice }}</p>
        <p class="muted">
          CIK {{ CIK }} 写死在本页，不提供其它公司；事实条目来自仓库内的解析样例，
          金额、单位、申报日、accession 与来源链接都按后端返回原样展示。
        </p>
      </PanelCard>

      <PanelCard
        title="已申报事实"
        :info="facts.length ? `共 ${facts.length} 条` : '该观察日没有可用事实'"
        flush
      >
        <div v-if="!facts.length" class="facts-empty">
          <StatusPill tone="warn" text="无可用事实" />
          <StateBlock state="empty" :message="emptyMessage" />
        </div>
        <div v-else class="facts-scroll">
          <el-table :data="facts" size="small" stripe row-key="accessionNo">
            <el-table-column prop="concept" label="概念（concept）" min-width="180" fixed />
            <el-table-column label="金额" width="150">
              <template #default="{ row }">
                <span class="tabular">{{ row.value }} {{ row.unit }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="form" label="申报表" width="100" />
            <el-table-column prop="filed" label="申报日" width="120" class-name="tabular" />
            <el-table-column prop="accessionNo" label="accession" min-width="200" />
            <el-table-column label="来源" width="160">
              <template #default="{ row }">
                <a :href="row.sourceUrl" target="_blank" rel="noreferrer noopener">
                  {{ hostOf(row.sourceUrl) }}
                </a>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </PanelCard>

      <SourceFooter
        :as-of="asOf || '—'"
        :sources="['仓库内 SEC 解析样例', 'CIK ' + CIK]"
        note="样例数据用于验证解析链路，不等于 SEC 实时申报；观察日之后的事实不会出现"
      />
    </template>
  </main>
</template>

<style scoped>
.filters-form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
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

.notice {
  margin: 0 0 8px;
  font-size: 13px;
}

.facts-empty {
  display: grid;
  gap: 10px;
  padding: 16px;
}

.facts-scroll {
  overflow-x: auto;
}

.request-line {
  margin: 8px 0 0;
  font-size: 12px;
}

a {
  color: var(--accent);
  font-size: 12px;
}
</style>
