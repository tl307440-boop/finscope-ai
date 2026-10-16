<script setup lang="ts">
/**
 * 产业研究 · 行业总览（/industry）。
 *
 * 数据来源（只读）：
 *  - GET /api/v1/industries?q=                  全部已入库行业（申万一级 12 个 + 目录分类）
 *  - GET /api/v1/market/sectors?as_of=          各行业成分股相对沪深300的强弱分数；样本不足时无分数
 *  - GET /api/v1/industries/{id}/facts?as_of=   成分公司已入库的财报事实，这里只取条数与申报日
 *
 * 研究假设的读写放在行业详情页（/industry/:id），这里只做横向对比，不编造景气度或关注度。
 */
import { computed, onMounted, ref } from "vue"
import { DataAnalysis, Document, Grid, Trophy } from "@element-plus/icons-vue"
import { apiUrl, readJson } from "../api/client"
import { asList, asRecord, asString, dataOf, listOf } from "../api/json"
import {
  STRENGTH_RULE,
  industryFamily,
  parseSectors,
  strengthCaption,
  strengthTone,
  type SectorStrength,
} from "../api/industry"
import MiniRing from "../components/ui/MiniRing.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"

type Phase = "loading" | "ready" | "error"
type SortKey = "score" | "facts" | "name"

interface IndustryRow {
  id: string
  code: string
  name: string
  family: string
  factCount: number | null
  filed: string[]
  strength: SectorStrength | null
}

const asOf = ref(new Date().toISOString().slice(0, 10))
const phase = ref<Phase>("loading")
const failure = ref("")
const sectorsFailed = ref(false)
const industries = ref<IndustryRow[]>([])
const sectorNotice = ref("")
const factsPending = ref(false)

const filter = ref("all")
const sortKey = ref<SortKey>("score")
const view = ref<"table" | "cards">("table")

const SORTS: { key: SortKey; label: string }[] = [
  { key: "score", label: "按强弱分数" },
  { key: "facts", label: "按已入库事实" },
  { key: "name", label: "按行业名称" },
]

const swCount = computed(() => industries.value.filter((row) => row.code.startsWith("sw-")).length)
const scoredRows = computed(() =>
  industries.value
    .filter((row) => row.strength?.scored)
    .sort((left, right) => (right.strength?.score ?? 0) - (left.strength?.score ?? 0)),
)
const leader = computed<IndustryRow | null>(() => scoredRows.value[0] ?? null)
const unscoredNames = computed(() =>
  industries.value.filter((row) => !row.strength?.scored).map((row) => row.name),
)

const tabs = computed(() => [
  { key: "all", label: `全部行业 ${industries.value.length}` },
  { key: "sw", label: `申万一级 ${swCount.value}` },
  { key: "scored", label: `可判断强弱 ${scoredRows.value.length}` },
])

/** 只给可判断强弱的行业编排名；样本不足的不参与排名，避免看起来像倒数。 */
const strengthRank = computed(() => {
  const ranks = new Map<string, number>()
  scoredRows.value.forEach((row, index) => ranks.set(row.id, index + 1))
  return ranks
})

function compareByName(left: IndustryRow, right: IndustryRow): number {
  return left.name.localeCompare(right.name, "zh-Hans-CN")
}

function compareByScore(left: IndustryRow, right: IndustryRow): number {
  const a = left.strength?.score ?? null
  const b = right.strength?.score ?? null
  if (a !== null && b !== null) {
    return b - a
  }
  if (a !== null || b !== null) {
    return a !== null ? -1 : 1
  }
  return (right.factCount ?? 0) - (left.factCount ?? 0) || compareByName(left, right)
}

const visibleRows = computed<IndustryRow[]>(() => {
  const rows = industries.value.filter((row) => {
    if (filter.value === "sw") {
      return row.code.startsWith("sw-")
    }
    if (filter.value === "scored") {
      return Boolean(row.strength?.scored)
    }
    return true
  })
  if (sortKey.value === "name") {
    return [...rows].sort(compareByName)
  }
  if (sortKey.value === "facts") {
    return [...rows].sort(
      (left, right) => (right.factCount ?? 0) - (left.factCount ?? 0) || compareByScore(left, right),
    )
  }
  return [...rows].sort(compareByScore)
})

const totalFacts = computed(() => industries.value.reduce((sum, row) => sum + (row.factCount ?? 0), 0))
const industriesWithFacts = computed(() => industries.value.filter((row) => (row.factCount ?? 0) > 0).length)
const lastFiled = computed(() => {
  const all = industries.value.flatMap((row) => row.filed).sort()
  return all[all.length - 1] ?? ""
})

/** 全部行业的事实按申报月累计，只累加真实条数。 */
const cumulativeFacts = computed<number[]>(() => {
  const buckets = new Map<string, number>()
  for (const day of industries.value.flatMap((row) => row.filed)) {
    const key = day.slice(0, 7)
    buckets.set(key, (buckets.get(key) ?? 0) + 1)
  }
  let running = 0
  return [...buckets.entries()]
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([, count]) => (running += count))
})

const familySegments = computed(() => [
  { label: "申万一级", ratio: swCount.value, tone: "accent" as const },
  { label: "目录分类", ratio: industries.value.length - swCount.value, tone: "muted" as const },
])

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") {
    return fallback
  }
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

function toneColor(row: IndustryRow): string {
  const tone = strengthTone(row.strength)
  if (tone === "up") {
    return cssVar("--rise", "#f6465d")
  }
  if (tone === "down") {
    return cssVar("--fall", "#1fc37d")
  }
  return cssVar("--accent", "#3b82f6")
}

const scoreChartHeight = computed(() => `${Math.max(150, scoredRows.value.length * 40 + 56)}px`)

const scoreOption = computed(() => {
  const rows = scoredRows.value
  const muted = cssVar("--muted", "#94a4be")
  return {
    grid: { left: 76, right: 44, top: 28, bottom: 24 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      valueFormatter: (value: number) => `${value} 分`,
    },
    xAxis: { type: "value", min: 0, max: 100, splitNumber: 5 },
    yAxis: {
      type: "category",
      inverse: true,
      data: rows.map((row) => row.name),
      axisLabel: { fontSize: 12, color: cssVar("--text", "#e6edf8") },
    },
    series: [
      {
        name: "强弱分数",
        type: "bar",
        barWidth: 14,
        data: rows.map((row) => ({
          value: row.strength?.score ?? null,
          itemStyle: { color: toneColor(row), borderRadius: [0, 4, 4, 0] },
        })),
        label: { show: true, position: "right", fontSize: 12, color: cssVar("--text", "#e6edf8") },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: muted, opacity: 0.6 },
          label: { color: muted, fontSize: 11, position: "start", formatter: "{b}" },
          data: [
            { xAxis: 40, name: "偏弱 40" },
            { xAxis: 60, name: "偏强 60" },
          ],
        },
      },
    ],
  }
})

function verdictClass(row: IndustryRow): string {
  return `verdict tone-${strengthTone(row.strength)}`
}

function serverMessage(body: unknown): string {
  return asString(asRecord(asRecord(body)?.error)?.message) ?? "暂时无法读取行业目录"
}

async function loadFacts(row: IndustryRow): Promise<void> {
  const params = new URLSearchParams({ as_of: asOf.value })
  try {
    const response = await fetch(
      apiUrl(`/api/v1/industries/${encodeURIComponent(row.id)}/facts?${params.toString()}`),
    )
    if (!response.ok) {
      row.factCount = null
      return
    }
    const facts = listOf(dataOf(await readJson(response))?.facts)
    row.factCount = facts.length
    row.filed = facts.flatMap((fact) => {
      const day = asString(fact.filed)
      return day ? [day] : []
    })
  } catch {
    row.factCount = null
  }
}

async function loadSectors(): Promise<SectorStrength[]> {
  try {
    const response = await fetch(
      apiUrl(`/api/v1/market/sectors?${new URLSearchParams({ as_of: asOf.value }).toString()}`),
    )
    if (!response.ok) {
      sectorsFailed.value = true
      return []
    }
    sectorsFailed.value = false
    const rows = parseSectors(await readJson(response))
    sectorNotice.value = rows.find((row) => row.notice)?.notice ?? ""
    return rows
  } catch {
    sectorsFailed.value = true
    return []
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    const [response, sectors] = await Promise.all([fetch(apiUrl("/api/v1/industries?q=")), loadSectors()])
    const body = await readJson(response)
    if (!response.ok) {
      failure.value = serverMessage(body)
      phase.value = "error"
      return
    }
    const rows = asList(asRecord(asRecord(body)?.data)?.industries).flatMap((item): IndustryRow[] => {
      const row = asRecord(item)
      const id = asString(row?.id)
      const name = asString(row?.name)
      if (!id || !name) {
        return []
      }
      const code = asString(row?.code) ?? ""
      return [
        {
          id,
          code,
          name,
          family: industryFamily(code),
          factCount: null,
          filed: [],
          strength: sectors.find((sector) => sector.name === name) ?? null,
        },
      ]
    })
    if (!rows.length) {
      failure.value = "行业目录还是空的，需管理员先初始化行业分类"
      phase.value = "error"
      return
    }
    industries.value = rows
    phase.value = "ready"
    factsPending.value = true
    await Promise.all(industries.value.map((row) => loadFacts(row)))
    factsPending.value = false
  } catch {
    failure.value = "网络连接失败，请稍后重试"
    phase.value = "error"
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="产业研究"
      subtitle="申万一级行业（2021 版）与目录分类，按成分股相对强弱与已入库财报事实横向对比"
    >
      <template #actions>
        <el-date-picker
          v-model="asOf"
          type="date"
          value-format="YYYY-MM-DD"
          :clearable="false"
          class="asof-picker"
          @change="load"
        />
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
      </template>
      <template v-if="phase === 'ready'" #tabs>
        <SectionTabs v-model="filter" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading'" state="loading" :rows="5" />
    <StateBlock v-else-if="phase === 'error'" state="error" :message="failure" @retry="load" />

    <template v-else>
      <div class="grid-4 section">
        <StatCard
          title="覆盖行业"
          :value="industries.length"
          unit="个"
          :caption="`申万一级 ${swCount} · 目录分类 ${industries.length - swCount}`"
          info="申万一级按 2021 版分类，成分为人工整理的代表性 A 股；目录分类只用于归档，不代表涨跌"
          tone="accent"
          more-text=""
        >
          <template #icon><Grid /></template>
          <template #chart>
            <MiniRing :segments="familySegments" :size="58" :thickness="8" />
          </template>
        </StatCard>
        <StatCard
          title="可判断强弱"
          :value="scoredRows.length"
          unit="个"
          :caption="
            sectorsFailed
              ? '强弱数据暂时读取失败'
              : `其余 ${unscoredNames.length} 个样本不足 3 只`
          "
          :info="STRENGTH_RULE"
          tone="cyan"
          more-text=""
        >
          <template #icon><DataAnalysis /></template>
          <template #chart>
            <MiniSpark
              :points="scoredRows.map((row) => row.strength?.score ?? null)"
              :baseline="0"
              kind="bar"
              :width="104"
              :height="38"
            />
          </template>
        </StatCard>
        <StatCard
          title="相对最强"
          :value="leader?.name ?? null"
          :caption="
            leader
              ? `${leader.strength?.score} 分 · ${strengthCaption(leader.strength)}`
              : '暂无可判断强弱的行业'
          "
          info="可判断强弱的行业里分数最高者；分数反映区间相对表现，不是收益承诺"
          tone="orange"
          more-text=""
        >
          <template #icon><Trophy /></template>
        </StatCard>
        <StatCard
          title="已入库财报事实"
          :value="totalFacts"
          unit="条"
          :caption="
            factsPending
              ? '正在统计各行业的事实条数'
              : totalFacts
              ? `覆盖 ${industriesWithFacts} 个行业 · 最近申报 ${lastFiled}`
              : '成分公司的财报事实尚未入库'
          "
          info="成分公司已入库的财报数字（目前来自美股 SEC 年报与季报），可在行业详情里逐条核对"
          tone="violet"
          more-text=""
        >
          <template #icon><Document /></template>
          <template #chart>
            <MiniSpark :points="cumulativeFacts" kind="area" :width="104" :height="38" />
          </template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <PanelCard title="行业强弱排行" :info="STRENGTH_RULE" flush>
          <template #actions>
            <el-select v-model="sortKey" size="small" class="sort-select">
              <el-option v-for="item in SORTS" :key="item.key" :label="item.label" :value="item.key" />
            </el-select>
            <el-radio-group v-model="view" size="small">
              <el-radio-button value="table">列表</el-radio-button>
              <el-radio-button value="cards">卡片</el-radio-button>
            </el-radio-group>
          </template>

          <StateBlock
            v-if="!visibleRows.length"
            state="empty"
            message="当前筛选下没有行业，切换到「全部行业」查看"
          />

          <el-table v-else-if="view === 'table'" :data="visibleRows" class="rank-table">
            <el-table-column label="排名" width="66" align="center">
              <template #default="{ row }">
                <span
                  v-if="strengthRank.get(row.id)"
                  class="rank tabular"
                  :class="{ top: (strengthRank.get(row.id) ?? 9) <= 3 }"
                >
                  {{ strengthRank.get(row.id) }}
                </span>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="行业" min-width="150">
              <template #default="{ row }">
                <RouterLink class="name-link" :to="`/industry/${row.id}`">{{ row.name }}</RouterLink>
                <span class="family">{{ row.family }}</span>
              </template>
            </el-table-column>
            <el-table-column label="强弱分数" min-width="190">
              <template #default="{ row }">
                <div v-if="row.strength?.scored" class="score-cell">
                  <span class="score-track">
                    <i :class="verdictClass(row)" :style="{ width: `${row.strength.score}%` }" />
                  </span>
                  <span class="score-num tabular">{{ row.strength.score?.toFixed(1) }}</span>
                </div>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="判断" width="96">
              <template #default="{ row }">
                <span :class="verdictClass(row)">{{ row.strength?.label ?? "暂无数据" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="有效成分" width="96" align="right">
              <template #default="{ row }">
                <span v-if="row.strength?.members" class="tabular">{{ row.strength.members }} 只</span>
                <span v-else class="muted">不足 3 只</span>
              </template>
            </el-table-column>
            <el-table-column label="已入库事实" width="110" align="right">
              <template #default="{ row }">
                <span v-if="row.factCount" class="tabular">{{ row.factCount }} 条</span>
                <span v-else class="muted">{{ factsPending && row.factCount === null ? "统计中" : "暂无" }}</span>
              </template>
            </el-table-column>
            <el-table-column label="" width="84" align="right">
              <template #default="{ row }">
                <RouterLink class="row-link" :to="`/industry/${row.id}`">逻辑追踪 ›</RouterLink>
              </template>
            </el-table-column>
          </el-table>

          <ul v-else class="tile-grid">
            <li v-for="row in visibleRows" :key="row.id">
              <RouterLink class="tile" :to="`/industry/${row.id}`">
                <header class="tile-head">
                  <strong>{{ row.name }}</strong>
                  <span class="family">{{ row.family }}</span>
                </header>
                <p class="tile-score">
                  <template v-if="row.strength?.scored">
                    <span class="tabular">{{ row.strength.score?.toFixed(1) }}</span>
                    <small>/100</small>
                  </template>
                  <span v-else class="tile-empty">样本不足</span>
                </p>
                <span class="score-track">
                  <i
                    v-if="row.strength?.scored"
                    :class="verdictClass(row)"
                    :style="{ width: `${row.strength.score}%` }"
                  />
                </span>
                <footer class="tile-foot">
                  <span :class="verdictClass(row)">{{ strengthCaption(row.strength) }}</span>
                  <span class="muted tabular">{{ row.factCount ? `${row.factCount} 条事实` : "暂无事实" }}</span>
                </footer>
              </RouterLink>
            </li>
          </ul>
        </PanelCard>

        <div class="stack">
          <PanelCard title="强弱分数对比" info="横线为偏弱（40）与偏强（60）分界；样本不足的行业不参与对比">
            <StateBlock
              v-if="!scoredRows.length"
              state="empty"
              :message="sectorsFailed ? '强弱数据暂时读取失败，稍后刷新重试' : '暂无成分股足够的行业，日线入库后自动计算'"
            />
            <VChart
              v-else
              class="score-chart"
              :style="{ height: scoreChartHeight }"
              :option="scoreOption"
              autoresize
            />
            <div v-if="unscoredNames.length" class="pending">
              <p class="muted">样本不足 3 只、暂不判断：</p>
              <div class="chips">
                <StatusPill v-for="name in unscoredNames" :key="name" :text="name" tone="muted" />
              </div>
            </div>
          </PanelCard>

          <PanelCard title="口径说明" info="本页数字都能追溯到行情与财报原始数据">
            <ul class="notes">
              <li>{{ STRENGTH_RULE }}</li>
              <li v-if="sectorNotice">{{ sectorNotice }}</li>
              <li>已入库事实目前来自美股 SEC 财报，A 股成分的财报事实暂未入库，因此申万行业显示「暂无」。</li>
            </ul>
          </PanelCard>

          <PanelCard title="相关页面">
            <nav class="link-row">
              <RouterLink to="/market/sectors">板块强弱</RouterLink>
              <RouterLink to="/hotspots">市场热点</RouterLink>
              <RouterLink to="/reports">研究报告</RouterLink>
            </nav>
          </PanelCard>
        </div>
      </div>

      <SourceFooter
        :as-of="asOf"
        :sources="['申万行业分类（2021 版）', '成分股与沪深300日线', 'SEC 财报事实']"
        note="强弱分数按入库日线计算；行业没有足够成分时不出分"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.asof-picker {
  width: 150px;
}

.sort-select {
  width: 128px;
}

.rank-table {
  width: 100%;
}

:deep(.el-radio-group) {
  --el-radio-button-checked-bg-color: var(--accent-soft);
  --el-radio-button-checked-text-color: var(--text-strong);
  --el-radio-button-checked-border-color: var(--accent);
}

.rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  border-radius: 6px;
  background: var(--panel-raise);
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
}

.rank.top {
  background: var(--accent-soft);
  color: var(--accent);
}

.name-link {
  color: var(--text-strong);
  font-weight: 600;
  text-decoration: none;
}

.name-link:hover {
  color: var(--accent);
}

.family {
  margin-left: 8px;
  font-size: 12px;
  color: var(--muted);
}

.score-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.score-track {
  position: relative;
  display: block;
  flex: 1 1 auto;
  height: 6px;
  border-radius: 999px;
  background: var(--panel-raise);
  overflow: hidden;
}

.score-track i {
  display: block;
  height: 100%;
  border-radius: 999px;
}

.score-num {
  min-width: 36px;
  text-align: right;
  font-weight: 600;
  color: var(--text-strong);
}

.verdict {
  font-size: 13px;
}

i.verdict {
  font-size: 0;
}

.tone-up {
  color: var(--rise);
}

i.tone-up {
  background: var(--rise);
}

.tone-down {
  color: var(--fall);
}

i.tone-down {
  background: var(--fall);
}

.tone-accent {
  color: var(--accent);
}

i.tone-accent {
  background: var(--accent);
}

.tone-muted {
  color: var(--muted);
}

.row-link {
  font-size: 13px;
  text-decoration: none;
}

.tile-grid {
  margin: 0;
  padding: 4px 16px 10px;
  list-style: none;
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
}

.tile {
  display: grid;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  color: inherit;
  text-decoration: none;
}

.tile:hover {
  border-color: var(--edge-light);
}

.tile-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.tile-head strong {
  font-size: 15px;
  color: var(--text-strong);
}

.tile-head .family {
  margin: 0;
}

.tile-score {
  margin: 0;
  font-size: 26px;
  font-weight: 600;
  line-height: 1.1;
  color: var(--text-strong);
}

.tile-score small {
  margin-left: 2px;
  font-size: 12px;
  color: var(--muted);
}

.tile-empty {
  font-size: 15px;
  color: var(--muted);
}

.tile-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 13px;
}

.score-chart {
  width: 100%;
}

.pending {
  margin-top: 10px;
  border-top: 1px solid var(--line-soft);
  padding-top: 10px;
}

.pending p {
  margin: 0 0 8px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.notes {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text);
}

.link-row {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 13px;
}
</style>
