<script setup lang="ts">
/**
 * 板块排行。
 *
 * 只用 GET /api/v1/market/sectors（可选 as_of）。后端返回的每个板块带
 * status / label / score / coverage / notice，样本不足时 score 为 null，
 * 这时如实展示「无法判断」与后端给的样本说明，不编造强度分。
 */
import { computed, onMounted, ref } from "vue"
import { DataAnalysis, Grid, InfoFilled, Trophy } from "@element-plus/icons-vue"
import { ApiError, apiUrl, errorFrom, readJson } from "../api/client"
import { asNumber, asRecord, asString, dataOf, formatMoment } from "../api/json"
import PanelCard from "../components/ui/PanelCard.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"

type Phase = "loading" | "ready" | "error"

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
  stale: boolean
  dataMode: string | null
}

const phase = ref<Phase>("loading")
const loading = ref(false)
const errorMessage = ref("")
const asOfInput = ref("")
const sectors = ref<SectorRow[]>([])
const meta = ref<MetaInfo>({ asOf: null, stale: false, dataMode: null })

const asOfText = computed(() => formatMoment(meta.value.asOf))
const demoMode = computed(() => meta.value.dataMode !== null && meta.value.dataMode !== "live")

/** 有分数的板块按强度从高到低排；没有分数的单独列出，不参与排名。 */
const ranked = computed<SectorRow[]>(() =>
  sectors.value.filter((row) => row.score !== null).sort((a, b) => (b.score ?? 0) - (a.score ?? 0)),
)
const unscored = computed<SectorRow[]>(() => sectors.value.filter((row) => row.score === null))
const leader = computed<SectorRow | null>(() => ranked.value[0] ?? null)

/** 样本不足板块的说明通常是同一句，去重后只说一次。 */
const unscoredReasons = computed<string[]>(() => [
  ...new Set(unscored.value.map((row) => row.coverage).filter((text): text is string => Boolean(text))),
])
const notices = computed<string[]>(() => [
  ...new Set(sectors.value.map((row) => row.notice).filter((text): text is string => Boolean(text))),
])

function clampScore(score: number | null): number {
  return score === null ? 0 : Math.max(0, Math.min(100, score))
}

/** 颜色跟随后端给的强弱结论：偏强用涨色，偏弱用跌色（A 股红强绿弱），中性不染色。 */
function direction(label: string): "strong" | "weak" | "neutral" {
  if (label.includes("强")) {
    return "strong"
  }
  if (label.includes("弱")) {
    return "weak"
  }
  return "neutral"
}

function levelClass(label: string): string {
  return `lvl-${direction(label)}`
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error
  }
  return new ApiError("无法连接行情服务", "NETWORK_ERROR", null)
}

function readMeta(body: unknown): MetaInfo {
  const record = asRecord(asRecord(body)?.meta)
  return {
    asOf: asString(record?.as_of),
    stale: record?.stale === true,
    dataMode: asString(record?.data_mode),
  }
}

function toSectorRow(record: Record<string, unknown>): SectorRow {
  return {
    name: asString(record.name) ?? "未命名板块",
    status: asString(record.status) ?? "unknown",
    label: asString(record.label) ?? "无法判断",
    score: asNumber(record.score),
    coverage: asString(record.coverage),
    notice: asString(record.notice),
  }
}

async function load(): Promise<void> {
  loading.value = true
  phase.value = "loading"
  errorMessage.value = ""
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
    const raw = Array.isArray(data?.sectors) ? data.sectors : []
    sectors.value = raw.flatMap((item) => {
      const record = asRecord(item)
      return record ? [toSectorRow(record)] : []
    })
    phase.value = "ready"
  } catch (error) {
    errorMessage.value = toApiError(error).message
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
    <PageHeader title="板块排行" subtitle="申万一级行业相对基准的强弱分 · 0–100，50 为中性">
      <template #actions>
        <el-date-picker
          v-model="asOfInput"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="观察日（默认最新）"
          :clearable="true"
          style="width: 180px"
          @change="load"
        />
        <el-button v-if="asOfInput" text :disabled="loading" @click="clearAsOf">回到最新</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <StateBlock
      v-if="demoMode"
      state="stale"
      :message="'当前展示的是演示数据，不代表真实行情'"
      @retry="load"
    />
    <StateBlock
      v-if="phase === 'ready' && meta.stale"
      state="stale"
      message="板块数据可能已过期，展示的是最近一次成功计算的结果"
      :as-of="asOfText"
      @retry="load"
    />

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="6" />

    <StateBlock v-else-if="phase === 'error'" state="error" :message="errorMessage || '板块排行加载失败'" @retry="load" />

    <StateBlock
      v-else-if="!sectors.length"
      state="empty"
      message="暂无行业分类数据，需管理员在数据中心导入行业成分后才能计算排行"
    />

    <template v-else>
      <div class="grid-4">
        <StatCard title="覆盖板块" :value="sectors.length" unit="个" caption="申万一级行业" tone="accent">
          <template #icon><el-icon><Grid /></el-icon></template>
        </StatCard>
        <StatCard title="已评分" :value="ranked.length" unit="个" caption="成分股足够，可计算强弱" tone="cyan">
          <template #icon><el-icon><DataAnalysis /></el-icon></template>
        </StatCard>
        <StatCard title="样本不足" :value="unscored.length" unit="个" caption="成分股不够，暂不评分" tone="warn">
          <template #icon><el-icon><InfoFilled /></el-icon></template>
        </StatCard>
        <StatCard
          title="强度第一"
          :value="leader ? leader.name : null"
          :caption="leader ? `强度 ${leader.score?.toFixed(1)} · ${leader.label}` : '暂无可评分板块'"
          tone="orange"
        >
          <template #icon><el-icon><Trophy /></el-icon></template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <PanelCard title="板块强度排行" info="强度分衡量板块相对基准的超额表现，50 为中性；只列出成分股足够的板块">
          <StateBlock
            v-if="!ranked.length"
            state="empty"
            message="所有板块的成分股样本都不足，暂不给出排行"
          />
          <template v-else>
            <ol class="rank-list">
              <li v-for="(row, index) in ranked" :key="row.name" class="rank-row">
                <span class="rank-no" :class="`rank-${Math.min(index + 1, 4)}`">{{ index + 1 }}</span>
                <span class="rank-name">{{ row.name }}</span>
                <span
                  class="bar"
                  role="img"
                  :aria-label="`${row.name} 强度 ${row.score?.toFixed(1)}`"
                >
                  <span class="bar-fill" :class="`dir-${direction(row.label)}`" :style="{ width: `${clampScore(row.score)}%` }" />
                  <span class="bar-mid" aria-hidden="true" />
                </span>
                <span class="rank-score tabular" :class="`dir-${direction(row.label)}`">{{ row.score?.toFixed(1) }}</span>
                <span class="level" :class="levelClass(row.label)">{{ row.label }}</span>
                <span class="rank-coverage">{{ row.coverage ?? "" }}</span>
              </li>
            </ol>
            <div class="rank-row scale-row" aria-hidden="true">
              <span />
              <span />
              <span class="scale">
                <span>0</span>
                <span class="scale-mid">50 中性</span>
                <span>100</span>
              </span>
            </div>
          </template>
        </PanelCard>

        <div class="stack">
          <PanelCard title="样本不足的板块" :info="`共 ${unscored.length} 个，补齐成分股日线后会自动参与排行`">
            <p v-if="!unscored.length" class="muted">所有板块都已评分。</p>
            <template v-else>
              <p class="reason">{{ unscoredReasons.join("；") || "成分股样本不足，暂不评分" }}</p>
              <ul class="chip-list">
                <li v-for="row in unscored" :key="row.name">{{ row.name }}</li>
              </ul>
            </template>
          </PanelCard>

          <PanelCard title="如何解读">
            <ul class="legend-list">
              <li><span class="level lvl-strong">偏强</span><span>强度分 ≥ 60，跑赢基准较多</span></li>
              <li><span class="level lvl-neutral">中性</span><span>40–60，与基准接近</span></li>
              <li><span class="level lvl-weak">偏弱</span><span>≤ 40，跑输基准</span></li>
            </ul>
            <p v-for="text in notices" :key="text" class="notice">{{ text }}</p>
          </PanelCard>
        </div>
      </div>

      <SourceFooter
        :as-of="asOfInput ? `${asOfInput}（观察日）` : asOfText"
        :sources="['申万一级行业成分日线（已入库）']"
        :stale="meta.stale"
        note="样本不足的板块不给强度分"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
  align-items: start;
}

.rank-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 4px;
}

.rank-row {
  display: grid;
  grid-template-columns: 28px 92px minmax(160px, 1fr) 52px 52px 80px;
  align-items: center;
  gap: 12px;
  padding: 16px 8px;
  border-radius: var(--radius-sm);
  font-size: 14px;
}

.rank-row:hover {
  background: rgb(96 140 220 / 6%);
}

.rank-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  font-family: var(--font-num);
  font-size: 13px;
  font-weight: 600;
  background: var(--panel-soft);
  color: var(--muted);
}

.rank-1 {
  background: rgb(246 70 93 / 18%);
  color: var(--rise);
}

.rank-2 {
  background: rgb(251 146 60 / 18%);
  color: var(--orange);
}

.rank-3 {
  background: var(--accent-soft);
  color: var(--accent);
}

.rank-name {
  font-weight: 600;
  color: var(--text-strong);
}

.bar {
  position: relative;
  display: block;
  height: 10px;
  border-radius: 999px;
  background: rgb(96 140 220 / 10%);
  overflow: hidden;
}

.bar-fill {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.bar-fill.dir-strong {
  background: linear-gradient(90deg, rgb(246 70 93 / 35%), var(--rise));
}

.bar-fill.dir-weak {
  background: linear-gradient(90deg, rgb(31 195 125 / 35%), var(--fall));
}

.bar-fill.dir-neutral {
  background: var(--accent);
}

.bar-mid {
  position: absolute;
  top: -2px;
  bottom: -2px;
  left: 50%;
  width: 1px;
  background: var(--muted);
  opacity: 0.6;
}

.rank-score {
  text-align: right;
  font-size: 18px;
  font-weight: 600;
}

.rank-score.dir-strong {
  color: var(--rise);
}

.rank-score.dir-weak {
  color: var(--fall);
}

.rank-coverage {
  font-size: 13px;
  color: var(--muted);
  white-space: nowrap;
}

.level {
  display: inline-flex;
  justify-content: center;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.lvl-strong {
  background: rgb(246 70 93 / 14%);
  color: var(--rise);
}

.lvl-weak {
  background: rgb(31 195 125 / 14%);
  color: var(--fall);
}

.lvl-neutral {
  background: rgb(149 166 190 / 14%);
  color: var(--text);
}

.scale-row {
  margin-top: 6px;
  padding-top: 8px;
  padding-bottom: 0;
  border-top: 1px solid var(--line-soft);
  border-radius: 0;
}

.scale-row:hover {
  background: none;
}

.scale {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
}

.scale {
  position: relative;
}

.scale-mid {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  color: var(--text);
}

.reason {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--muted);
}

.chip-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip-list li {
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px dashed var(--line);
  background: rgb(96 140 220 / 4%);
  color: var(--text);
  font-size: 13px;
}

.legend-list {
  margin: 0 0 12px;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 8px;
}

.legend-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--muted);
}

.notice {
  margin: 0;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
  font-size: 13px;
  line-height: 1.6;
  color: var(--muted);
}
</style>
