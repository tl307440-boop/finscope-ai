<script setup lang="ts">
/**
 * 板块排行。
 *
 * 只用 GET /api/v1/market/sectors（可选 as_of）。后端返回的每个板块带
 * status / label / score / coverage / notice，样本不足时 score 为 null，
 * 这时如实展示「无法判断」与后端给的样本说明，不编造强度分。
 */
import { computed, onMounted, ref } from "vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, formatMoment } from "../api/json"
import MetricTile from "../components/ui/MetricTile.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"
type SortKey = "default" | "score-desc" | "score-asc" | "coverage"

interface SectorRow {
  name: string
  status: string
  label: string
  score: number | null
  coverage: string | null
  notice: string | null
}

interface MetaInfo {
  asOf: string | null
  source: string | null
  stale: boolean
  dataMode: string | null
  requestId: string | null
}

const phase = ref<Phase>("loading")
const loading = ref(false)
const errorMessage = ref("")
const errorCode = ref("")
const requestId = ref("")
const asOfInput = ref("")
const sortKey = ref<SortKey>("default")
const sectors = ref<SectorRow[]>([])
const meta = ref<MetaInfo>({
  asOf: null,
  source: null,
  stale: false,
  dataMode: null,
  requestId: null,
})

const asOfText = computed(() => formatMoment(meta.value.asOf))
const available = computed(() => sectors.value.filter((row) => row.score !== null).length)
const undecided = computed(() => sectors.value.length - available.value)
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")

const sortedSectors = computed<SectorRow[]>(() => {
  const rows = [...sectors.value]
  if (sortKey.value === "score-desc") {
    rows.sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
  } else if (sortKey.value === "score-asc") {
    rows.sort((a, b) => (a.score ?? 101) - (b.score ?? 101))
  } else if (sortKey.value === "coverage") {
    rows.sort((a, b) => (a.coverage ? 1 : 0) - (b.coverage ? 1 : 0))
  }
  return rows
})

/** notice 与 coverage 重复的行不再重复展示同一句话。 */
const noticeRows = computed(() =>
  sectors.value.filter((row) => row.notice && row.notice !== row.coverage),
)

/** 0..100 的强度分，只在后端确实给了分数时画。 */
function scoreBarWidth(row: SectorRow): string {
  if (row.score === null) {
    return "0%"
  }
  return `${Math.max(0, Math.min(100, row.score))}%`
}

function scoreText(row: SectorRow): string {
  if (row.score === null) {
    return "无法判断"
  }
  return row.score.toFixed(2)
}

/** StatusPill 只有 accent/ok/warn/muted/danger 五种语气。 */
function statusTone(status: string, score: number | null): "accent" | "ok" | "warn" | "muted" | "danger" {
  if (status === "insufficient" || score === null) {
    return "warn"
  }
  if (score >= 60) {
    return "danger"
  }
  if (score <= 40) {
    return "ok"
  }
  return "accent"
}

/** 表格里显示的状态文案直接用后端的 label。 */
function statusText(row: SectorRow): string {
  return row.label || row.status || "—"
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError("无法连接接口", "NETWORK_ERROR", null)
}

function readMeta(body: unknown): MetaInfo {
  const record = asRecord(asRecord(body)?.meta)
  const stale = record?.stale
  return {
    asOf: asString(record?.as_of),
    source: asString(record?.source),
    stale: typeof stale === "boolean" ? stale : false,
    dataMode: asString(record?.data_mode),
    requestId: asString(record?.request_id),
  }
}

function toSectorRow(record: Record<string, unknown>): SectorRow {
  return {
    name: asString(record.name) ?? "未命名板块",
    status: asString(record.status) ?? "unknown",
    label: asString(record.label) ?? "—",
    score: asNumber(record.score),
    coverage: asString(record.coverage),
    notice: asString(record.notice),
  }
}

async function load(): Promise<void> {
  loading.value = true
  phase.value = "loading"
  errorMessage.value = ""
  errorCode.value = ""
  try {
    const query = asOfInput.value ? `?as_of=${encodeURIComponent(asOfInput.value)}` : ""
    const response = await fetch(apiUrl(`/api/v1/market/sectors${query}`), {
      headers: { Accept: "application/json" },
      credentials: "include",
    })
    const body = await readJson(response)
    if (!response.ok) {
      throw errorFrom(body, response.headers.get("X-Request-ID"))
    }
    const data = dataOf(body)
    meta.value = readMeta(body)
    requestId.value = meta.value.requestId ?? ""
    const raw = Array.isArray(data?.sectors) ? data.sectors : []
    sectors.value = raw.flatMap((item) => {
      const record = asRecord(item)
      return record ? [toSectorRow(record)] : []
    })
    phase.value = "ready"
  } catch (error) {
    const apiError = toApiError(error)
    errorMessage.value = apiError.message
    errorCode.value = apiError.code
    requestId.value = requestId.value || (apiError.requestId ?? "")
    phase.value = "error"
  } finally {
    loading.value = false
  }
}

function clearAsOf(): void {
  asOfInput.value = ""
  void load()
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="板块排行"
      subtitle="强度分＝板块相对基准的超额收益（0–100，50 为中性）；样本不足时不给分，只说明原因"
    >
      <template #filters>
        <div class="filters-form">
          <el-date-picker
            v-model="asOfInput"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="as_of 观察日（可留空）"
            :clearable="true"
          />
          <button class="retry" type="button" :disabled="loading" @click="load">查询</button>
          <button class="retry" type="button" :disabled="loading" @click="clearAsOf">用最新交易日</button>
        </div>
      </template>
    </PageHeader>

    <p v-if="demoMode" class="banner" role="status">
      当前返回的是{{ meta.dataMode }}数据，不是实时行情。
    </p>
    <p v-if="phase === 'ready' && meta.stale" class="banner" role="status">
      数据已过期（stale），展示的是上次成功计算的结果。
    </p>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="4" />

    <template v-else-if="phase === 'error'">
      <StateBlock state="error" :message="errorMessage || '无法加载板块排行'" @retry="load" />
      <p v-if="errorCode || requestId" class="muted request-line">
        错误码 {{ errorCode || "—" }} · 请求号 {{ requestId || "—" }}
      </p>
    </template>

    <template v-else>
      <div class="grid-4">
        <MetricTile
          label="板块数"
          :value="String(sectors.length)"
          hint="后端本次返回的板块条目"
        />
        <MetricTile
          label="给出强度的板块"
          :value="String(available)"
          hint="score 非 null 才计入"
        />
        <MetricTile
          label="样本不足 / 无法判断"
          :value="String(undecided)"
          hint="score 为 null，原因是后端给的 coverage / notice"
        />
        <MetricTile label="数据口径" :value="meta.dataMode ?? '—'" :hint="`数据时间 ${asOfText}`" />
      </div>

      <PanelCard
        title="板块明细"
        :info="`共 ${sortedSectors.length} 个板块 · 排序由前端切换，不改变后端返回的数据`"
        flush
      >
        <div v-if="sortedSectors.length" class="table-actions">
          <el-select v-model="sortKey" size="small" style="width: 180px" name="sector-sort">
            <el-option label="后端返回顺序" value="default" />
            <el-option label="强度从高到低" value="score-desc" />
            <el-option label="强度从低到高" value="score-asc" />
            <el-option label="有说明的排前面" value="coverage" />
          </el-select>
        </div>
        <div v-if="sortedSectors.length" class="table-scroll">
          <el-table :data="sortedSectors" size="small" stripe max-height="620" row-key="name">
            <el-table-column prop="name" label="板块" min-width="140" fixed />
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <StatusPill :text="statusText(row)" :tone="statusTone(row.status, row.score)" />
              </template>
            </el-table-column>
            <el-table-column label="强度分" width="200">
              <template #default="{ row }">
                <div class="score-cell">
                  <span class="score-value tabular">{{ scoreText(row) }}</span>
                  <span class="score-track" role="img" :aria-label="`强度 ${scoreText(row)}`">
                    <span class="score-fill" :style="{ width: scoreBarWidth(row) }" />
                  </span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="覆盖度 / 样本说明" min-width="260">
              <template #default="{ row }">
                <span :class="row.coverage ? '' : 'muted'">{{ row.coverage ?? "—" }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <div v-else class="empty-wrap">
          <StateBlock
            state="empty"
            message="后端这次没有返回任何板块条目；行业分类入库后这里才会出现排行。"
          />
        </div>
      </PanelCard>

      <PanelCard v-if="noticeRows.length" title="后端说明" info="notice 与 coverage 不重复展示">
        <ul class="notice-list">
          <li v-for="row in noticeRows" :key="row.name" class="muted">
            <strong>{{ row.name }}</strong>：{{ row.notice }}
          </li>
        </ul>
      </PanelCard>

      <SourceFooter
        :as-of="asOfText"
        :sources="[meta.source ?? '未标注来源', 'GET /api/v1/market/sectors']"
        :stale="meta.stale"
        :note="`请求号 ${requestId || '—'} · 样本不足的板块不给强度分，也不使用占位数字`"
      />
    </template>
  </main>
</template>

<style scoped>
.filters-form {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.banner {
  margin: 0 0 12px;
  padding: 8px 12px;
  border: 1px solid color-mix(in srgb, var(--warn) 40%, transparent);
  border-radius: var(--radius-sm);
  color: var(--warn);
  font-size: 13px;
}

.request-line {
  margin: 8px 0 0;
  font-size: 12px;
}

.table-actions {
  display: flex;
  justify-content: flex-end;
  padding: 12px 16px 0;
}

.table-scroll {
  overflow-x: auto;
}

.empty-wrap {
  padding: 16px;
}

.score-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-value {
  min-width: 62px;
}

.score-track {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: var(--line);
  overflow: hidden;
}

.score-fill {
  display: block;
  height: 100%;
  background: var(--accent);
}

.notice-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
}
</style>
