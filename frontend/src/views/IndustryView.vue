<script setup lang="ts">
/**
 * 产业研究 · 行业总览（/industry）。
 *
 * 数据来源（只读）：
 *  - GET /api/v1/industry-market/boards?level=   东财一/二/三级行业或概念板块的行情快照与市场宽度
 *  - GET /api/v1/industry-market/hot             东财人气榜、同花顺热股与热门板块
 *  - GET /api/v1/industries?q=                   申万一级行业目录，用于跳到「逻辑追踪」写研究假设
 *
 * 所有区间涨幅、资金流都是上游原值或后端计算值；页面只排序、着色与分组。
 */
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { Coin, DataAnalysis, Histogram, TrendCharts } from "@element-plus/icons-vue"
import { apiUrl, readJson } from "../api/client"
import { asList, asRecord, asString } from "../api/json"
import {
  LEVEL_OPTIONS,
  companySymbol,
  fetchCatalog,
  fetchHot,
  fetchOverview,
  fetchSnapshotDates,
  momentumQuadrant,
  sectionBadge,
  type BoardLevel,
  type BoardOverview,
  type BoardQuote,
  type CatalogItem,
  type HotList,
} from "../api/industryMarket"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniRing from "../components/ui/MiniRing.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import { cssVar, heatColor, pctText, sectionLabel, sectionTone, shortTime, toYi, yuan } from "./parts/industry/chartKit"

type Phase = "loading" | "ready" | "error"
type PeriodKey = "changePct" | "change5dPct" | "change10dPct" | "change60dPct" | "changeYtdPct"
type HotTab = "ths" | "em" | "industry" | "concept"

const HOT_TABS: { key: HotTab; label: string }[] = [
  { key: "ths", label: "同花顺热股" },
  { key: "em", label: "东财人气" },
  { key: "industry", label: "热门行业" },
  { key: "concept", label: "热门概念" },
]

const PERIODS: { key: PeriodKey; label: string; range: number }[] = [
  { key: "changePct", label: "今日", range: 4 },
  { key: "change5dPct", label: "5日", range: 8 },
  { key: "change10dPct", label: "10日", range: 12 },
  { key: "change60dPct", label: "60日", range: 25 },
  { key: "changeYtdPct", label: "年初至今", range: 40 },
]
const TREEMAP_LIMIT = 160
const PAGE_SIZE = 30

const router = useRouter()
const level = ref<BoardLevel>("1")
const period = ref<PeriodKey>("changePct")
const phase = ref<Phase>("loading")
const failure = ref("")
const overview = ref<BoardOverview | null>(null)
const hot = ref<HotList | null>(null)
const hotFailure = ref("")
const hotTab = ref<HotTab>("ths")
const keyword = ref("")
const page = ref(1)
const sortKey = ref<keyof BoardQuote>("changePct")
const sortDesc = ref(true)
const thesisIndustries = ref<Map<string, string>>(new Map())
const degraded = ref(false)
const catalog = ref<CatalogItem[]>([])
const asOf = ref("")
const snapshotDates = ref<string[]>([])

const periodMeta = computed(() => PERIODS.find((item) => item.key === period.value) ?? PERIODS[0]!)
const boards = computed<BoardQuote[]>(() => overview.value?.boards ?? [])
const breadth = computed(() => overview.value?.breadth ?? null)

const ranked = computed<BoardQuote[]>(() =>
  [...boards.value]
    .filter((row) => row[period.value] !== null)
    .sort((left, right) => (right[period.value] ?? 0) - (left[period.value] ?? 0)),
)
const leader = computed<BoardQuote | null>(() => ranked.value[0] ?? null)
const laggard = computed<BoardQuote | null>(() => ranked.value[ranked.value.length - 1] ?? null)
const topInflow = computed<BoardQuote | null>(
  () => [...boards.value].sort((a, b) => (b.mainNetInflow ?? -Infinity) - (a.mainNetInflow ?? -Infinity))[0] ?? null,
)

const boardRing = computed(() => [
  { label: "上涨", ratio: breadth.value?.boardsUp ?? 0, tone: "up" as const },
  { label: "下跌", ratio: breadth.value?.boardsDown ?? 0, tone: "down" as const },
  {
    label: "平",
    ratio: Math.max(0, (breadth.value?.boards ?? 0) - (breadth.value?.boardsUp ?? 0) - (breadth.value?.boardsDown ?? 0)),
    tone: "muted" as const,
  },
])
const stockRing = computed(() => [
  { label: "上涨", ratio: breadth.value?.stocksUp ?? 0, tone: "up" as const },
  { label: "下跌", ratio: breadth.value?.stocksDown ?? 0, tone: "down" as const },
  { label: "平盘", ratio: breadth.value?.stocksFlat ?? 0, tone: "muted" as const },
])

const filtered = computed<BoardQuote[]>(() => {
  const text = keyword.value.trim()
  const rows = text ? boards.value.filter((row) => row.name.includes(text) || row.code.includes(text.toUpperCase())) : boards.value
  const key = sortKey.value
  return [...rows].sort((left, right) => {
    const a = left[key]
    const b = right[key]
    if (typeof a === "number" && typeof b === "number") {
      return sortDesc.value ? b - a : a - b
    }
    if (typeof a === "number") {
      return -1
    }
    if (typeof b === "number") {
      return 1
    }
    return String(a).localeCompare(String(b), "zh-Hans-CN")
  })
})
const paged = computed(() => filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

const treemapOption = computed(() => {
  const rows = [...boards.value]
    .filter((row) => (row.marketCap ?? 0) > 0)
    .sort((a, b) => (b.marketCap ?? 0) - (a.marketCap ?? 0))
    .slice(0, TREEMAP_LIMIT)
  const range = periodMeta.value.range
  const text = cssVar("--text-strong", "#f1f5ff")
  return {
    tooltip: {
      formatter: (info: { data?: { code?: string; name?: string; value?: number; pct?: number | null; inflow?: number | null } }) => {
        const data = info.data
        if (!data?.code) {
          return ""
        }
        return [
          `<b>${data.name}</b> <span style="opacity:.6">${data.code}</span>`,
          `${periodMeta.value.label}涨跌：${pctText(data.pct ?? null)}`,
          `总市值：${yuan(data.value ?? null)}元`,
          `主力净流入：${yuan(data.inflow ?? null)}元`,
        ].join("<br/>")
      },
    },
    series: [
      {
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        width: "100%",
        height: "100%",
        top: 0,
        left: 0,
        itemStyle: { borderColor: cssVar("--panel", "#0f1b30"), borderWidth: 1, gapWidth: 1 },
        label: {
          show: true,
          color: text,
          fontSize: 12,
          lineHeight: 16,
          formatter: (info: { data?: { name?: string; pct?: number | null } }) =>
            `${info.data?.name ?? ""}\n${pctText(info.data?.pct ?? null)}`,
        },
        upperLabel: { show: false },
        data: rows.map((row) => ({
          name: row.name,
          code: row.code,
          value: row.marketCap,
          pct: row[period.value],
          inflow: row.mainNetInflow,
          itemStyle: { color: heatColor(row[period.value], range) },
        })),
      },
    ],
  }
})

const scatterOption = computed(() => {
  const rows = boards.value.filter((row) => row.change5dPct !== null && row.change60dPct !== null)
  const caps = rows.map((row) => row.marketCap ?? 0)
  const maxCap = Math.max(1, ...caps)
  const muted = cssVar("--muted", "#94a4be")
  const quadrant = (left: string, top: string, label: string, color: string) => ({
    type: "text",
    left,
    top,
    style: { text: label, fill: color, fontSize: 12, opacity: 0.75 },
  })
  return {
    grid: { left: 48, right: 24, top: 24, bottom: 40 },
    tooltip: {
      formatter: (info: { data?: { name?: string; value?: number[] } }) => {
        const [x, y] = info.data?.value ?? []
        return `<b>${info.data?.name ?? ""}</b><br/>60日：${pctText(x ?? null)}<br/>5日：${pctText(y ?? null)}<br/>${momentumQuadrant(y ?? null, x ?? null)}`
      },
    },
    xAxis: { type: "value", name: "60日涨跌 %", nameLocation: "middle", nameGap: 26, splitLine: { show: false } },
    yAxis: { type: "value", name: "5日涨跌 %", splitLine: { show: false } },
    graphic: [
      quadrant("right", "top", "强势延续", cssVar("--rise", "#f6465d")),
      quadrant("8%", "top", "低位反弹", cssVar("--orange", "#fb923c")),
      quadrant("right", "bottom", "高位回调", cssVar("--cyan", "#22d3ee")),
      quadrant("8%", "bottom", "弱势下行", cssVar("--fall", "#1fc37d")),
    ],
    series: [
      {
        type: "scatter",
        data: rows.map((row) => ({
          name: row.name,
          code: row.code,
          value: [row.change60dPct, row.change5dPct],
          symbolSize: 6 + Math.sqrt((row.marketCap ?? 0) / maxCap) * 26,
          itemStyle: { color: heatColor(row.changePct, 4), borderColor: "rgba(255,255,255,.35)", borderWidth: 0.5 },
        })),
        label: {
          show: rows.length <= 40,
          formatter: (info: { data?: { name?: string } }) => info.data?.name ?? "",
          position: "right",
          fontSize: 10,
          color: muted,
        },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { type: "dashed", color: muted, opacity: 0.5 },
          label: { show: false },
          data: [{ xAxis: 0 }, { yAxis: 0 }],
        },
      },
    ],
  }
})

const flowRows = computed(() => {
  const rows = boards.value.filter((row) => row.mainNetInflow !== null)
  const sorted = [...rows].sort((a, b) => (b.mainNetInflow ?? 0) - (a.mainNetInflow ?? 0))
  const top = sorted.slice(0, 8)
  const bottom = sorted.slice(-8).filter((row) => !top.includes(row))
  return [...top, ...bottom].reverse()
})

const flowOption = computed(() => ({
  grid: { left: 92, right: 56, top: 8, bottom: 24 },
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    valueFormatter: (value: number) => `${value} 亿元`,
  },
  xAxis: { type: "value", name: "亿元", splitLine: { show: false } },
  yAxis: { type: "category", data: flowRows.value.map((row) => row.name), axisLabel: { fontSize: 11 } },
  series: [
    {
      type: "bar",
      name: "主力净流入",
      barWidth: 10,
      data: flowRows.value.map((row) => ({
        value: toYi(row.mainNetInflow),
        code: row.code,
        itemStyle: {
          color: (row.mainNetInflow ?? 0) >= 0 ? cssVar("--rise", "#f6465d") : cssVar("--fall", "#1fc37d"),
          borderRadius: (row.mainNetInflow ?? 0) >= 0 ? [0, 3, 3, 0] : [3, 0, 0, 3],
        },
      })),
      label: { show: true, position: "right", fontSize: 10, color: cssVar("--muted", "#94a4be") },
    },
  ],
}))

function openBoard(code: string | undefined): void {
  if (code) {
    void router.push(`/industry/board/${code}`)
  }
}

function onChartClick(params: { data?: unknown }): void {
  const code = asString(asRecord(params.data)?.code)
  openBoard(code ?? undefined)
}

function onFlowClick(params: { dataIndex?: number }): void {
  openBoard(flowRows.value[params.dataIndex ?? -1]?.code)
}

function sortBy(key: keyof BoardQuote): void {
  if (sortKey.value === key) {
    sortDesc.value = !sortDesc.value
  } else {
    sortKey.value = key
    sortDesc.value = true
  }
  page.value = 1
}

function sortMark(key: keyof BoardQuote): string {
  return sortKey.value === key ? (sortDesc.value ? "↓" : "↑") : ""
}

function breadthWidth(row: BoardQuote): string {
  const up = row.advancers ?? 0
  const total = up + (row.decliners ?? 0) + (row.unchanged ?? 0)
  return total ? `${(up / total) * 100}%` : "0%"
}

async function loadOverview(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  try {
    overview.value = await fetchOverview(level.value, asOf.value)
    degraded.value = false
    page.value = 1
    phase.value = "ready"
  } catch (error) {
    failure.value = error instanceof Error ? error.message : "上游行情暂时不可用"
    phase.value = "error"
    if (!overview.value || overview.value.level !== level.value || degraded.value) {
      // 行情和库里快照都没有：保留热榜与板块目录，行情区块明确标「暂不可用」，不画任何占位数字。
      degraded.value = true
      overview.value = unavailableOverview(level.value, failure.value)
    }
  }
}

function unavailableOverview(levelKey: string, reason: string): BoardOverview {
  return {
    level: levelKey,
    section: { status: "unavailable", reason, fetchedAt: "", origin: "live", dataDate: "" },
    breadth: null,
    boards: [],
    sources: [],
    rules: [],
  }
}

const directory = computed(() => {
  const want = LEVEL_OPTIONS.find((item) => item.key === level.value)
  return catalog.value.filter((item) =>
    level.value === "concept" ? item.kind === "concept" : item.kind === "industry" && String(item.level) === level.value,
  ).map((item) => ({ ...item, label: want?.label ?? "" }))
})

async function loadCatalog(): Promise<void> {
  try {
    catalog.value = await fetchCatalog()
  } catch {
    catalog.value = []
  }
}

async function loadHot(): Promise<void> {
  hotFailure.value = ""
  try {
    hot.value = await fetchHot()
  } catch (error) {
    hotFailure.value = error instanceof Error ? error.message : "热榜暂时不可用"
  }
}

async function loadThesisIndustries(): Promise<void> {
  try {
    const response = await fetch(apiUrl("/api/v1/industries?q="))
    if (!response.ok) {
      return
    }
    const rows = asList(asRecord(asRecord(await readJson(response))?.data)?.industries)
    const map = new Map<string, string>()
    for (const item of rows) {
      const row = asRecord(item)
      const id = asString(row?.id)
      const name = asString(row?.name)
      if (id && name) {
        map.set(name, id)
      }
    }
    thesisIndustries.value = map
  } catch {
    thesisIndustries.value = new Map()
  }
}

async function loadSnapshotDates(): Promise<void> {
  try {
    snapshotDates.value = await fetchSnapshotDates()
  } catch {
    snapshotDates.value = []
  }
}

function refresh(): void {
  void loadOverview()
  void loadHot()
}

watch(level, () => {
  keyword.value = ""
  void loadOverview()
})

watch(asOf, () => {
  void loadOverview()
})

onMounted(() => {
  void loadOverview()
  void loadHot()
  void loadThesisIndustries()
  void loadSnapshotDates()
  void loadCatalog()
})

const overviewBadge = computed(() => (overview.value ? sectionBadge(overview.value.section) : null))

const hotStocks = computed(() => (hotTab.value === "em" ? hot.value?.eastmoney : hot.value?.thsStocks) ?? [])
const hotPlates = computed(() => (hotTab.value === "concept" ? hot.value?.thsConcepts : hot.value?.thsIndustries) ?? [])
const hotSection = computed(() => {
  if (!hot.value) {
    return null
  }
  if (hotTab.value === "em") {
    return hot.value.eastmoneySection
  }
  return hotTab.value === "ths" ? hot.value.thsSection : hot.value.platesSection
})
const sourceNames = computed(() => [
  ...(overview.value?.sources.map((item) => item.name) ?? []),
  ...(hot.value?.sources.map((item) => item.name) ?? []),
])
</script>

<template>
  <main class="page">
    <PageHeader title="产业研究" subtitle="东财一 / 二 / 三级行业与概念板块的行情、资金与热度，点任意板块进入深度分析">
      <template #actions>
        <el-radio-group v-model="level" size="default">
          <el-radio-button v-for="item in LEVEL_OPTIONS" :key="item.key" :value="item.key">{{ item.label }}</el-radio-button>
        </el-radio-group>
        <el-select
          v-model="asOf"
          class="as-of"
          aria-label="选择行情日期"
          :placeholder="snapshotDates.length ? '实时行情' : '暂无入库快照'"
          :disabled="!snapshotDates.length"
        >
          <el-option label="实时行情" value="" />
          <el-option v-for="day in snapshotDates" :key="day" :label="`${day} 收盘快照`" :value="day" />
        </el-select>
        <el-button :loading="phase === 'loading'" @click="refresh">刷新</el-button>
        <RouterLink class="pulse-link" to="/industry/pulse">轮动 · 涨停 · 调研 →</RouterLink>
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading' && !overview" state="loading" :rows="6" />
    <StateBlock v-else-if="phase === 'error' && !overview" state="error" :message="failure" @retry="loadOverview" />

    <template v-else-if="overview">
      <p v-if="overview.section.status !== 'ok' && !degraded" class="notice warn" role="status">
        {{ overview.section.reason || "行情暂时不可用" }}
      </p>
      <p v-else-if="overview.section.origin === 'stored'" class="notice" role="status">
        当前显示 {{ overview.section.dataDate }} 收盘后入库的板块快照，不是实时行情；切回「实时行情」可查看最新数据。
      </p>
      <p v-if="overviewBadge" class="badge-line">
        <StatusPill :text="overviewBadge.text" :tone="overviewBadge.tone" dot />
        <span v-if="overview.section.fetchedAt" class="muted fine">取数 {{ shortTime(overview.section.fetchedAt) }}</span>
      </p>
      <p v-if="phase === 'error' && !degraded" class="notice danger" role="alert">{{ failure }}（下方为上次成功加载的数据）</p>
      <div v-if="degraded" class="degraded" role="alert">
        <p>
          板块行情现在拿不到：东财行情接口对本机限流，库里也还没有收盘快照（每个工作日 15:45 的入库任务成功跑一次后就有）。
          下方的同花顺热榜不受影响，板块目录仍可点进去看研报和研究假设。
        </p>
        <el-button size="small" :loading="phase === 'loading'" @click="loadOverview">重试行情</el-button>
      </div>

      <div class="grid-4 section">
        <StatCard
          title="板块涨跌"
          :value="breadth ? `${breadth.boardsUp} / ${breadth.boardsDown}` : null"
          :caption="breadth ? `${breadth.boards} 个板块：上涨 / 下跌` : '暂无行情'"
          info="当日涨跌幅大于 0 / 小于 0 的板块数"
          tone="accent"
          more-text=""
        >
          <template #icon><Histogram /></template>
          <template #chart><MiniRing :segments="boardRing" :size="58" :thickness="8" /></template>
        </StatCard>
        <StatCard
          title="成分股涨跌家数"
          :value="breadth ? `${breadth.stocksUp} / ${breadth.stocksDown}` : null"
          :caption="level === '1' ? '一级行业覆盖全部 A 股，可视作全市场宽度' : '同一只股票可能属于多个板块，家数会重复计入'"
          info="各板块上涨 / 下跌家数之和，来自东财板块行情字段"
          tone="violet"
          more-text=""
        >
          <template #icon><DataAnalysis /></template>
          <template #chart><MiniRing :segments="stockRing" :size="58" :thickness="8" /></template>
        </StatCard>
        <StatCard
          title="主力资金净流入"
          :value="breadth?.totalMainNetInflow != null ? yuan(breadth.totalMainNetInflow) : null"
          unit="元"
          :caption="topInflow ? `流入最多：${topInflow.name} ${yuan(topInflow.mainNetInflow)}元` : '暂无资金数据'"
          info="超大单 + 大单净额之和；概念板块之间成分重叠，合计仅作方向参考"
          tone="orange"
          more-text=""
        >
          <template #icon><Coin /></template>
        </StatCard>
        <StatCard
          :title="`${periodMeta.label}最强`"
          :value="leader?.name ?? null"
          :caption="leader ? `${pctText(leader[period])} · 最弱 ${laggard?.name ?? '—'} ${pctText(laggard?.[period] ?? null)}` : '暂无数据'"
          info="按所选区间涨跌幅排序"
          tone="cyan"
          more-text=""
        >
          <template #icon><TrendCharts /></template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
        <PanelCard title="板块热力图" info="面积 = 总市值，颜色 = 所选区间涨跌幅（红涨绿跌）；点击方块进入板块分析">
          <template #actions>
            <el-radio-group v-model="period" size="small">
              <el-radio-button v-for="item in PERIODS" :key="item.key" :value="item.key">{{ item.label }}</el-radio-button>
            </el-radio-group>
          </template>
          <StateBlock v-if="!boards.length" state="empty" message="暂无板块行情" />
          <VChart v-else class="treemap" :option="treemapOption" autoresize @click="onChartClick" />
          <p v-if="boards.length > TREEMAP_LIMIT" class="muted fine">按总市值取前 {{ TREEMAP_LIMIT }} 个板块绘制，完整列表见下方排行。</p>
        </PanelCard>

        <PanelCard title="市场热度" info="东财股吧人气榜（按代码再取行情）与同花顺热榜，每 5 分钟刷新" flush>
          <template #actions>
            <StatusPill v-if="hotSection" :text="sectionLabel(hotSection.status)" :tone="sectionTone(hotSection.status)" />
          </template>
          <div class="hot-tabs">
            <button
              v-for="tab in HOT_TABS"
              :key="tab.key"
              type="button"
              :class="{ active: hotTab === tab.key }"
              @click="hotTab = tab.key"
            >
              {{ tab.label }}
            </button>
          </div>
          <StateBlock v-if="!hot && !hotFailure" state="loading" :rows="6" />
          <StateBlock v-else-if="hotFailure" state="error" :message="hotFailure" @retry="loadHot" />
          <StateBlock v-else-if="hotSection?.status === 'unavailable'" state="empty" :message="hotSection.reason || '这个榜单暂时不可用'" />
          <ol v-else-if="hotTab === 'ths' || hotTab === 'em'" class="hot-list">
            <li v-for="item in hotStocks.slice(0, 15)" :key="`${item.rank}-${item.code}`">
              <span class="hot-rank" :class="{ top: item.rank <= 3 }">{{ item.rank }}</span>
              <div class="hot-main">
                <p class="hot-name">
                  <RouterLink v-if="companySymbol(item.code, item.exchange)" :to="`/company/${companySymbol(item.code, item.exchange)}`">{{ item.name }}</RouterLink>
                  <span v-else>{{ item.name }}</span>
                  <small class="muted tabular">{{ item.code }}</small>
                </p>
                <p class="hot-tags">
                  <span v-for="tag in item.tags.slice(0, 3)" :key="tag" class="chip">{{ tag }}</span>
                  <span v-if="item.industry" class="chip">{{ item.industry }}</span>
                </p>
              </div>
              <DeltaValue :value="item.changePct" suffix="%" size="sm" />
            </li>
          </ol>
          <ol v-else class="hot-list">
            <li v-for="item in hotPlates.slice(0, 15)" :key="item.code">
              <span class="hot-rank" :class="{ top: item.rank <= 3 }">{{ item.rank }}</span>
              <div class="hot-main">
                <p class="hot-name">{{ item.name }}</p>
                <p class="hot-tags"><span v-if="item.tag" class="chip">{{ item.tag }}</span></p>
              </div>
              <DeltaValue :value="item.changePct" suffix="%" size="sm" />
            </li>
          </ol>
        </PanelCard>
      </div>

      <div class="grid-2 section">
        <PanelCard title="动量象限" info="横轴 60 日、纵轴 5 日涨跌幅；气泡大小 = 总市值，颜色 = 今日涨跌。右上强势延续、左上低位反弹、右下高位回调、左下弱势下行">
          <StateBlock v-if="!boards.length" state="empty" message="暂无板块行情" />
          <VChart v-else class="chart" :option="scatterOption" autoresize @click="onChartClick" />
        </PanelCard>
        <PanelCard title="主力资金流向" info="当日主力净流入前 8 与后 8 的板块，单位亿元；点击柱子进入板块分析">
          <VChart v-if="flowRows.length" class="chart" :option="flowOption" autoresize @click="onFlowClick" />
          <StateBlock v-else state="empty" message="上游没有返回资金流数据" />
        </PanelCard>
      </div>

      <PanelCard v-if="degraded" title="板块目录" info="东财板块目录不依赖行情接口；点击进入板块页，可看行业研报与研究假设" class="section">
        <StateBlock v-if="!directory.length" state="empty" message="板块目录也暂时读不到" />
        <div v-else class="directory">
          <RouterLink v-for="item in directory" :key="item.code" class="chip link" :to="`/industry/board/${item.code}`">
            {{ item.name }}
          </RouterLink>
        </div>
      </PanelCard>

      <PanelCard title="板块排行" :info="`共 ${filtered.length} 个板块；点表头排序，点名称进入板块分析`" flush class="section">
        <template #actions>
          <el-input v-model="keyword" size="small" clearable placeholder="搜索板块名称或代码" class="search" @input="page = 1" />
        </template>
        <div class="table-wrap">
          <table class="rank">
            <thead>
              <tr>
                <th class="left">板块</th>
                <th @click="sortBy('changePct')">今日 {{ sortMark("changePct") }}</th>
                <th @click="sortBy('change5dPct')">5日 {{ sortMark("change5dPct") }}</th>
                <th @click="sortBy('change10dPct')">10日 {{ sortMark("change10dPct") }}</th>
                <th @click="sortBy('change60dPct')">60日 {{ sortMark("change60dPct") }}</th>
                <th @click="sortBy('changeYtdPct')">年初至今 {{ sortMark("changeYtdPct") }}</th>
                <th @click="sortBy('mainNetInflow')">主力净流入 {{ sortMark("mainNetInflow") }}</th>
                <th @click="sortBy('mainNetInflow5d')">5日主力 {{ sortMark("mainNetInflow5d") }}</th>
                <th class="left">涨 / 跌家数</th>
                <th @click="sortBy('amount')">成交额 {{ sortMark("amount") }}</th>
                <th @click="sortBy('peTtm')">市盈率 {{ sortMark("peTtm") }}</th>
                <th class="left">领涨股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in paged" :key="row.code">
                <td class="left">
                  <RouterLink class="name-link" :to="`/industry/board/${row.code}`">{{ row.name }}</RouterLink>
                  <RouterLink v-if="thesisIndustries.get(row.name)" class="thesis-link" :to="`/industry/${thesisIndustries.get(row.name)}`" title="申万同名行业的研究假设">假设</RouterLink>
                </td>
                <td><DeltaValue :value="row.changePct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.change5dPct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.change10dPct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.change60dPct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.changeYtdPct" suffix="%" size="sm" /></td>
                <td :class="(row.mainNetInflow ?? 0) >= 0 ? 'up' : 'down'" class="tabular">{{ yuan(row.mainNetInflow) }}</td>
                <td :class="(row.mainNetInflow5d ?? 0) >= 0 ? 'up' : 'down'" class="tabular">{{ yuan(row.mainNetInflow5d) }}</td>
                <td class="left">
                  <span class="breadth-bar"><i :style="{ width: breadthWidth(row) }" /></span>
                  <span class="tabular muted small">{{ row.advancers ?? "—" }} / {{ row.decliners ?? "—" }}</span>
                </td>
                <td class="tabular">{{ yuan(row.amount) }}</td>
                <td class="tabular">{{ row.peTtm !== null ? row.peTtm.toFixed(1) : "—" }}</td>
                <td class="left">
                  <span v-if="row.leaderName">{{ row.leaderName }} <DeltaValue :value="row.leaderChangePct" suffix="%" size="sm" /></span>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="filtered.length > PAGE_SIZE" class="pager">
          <el-pagination v-model:current-page="page" :page-size="PAGE_SIZE" :total="filtered.length" layout="total, prev, pager, next" small background />
        </div>
      </PanelCard>

      <PanelCard title="口径说明" class="section">
        <ul class="notes">
          <li v-for="rule in overview.rules" :key="rule">{{ rule }}</li>
          <li>「假设」入口链接到申万同名行业的逻辑追踪页，可写下可证伪的研究假设并附上财报依据。</li>
        </ul>
      </PanelCard>

      <SourceFooter
        :as-of="shortTime(overview.section.fetchedAt)"
        :sources="sourceNames"
        latency="交易时段约 15 秒延迟，本系统缓存 60 秒"
        :stale="overview.section.status === 'stale'"
        note="板块行情为实时快照，不入库"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.notice {
  margin: 0 0 12px;
  font-size: 13px;
}

.notice.warn {
  color: var(--warn);
}

.degraded {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 12px;
  padding: 10px 14px;
  border: 1px solid color-mix(in srgb, var(--warn) 45%, transparent);
  border-radius: 8px;
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  font-size: 13px;
  line-height: 1.6;
}

.degraded p {
  margin: 0;
}

.directory {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.badge-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 12px;
}

.badge-line .fine {
  margin: 0;
}

.as-of {
  width: 168px;
}

.pulse-link {
  align-self: center;
  color: var(--accent);
  font-size: 13px;
  white-space: nowrap;
}

.notice.danger {
  color: var(--danger);
}

.treemap {
  width: 100%;
  height: 420px;
}

.chart {
  width: 100%;
  height: 340px;
}

.fine {
  margin: 6px 0 0;
  font-size: 12px;
}

.hot-tabs {
  display: flex;
  gap: 4px;
  padding: 0 16px 8px;
  border-bottom: 1px solid var(--line-soft);
}

.hot-tabs button {
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
}

.hot-tabs button.active {
  color: var(--text-strong);
  background: var(--accent-soft);
}

.hot-list {
  list-style: none;
  margin: 0;
  padding: 4px 16px 6px;
  max-height: 404px;
  overflow: auto;
}

.hot-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line-soft);
}

.hot-rank {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  background: var(--panel-raise);
  color: var(--muted);
}

.hot-rank.top {
  background: color-mix(in srgb, var(--rise) 22%, transparent);
  color: var(--rise);
}

.hot-main {
  flex: 1 1 auto;
  min-width: 0;
}

.hot-name {
  margin: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
}

.hot-name a {
  color: inherit;
  text-decoration: none;
}

.hot-name a:hover {
  color: var(--accent);
}

.hot-tags {
  margin: 3px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.chip {
  font-size: 11px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--panel-raise);
  color: var(--muted);
  white-space: nowrap;
}

.search {
  width: 200px;
}

.table-wrap {
  overflow-x: auto;
}

.rank {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.rank th,
.rank td {
  padding: 8px 10px;
  text-align: right;
  white-space: nowrap;
  border-bottom: 1px solid var(--line-soft);
}

.rank th {
  color: var(--muted);
  font-weight: 500;
  cursor: pointer;
  user-select: none;
  position: sticky;
  top: 0;
  background: var(--panel);
}

.rank .left {
  text-align: left;
}

.rank tbody tr:hover {
  background: var(--panel-soft);
}

.name-link {
  color: var(--text-strong);
  font-weight: 600;
  text-decoration: none;
}

.name-link:hover {
  color: var(--accent);
}

.thesis-link {
  margin-left: 6px;
  font-size: 11px;
  padding: 0 5px;
  border-radius: 4px;
  border: 1px solid var(--line);
  color: var(--muted);
  text-decoration: none;
}

.up {
  color: var(--rise);
}

.down {
  color: var(--fall);
}

.breadth-bar {
  display: inline-block;
  width: 64px;
  height: 6px;
  margin-right: 6px;
  vertical-align: middle;
  border-radius: 999px;
  background: color-mix(in srgb, var(--fall) 55%, transparent);
  overflow: hidden;
}

.breadth-bar i {
  display: block;
  height: 100%;
  background: var(--rise);
}

.small {
  font-size: 12px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 10px 16px 4px;
}

.notes {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 13px;
  line-height: 1.7;
}

:deep(.el-radio-group) {
  --el-radio-button-checked-bg-color: var(--accent-soft);
  --el-radio-button-checked-text-color: var(--text-strong);
  --el-radio-button-checked-border-color: var(--accent);
}
</style>
