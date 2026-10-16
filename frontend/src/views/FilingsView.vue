<script setup lang="ts">
/**
 * SEC 申报事实（`GET /api/v1/sec/facts?cik=320193&as_of=...`）。
 *
 * 固定 CIK 0000320193（苹果）。逐条列出观察日之前已申报的 XBRL 事实：
 * 指标、数值、期间口径、申报表、申报日、申报号与来源链接；当天没有已申报事实时，
 * 直接说明「该日期还看不到已申报的事实」，不换别的日期凑数据。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asRecord, asString, dataOf, listOf } from "../api/json"
import {
  conceptLabel,
  conceptShortName,
  formatFact,
  parseFactPoint,
  periodKindLabel,
  todayText,
  type FactPoint,
} from "../api/secFacts"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"

const CIK = "0000320193"

const asOf = ref(todayText())
const phase = ref<"idle" | "loading" | "ready" | "error">("idle")
const failure = ref("")
const requestId = ref("")
const facts = ref<FactPoint[]>([])

const rows = computed(() =>
  [...facts.value]
    .sort((left, right) => right.periodEnd.localeCompare(left.periodEnd) || left.concept.localeCompare(right.concept))
    .map((point, index) => ({
      key: `${point.concept}-${point.periodStart}-${point.periodEnd}-${index}`,
      label: conceptLabel(point.concept),
      concept: conceptShortName(point.concept),
      value: formatFact(point.value, point.unit),
      kind: periodKindLabel(point),
      periodEnd: point.periodEnd,
      form: point.form,
      filed: point.filed,
      accessionNo: point.accessionNo,
      sourceUrl: point.sourceUrl,
    })),
)

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
    failure.value = "请选择观察日"
    facts.value = []
    return
  }
  phase.value = "loading"
  failure.value = ""
  requestId.value = ""
  facts.value = []
  try {
    const response = await fetch(apiUrl(`/api/v1/sec/facts?cik=320193&as_of=${encodeURIComponent(asOf.value)}`), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    requestId.value = asString(asRecord(asRecord(body)?.meta)?.request_id) ?? ""
    facts.value = listOf(dataOf(body)?.facts).flatMap(parseFactPoint)
    phase.value = "ready"
  } catch (caught) {
    failure.value = caught instanceof ApiError ? caught.message : "无法连接服务器"
    phase.value = "error"
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader title="SEC 申报事实" :subtitle="`苹果 AAPL · CIK ${CIK} · 观察日之前已申报的 XBRL 科目`">
      <template #filters>
        <form class="filters-form" @submit.prevent="load">
          <label class="field">
            观察日
            <input v-model="asOf" name="sec-as-of" type="date" />
          </label>
          <button class="retry" type="submit" :disabled="phase === 'loading'">查询</button>
        </form>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading' || phase === 'idle'" state="loading" :rows="3" />

    <template v-else-if="phase === 'error'">
      <StateBlock state="error" :message="failure" @retry="load" />
      <p v-if="requestId" class="muted request-line">请求号 {{ requestId }}</p>
    </template>

    <PanelCard v-else title="已申报事实" :info="facts.length ? `共 ${facts.length} 条` : '该观察日没有可用事实'" flush>
      <div v-if="!facts.length" class="facts-empty">
        <StateBlock state="empty" title="该日期还看不到已申报的事实" message="可以把观察日调晚后再查询。" />
      </div>
      <div v-else class="facts-scroll">
        <el-table :data="rows" size="small" stripe row-key="key">
          <el-table-column label="指标" min-width="170" fixed>
            <template #default="{ row }">
              <div class="concept-cell">
                <span>{{ row.label }}</span>
                <small v-if="row.label !== row.concept">{{ row.concept }}</small>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="数值" width="150" align="right">
            <template #default="{ row }"><span class="tabular">{{ row.value }}</span></template>
          </el-table-column>
          <el-table-column prop="kind" label="口径" width="110" />
          <el-table-column prop="periodEnd" label="期末" width="110" class-name="tabular" />
          <el-table-column prop="form" label="申报表" width="90" />
          <el-table-column prop="filed" label="申报日" width="110" class-name="tabular" />
          <el-table-column prop="accessionNo" label="申报号" min-width="190" class-name="tabular" />
          <el-table-column label="来源" width="150">
            <template #default="{ row }">
              <a :href="row.sourceUrl" target="_blank" rel="noreferrer noopener">{{ hostOf(row.sourceUrl) }}</a>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </PanelCard>

    <SourceFooter :as-of="asOf || '—'" :sources="['SEC EDGAR']" note="按申报日入库，观察日之后的申报不会出现" />
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
  font-size: 13px;
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

.retry {
  appearance: none;
  border: 1px solid var(--accent);
  border-radius: 6px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 13px;
  padding: 6px 14px;
  cursor: pointer;
}

.facts-empty {
  padding: 0 16px 10px;
}

.facts-scroll {
  overflow-x: auto;
}

.concept-cell {
  display: grid;
  line-height: 1.4;
}

.concept-cell small {
  color: var(--faint);
  font-size: 12px;
}

.request-line {
  margin: 8px 0 0;
}

a {
  color: var(--accent);
  font-size: 13px;
  text-decoration: none;
}
</style>
