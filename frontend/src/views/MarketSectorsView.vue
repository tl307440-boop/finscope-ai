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

type Phase = "loading" | "ready" | "error"
type SortKey = "default" | "score-desc" | "score-asc" | "coverage"

interface SectorRow {
  name: string
  status: string
  label: string
  score: number | null
  coverage: string | null
  notice: string | null
  factorCode: string | null
  factorRaw: string | null
  factorWeight: string | null
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

/** 0..100 的强度分，只在后端确实给了分数时画。 */
function scoreBarWidth(row: SectorRow): string {
  if (row.score === null) {
    return "0%"
  }
  return `${Math.max(0, Math.min(100, row.score))}%`
}

function scoreText(row: SectorRow): string {
  if (row.score === null) {
    return "—"
  }
  return row.score.toFixed(2)
}

function statusClass(status: string, score: number | null): string {
  if (status === "insufficient" || score === null) {
    return "chip-warn"
  }
  if (score >= 60) {
    return "chip-rise"
  }
  if (score <= 40) {
    return "chip-fall"
  }
  return "chip-ok"
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
  const factors = Array.isArray(record.factors) ? record.factors : []
  const factor = asRecord(factors[0])
  return {
    name: asString(record.name) ?? "未命名板块",
    status: asString(record.status) ?? "unknown",
    label: asString(record.label) ?? "—",
    score: asNumber(record.score),
    coverage: asString(record.coverage),
    notice: asString(record.notice),
    factorCode: asString(factor?.code),
    factorRaw: asString(factor?.raw),
    factorWeight: asString(factor?.weight),
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
    <header class="page-head">
      <div>
        <h1>板块排行</h1>
        <p class="muted">
          强度分＝板块相对基准的超额收益（0–100，50 为中性）；样本不足时不给分，只说明原因。
        </p>
      </div>
      <div class="filter-bar">
        <el-date-picker
          v-model="asOfInput"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="as_of 观察日（可留空）"
          :clearable="true"
        />
        <el-button :loading="loading" @click="load">查询</el-button>
        <el-button text :loading="loading" @click="clearAsOf">用最新交易日</el-button>
      </div>
    </header>

    <p v-if="phase === 'ready'" class="meta-line muted">
      数据时间 {{ asOfText }} · 来源 {{ meta.source ?? "—" }} · 请求号 {{ requestId || "—" }}
    </p>

    <div v-if="demoMode" class="banner" role="status">
      当前返回的是{{ meta.dataMode }}数据，不是实时行情。
    </div>
    <div v-if="phase === 'ready' && meta.stale" class="banner" role="status">
      数据已过期（stale），展示的是上次成功计算的结果。
    </div>

    <section v-if="phase === 'loading'" class="skeleton" aria-busy="true">正在计算板块强度</section>

    <section v-else-if="phase === 'error'" class="panel" role="alert">
      <h1>无法加载板块排行</h1>
      <p>{{ errorMessage }}</p>
      <p v-if="errorCode" class="muted">错误码 {{ errorCode }} · 请求号 {{ requestId || "—" }}</p>
      <button class="retry" type="button" @click="load">重试</button>
    </section>

    <template v-else>
      <section class="panel">
        <div class="panel-head">
          <h2>概览</h2>
          <el-select v-model="sortKey" size="small" style="width: 180px">
            <el-option label="后端返回顺序" value="default" />
            <el-option label="强度从高到低" value="score-desc" />
            <el-option label="强度从低到高" value="score-asc" />
            <el-option label="有说明的排前面" value="coverage" />
          </el-select>
        </div>
        <div class="stat-grid">
          <div class="card">
            <p class="muted">板块数</p>
            <p class="state">{{ sectors.length }}</p>
          </div>
          <div class="card">
            <p class="muted">给出强度的板块</p>
            <p class="state up">{{ available }}</p>
          </div>
          <div class="card">
            <p class="muted">样本不足 / 无法判断</p>
            <p class="state stale">{{ undecided }}</p>
          </div>
          <div class="card">
            <p class="muted">数据口径</p>
            <p class="state">{{ meta.dataMode ?? "—" }}</p>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>板块明细</h2>
          <span class="muted">共 {{ sortedSectors.length }} 个板块</span>
        </div>

        <el-table
          v-if="sortedSectors.length"
          :data="sortedSectors"
          size="small"
          stripe
          max-height="620"
          row-key="name"
        >
          <el-table-column prop="name" label="板块" min-width="140" fixed />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <span class="chip" :class="statusClass(row.status, row.score)">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="强度分" width="200">
            <template #default="{ row }">
              <div class="score-cell">
                <span class="score-value">{{ scoreText(row) }}</span>
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
          <el-table-column label="因子" width="180">
            <template #default="{ row }">
              <span v-if="row.factorCode" class="muted">
                {{ row.factorCode }} {{ row.factorRaw ?? "—" }}（权重
                {{ row.factorWeight ?? "—" }}）
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>

        <template v-else>
          <el-empty description="暂无已入库的板块数据" />
          <p class="muted">行业分类入库后这里会列出各板块强度；同步任务完成前没有可展示的排行。</p>
        </template>
      </section>

      <section v-if="sectors.some((row) => row.notice)" class="panel">
        <h2>后端说明</h2>
        <ul class="notice-list">
          <li v-for="row in sectors.filter((item) => item.notice)" :key="row.name" class="muted">
            <strong>{{ row.name }}</strong>：{{ row.notice }}
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.page-head h1 {
  margin: 0 0 4px;
  font-size: 22px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.meta-line {
  margin: 0 0 16px;
  word-break: break-all;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.panel-head h2,
.panel > h2 {
  margin: 0 0 8px;
  font-size: 17px;
}

.stat-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
  margin-top: 12px;
}

.state {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.chip {
  display: inline-block;
  padding: 2px 10px;
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12px;
  color: var(--muted);
}

.chip-ok {
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 40%, transparent);
}

.chip-warn {
  color: var(--warn);
  border-color: color-mix(in srgb, var(--warn) 40%, transparent);
}

.chip-rise {
  color: var(--rise);
  border-color: color-mix(in srgb, var(--rise) 40%, transparent);
}

.chip-fall {
  color: var(--fall);
  border-color: color-mix(in srgb, var(--fall) 40%, transparent);
}

.score-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.score-value {
  min-width: 44px;
  font-variant-numeric: tabular-nums;
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
}

@media (min-width: 768px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 1280px) {
  .stat-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}
</style>
