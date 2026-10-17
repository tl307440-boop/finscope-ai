<script setup lang="ts">
/**
 * 产业研究 · 板块分析（/industry/board/:code）。
 *
 * 数据来源（只读）：
 *  - GET /api/v1/industry-market/catalog                 东财行业 / 概念目录，用于顶部切换
 *  - GET /api/v1/industry-market/boards/{code}           行情、日线 + 均线、相对沪深300、成分、子行业、估值分位
 *  - GET /api/v1/industry-market/boards/{code}/reports   近 180 天行业研报
 *  - GET /api/v1/industries?q=                           申万同名行业，跳到逻辑追踪写研究假设
 *
 * 每个区块独立显示 ok / 旧数据 / 不可用，上游某一项失败不会让整页空白。
 */
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import { Coin, DataLine, Histogram, PieChart } from "@element-plus/icons-vue"
import { asRecord, asString } from "../api/json"
import {
  companySymbol,
  fetchCatalog,
  fetchDetail,
  fetchReports,
  levelLabel,
  sectionBadge,
  type BoardDetail,
  type CatalogItem,
  type Member,
  type Report,
  type Section,
} from "../api/industryMarket"
import DeltaValue from "../components/ui/DeltaValue.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import PanelCard from "../components/ui/PanelCard.vue"
import SectionTabs from "../components/ui/SectionTabs.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import StatusPill from "../components/ui/StatusPill.vue"
import BoardReportsPanel from "./parts/industry/BoardReportsPanel.vue"
import BoardThesesPanel from "./parts/industry/BoardThesesPanel.vue"
import { cssVar, heatColor, pctText, shortTime, yuan } from "./parts/industry/chartKit"

type Phase = "loading" | "ready" | "error"
type MemberSort = "marketCap" | "changePct" | "change5dPct" | "change60dPct" | "changeYtdPct" | "mainNetInflow" | "peDynamic" | "pb" | "amount"

const props = defineProps<{ code: string }>()
const router = useRouter()

const phase = ref<Phase>("loading")
const failure = ref("")
const detail = ref<BoardDetail | null>(null)
const catalog = ref<CatalogItem[]>([])
const reports = ref<Report[]>([])
const reportSection = ref<Section | null>(null)
const tab = ref("overview")
const memberSort = ref<MemberSort>("marketCap")
const memberDesc = ref(true)
const memberKeyword = ref("")
const memberPage = ref(1)
const MEMBER_PAGE = 30

const board = computed(() => detail.value?.board ?? null)
const trend = computed(() => detail.value?.trend ?? null)
const trendBadge = computed(() => (trend.value ? sectionBadge(trend.value.section) : null))
const members = computed(() => detail.value?.members ?? null)
const valuation = computed(() => detail.value?.valuation ?? null)

const tabs = computed(() => {
  const research = [
    { key: "reports", label: `行业研报${reports.value.length ? ` ${reports.value.length}` : ""}` },
    { key: "theses", label: "研究假设" },
  ]
  // 行情取不到时只保留不依赖行情的页签。
  if (!detail.value && phase.value === "error") {
    return research
  }
  return [
    { key: "overview", label: "板块总览" },
    { key: "members", label: `成分股${members.value?.total ? ` ${members.value.total}` : ""}` },
    { key: "valuation", label: "估值分位" },
    ...research,
  ]
})

const catalogGroups = computed(() => {
  const groups: { label: string; items: CatalogItem[] }[] = [
    { label: "一级行业", items: [] },
    { label: "二级行业", items: [] },
    { label: "三级行业", items: [] },
    { label: "概念板块", items: [] },
  ]
  for (const item of catalog.value) {
    const index = item.kind === "concept" ? 3 : Math.min(2, Math.max(0, item.level - 1))
    groups[index]!.items.push(item)
  }
  return groups.filter((group) => group.items.length)
})

const latestBar = computed(() => trend.value?.bars[trend.value.bars.length - 1] ?? null)

const sortedMembers = computed<Member[]>(() => {
  const text = memberKeyword.value.trim()
  const rows = (members.value?.members ?? []).filter(
    (row) => !text || row.name.includes(text) || row.code.includes(text) || row.subIndustry.includes(text),
  )
  const key = memberSort.value
  return [...rows].sort((left, right) => {
    const a = left[key]
    const b = right[key]
    if (a === null && b === null) {
      return 0
    }
    if (a === null) {
      return 1
    }
    if (b === null) {
      return -1
    }
    return memberDesc.value ? b - a : a - b
  })
})
const pagedMembers = computed(() => sortedMembers.value.slice((memberPage.value - 1) * MEMBER_PAGE, memberPage.value * MEMBER_PAGE))

function topBy(key: "changePct" | "mainNetInflow", desc: boolean): Member[] {
  return [...(members.value?.members ?? [])]
    .filter((row) => row[key] !== null)
    .sort((a, b) => (desc ? (b[key] ?? 0) - (a[key] ?? 0) : (a[key] ?? 0) - (b[key] ?? 0)))
    .slice(0, 6)
}
const gainers = computed(() => topBy("changePct", true))
const losers = computed(() => topBy("changePct", false))
const inflows = computed(() => topBy("mainNetInflow", true))
const leaderTab = ref<"gainers" | "losers" | "inflows">("gainers")
const leaderRows = computed(() =>
  leaderTab.value === "gainers" ? gainers.value : leaderTab.value === "losers" ? losers.value : inflows.value,
)

const klineOption = computed(() => {
  const bars = trend.value?.bars ?? []
  const rise = cssVar("--rise", "#f6465d")
  const fall = cssVar("--fall", "#1fc37d")
  const dates = bars.map((bar) => bar.date)
  const start = bars.length > 130 ? Math.round(((bars.length - 130) / bars.length) * 100) : 0
  return {
    animation: false,
    legend: { top: 0, data: ["日K", "MA20", "MA60"] },
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 56, right: 18, top: 30, height: "58%" },
      { left: 56, right: 18, top: "76%", height: "14%" },
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLabel: { show: false } },
      { type: "category", data: dates, gridIndex: 1, boundaryGap: true },
    ],
    yAxis: [
      { scale: true, splitNumber: 4 },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { formatter: (v: number) => `${(v / 1e8).toFixed(0)}亿` } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], start, end: 100, height: 16, bottom: 4 },
    ],
    series: [
      {
        name: "日K",
        type: "candlestick",
        data: bars.map((bar) => [bar.open, bar.close, bar.low, bar.high]),
        itemStyle: { color: rise, color0: fall, borderColor: rise, borderColor0: fall },
      },
      { name: "MA20", type: "line", data: bars.map((bar) => bar.ma20), showSymbol: false, lineStyle: { width: 1.2 }, color: cssVar("--warn", "#fbbf24") },
      { name: "MA60", type: "line", data: bars.map((bar) => bar.ma60), showSymbol: false, lineStyle: { width: 1.2 }, color: cssVar("--violet", "#a78bfa") },
      {
        name: "成交额",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: bars.map((bar) => ({ value: bar.amount, itemStyle: { color: bar.close >= bar.open ? rise : fall, opacity: 0.7 } })),
      },
    ],
  }
})

const relativeOption = computed(() => {
  const curve = trend.value?.relative ?? []
  return {
    legend: { top: 0 },
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 48, top: 32, bottom: 28 },
    xAxis: { type: "category", data: curve.map((point) => point.date) },
    yAxis: [
      { type: "value", scale: true, name: "指数(首日=100)" },
      { type: "value", name: "超额(百分点)", splitLine: { show: false } },
    ],
    series: [
      { name: board.value?.name ?? "板块", type: "line", showSymbol: false, data: curve.map((p) => p.boardIndex), color: cssVar("--accent", "#3b82f6") },
      { name: trend.value?.benchmark ?? "沪深300", type: "line", showSymbol: false, data: curve.map((p) => p.benchmarkIndex), color: cssVar("--muted", "#94a4be"), lineStyle: { type: "dashed" } },
      {
        name: "超额",
        type: "line",
        yAxisIndex: 1,
        showSymbol: false,
        data: curve.map((p) => p.excessPct),
        color: cssVar("--orange", "#fb923c"),
        areaStyle: { opacity: 0.12 },
        lineStyle: { width: 1 },
      },
    ],
  }
})

const distributionOption = computed(() => {
  const buckets = members.value?.distribution ?? []
  const color = (tone: string): string =>
    tone === "up" ? cssVar("--rise", "#f6465d") : tone === "down" ? cssVar("--fall", "#1fc37d") : cssVar("--muted", "#94a4be")
  return {
    grid: { left: 28, right: 8, top: 18, bottom: 24 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => `${v} 只` },
    xAxis: { type: "category", data: buckets.map((b) => b.label), axisLabel: { fontSize: 10, interval: 0 } },
    yAxis: { type: "value", minInterval: 1, splitLine: { show: false } },
    series: [
      {
        type: "bar",
        barWidth: "60%",
        data: buckets.map((b) => ({ value: b.count, itemStyle: { color: color(b.tone), borderRadius: [3, 3, 0, 0] } })),
        label: { show: true, position: "top", fontSize: 10, color: cssVar("--muted", "#94a4be") },
      },
    ],
  }
})

const compositionTitle = computed(() => (members.value?.subIndustries.length ? "子行业构成" : "成分股市值分布"))
const compositionOption = computed(() => {
  const subs = members.value?.subIndustries ?? []
  const data = subs.length
    ? subs.map((item) => ({
        name: item.name,
        code: item.code,
        value: item.marketCap,
        pct: item.weightedChangePct,
        extra: `${item.members} 只 · 主力 ${yuan(item.mainNetInflow)}元`,
        itemStyle: { color: heatColor(item.weightedChangePct, 4) },
      }))
    : [...(members.value?.members ?? [])]
        .filter((row) => (row.marketCap ?? 0) > 0)
        .sort((a, b) => (b.marketCap ?? 0) - (a.marketCap ?? 0))
        .slice(0, 60)
        .map((row) => ({
          name: row.name,
          code: "",
          value: row.marketCap,
          pct: row.changePct,
          extra: `${row.code} · 主力 ${yuan(row.mainNetInflow)}元`,
          itemStyle: { color: heatColor(row.changePct, 6) },
        }))
  return {
    tooltip: {
      formatter: (info: { data?: { name?: string; value?: number; pct?: number | null; extra?: string } }) =>
        `<b>${info.data?.name ?? ""}</b><br/>涨跌：${pctText(info.data?.pct ?? null)}<br/>总市值：${yuan(info.data?.value ?? null)}元<br/>${info.data?.extra ?? ""}`,
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
          fontSize: 12,
          color: cssVar("--text-strong", "#f1f5ff"),
          formatter: (info: { data?: { name?: string; pct?: number | null } }) => `${info.data?.name ?? ""}\n${pctText(info.data?.pct ?? null)}`,
        },
        data,
      },
    ],
  }
})

const bubbleOption = computed(() => {
  const rows = (members.value?.members ?? []).filter(
    (row) => row.peDynamic !== null && row.peDynamic > 0 && row.peDynamic < 300 && row.change60dPct !== null,
  )
  const maxCap = Math.max(1, ...rows.map((row) => row.marketCap ?? 0))
  const muted = cssVar("--muted", "#94a4be")
  return {
    grid: { left: 48, right: 20, top: 20, bottom: 40 },
    tooltip: {
      formatter: (info: { data?: { name?: string; value?: number[]; cap?: number | null } }) => {
        const [pe, chg] = info.data?.value ?? []
        return `<b>${info.data?.name ?? ""}</b><br/>市盈率(动)：${pe?.toFixed(1) ?? "—"}<br/>60日涨跌：${pctText(chg ?? null)}<br/>总市值：${yuan(info.data?.cap ?? null)}元`
      },
    },
    xAxis: { type: "log", name: "市盈率(动，对数)", nameLocation: "middle", nameGap: 26, splitLine: { show: false } },
    yAxis: { type: "value", name: "60日涨跌 %", splitLine: { show: false } },
    series: [
      {
        type: "scatter",
        data: rows.map((row) => ({
          name: row.name,
          value: [row.peDynamic, row.change60dPct],
          cap: row.marketCap,
          symbolSize: 5 + Math.sqrt((row.marketCap ?? 0) / maxCap) * 30,
          itemStyle: { color: heatColor(row.changePct, 6), borderColor: "rgba(255,255,255,.35)", borderWidth: 0.5 },
        })),
        label: {
          show: true,
          formatter: (info: { data?: { name?: string; cap?: number | null } }) =>
            (info.data?.cap ?? 0) >= maxCap * 0.25 ? (info.data?.name ?? "") : "",
          position: "right",
          fontSize: 10,
          color: muted,
        },
        markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { type: "dashed", color: muted, opacity: 0.5 }, data: [{ yAxis: 0 }] },
      },
    ],
  }
})

function bandOption(field: "peTtm" | "pbMrq", label: string) {
  const series = valuation.value?.series ?? []
  const summary = field === "peTtm" ? valuation.value?.peTtm : valuation.value?.pbMrq
  const muted = cssVar("--muted", "#94a4be")
  const line = (value: number | undefined, name: string, color: string) =>
    value === undefined ? null : { yAxis: value, name, lineStyle: { color, type: "dashed" }, label: { formatter: `${name} ${value.toFixed(2)}`, color, position: "insideEndTop", fontSize: 10 } }
  return {
    tooltip: { trigger: "axis", valueFormatter: (v: number) => (v === null || v === undefined ? "—" : v.toFixed(2)) },
    grid: { left: 48, right: 20, top: 16, bottom: 40 },
    xAxis: { type: "category", data: series.map((p) => p.date) },
    yAxis: { type: "value", scale: true, name: label },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 14, bottom: 4 }],
    series: [
      {
        name: label,
        type: "line",
        showSymbol: false,
        data: series.map((p) => p[field]),
        color: cssVar("--cyan", "#22d3ee"),
        areaStyle: { opacity: 0.08 },
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            line(summary?.p80, "80%分位", cssVar("--rise", "#f6465d")),
            line(summary?.median, "中位数", muted),
            line(summary?.p20, "20%分位", cssVar("--fall", "#1fc37d")),
          ].filter((item) => item !== null),
        },
      },
    ],
  }
}
const peBand = computed(() => bandOption("peTtm", "PE(TTM)"))
const pbBand = computed(() => bandOption("pbMrq", "PB(MRQ)"))

function percentileTone(value: number | null | undefined): "ok" | "warn" | "danger" | "muted" {
  if (value === null || value === undefined) {
    return "muted"
  }
  return value <= 30 ? "ok" : value >= 70 ? "danger" : "warn"
}

function percentileWord(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "无分位"
  }
  return value <= 30 ? "历史低位" : value >= 70 ? "历史高位" : "历史中位"
}

function sortMembers(key: MemberSort): void {
  if (memberSort.value === key) {
    memberDesc.value = !memberDesc.value
  } else {
    memberSort.value = key
    memberDesc.value = true
  }
  memberPage.value = 1
}

function mark(key: MemberSort): string {
  return memberSort.value === key ? (memberDesc.value ? "↓" : "↑") : ""
}

function switchBoard(next: string): void {
  if (next && next !== props.code) {
    void router.push(`/industry/board/${next}`)
  }
}

function onCompositionClick(params: { data?: unknown }): void {
  const code = asString(asRecord(params.data)?.code)
  if (code) {
    switchBoard(code)
  }
}

async function loadReports(): Promise<void> {
  reportSection.value = null
  reports.value = []
  try {
    const result = await fetchReports(props.code)
    reports.value = result.reports
    reportSection.value = result.section
  } catch (error) {
    reportSection.value = {
      status: "unavailable",
      reason: error instanceof Error ? error.message : "研报暂时不可用",
      fetchedAt: "",
      origin: "live",
      dataDate: "",
    }
  }
}

async function load(): Promise<void> {
  phase.value = "loading"
  failure.value = ""
  memberPage.value = 1
  void loadReports()
  try {
    detail.value = await fetchDetail(props.code)
    phase.value = "ready"
  } catch (error) {
    failure.value = error instanceof Error ? error.message : "上游行情暂时不可用"
    phase.value = "error"
    if (!detail.value && tab.value !== "theses") {
      tab.value = "reports"
    }
  }
}

watch(
  () => props.code,
  () => {
    tab.value = "overview"
    detail.value = null
    void load()
  },
)

onMounted(() => {
  void load()
  fetchCatalog()
    .then((items) => {
      catalog.value = items
    })
    .catch(() => {
      catalog.value = []
    })
})
</script>

<template>
  <main class="page">
    <PageHeader
      title="产业研究 / 板块分析"
      :subtitle="board ? `${board.name} · 东财${levelLabel(board.kind, board.level)}${board.kind === 'concept' ? '板块' : '行业'} · ${board.code}` : props.code"
    >
      <template #actions>
        <RouterLink class="back" to="/industry">‹ 行业总览</RouterLink>
        <el-select :model-value="props.code" class="board-select" filterable placeholder="切换行业 / 概念" @change="switchBoard">
          <el-option-group v-for="group in catalogGroups" :key="group.label" :label="group.label">
            <el-option v-for="item in group.items" :key="item.code" :label="item.name" :value="item.code" />
          </el-option-group>
        </el-select>
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
      </template>
      <template #tabs>
        <SectionTabs v-model="tab" :tabs="tabs" />
      </template>
    </PageHeader>

    <StateBlock v-if="phase === 'loading' && !detail" state="loading" :rows="6" />
    <template v-else-if="phase === 'error' && !detail">
      <StateBlock state="error" :message="`板块行情暂不可用：${failure}。研报和研究假设不依赖行情接口，仍可查看。`" @retry="load" />
      <BoardThesesPanel v-if="tab === 'theses'" :code="props.code" class="section" />
      <BoardReportsPanel v-else :reports="reports" :section="reportSection" class="section" />
    </template>

    <template v-else-if="detail && board">
      <p v-if="phase === 'error'" class="notice danger" role="alert">{{ failure }}（下方为上次成功加载的数据）</p>
      <p v-if="detail.quoteSection.status === 'stale'" class="notice warn" role="status">{{ detail.quoteSection.reason }}</p>
      <p v-else-if="detail.quoteSection.origin === 'stored'" class="notice" role="status">
        行情为 {{ detail.quoteSection.dataDate }} 收盘后入库的快照，不是实时报价。
      </p>

      <div class="grid-4 section">
        <PanelCard class="hero" compact>
          <div class="hero-body">
            <span class="hero-mark" aria-hidden="true">{{ board.name.slice(0, 1) }}</span>
            <div class="hero-text">
              <h2>{{ board.name }}</h2>
              <p class="hero-price">
                <span class="tabular">{{ board.last?.toFixed(2) ?? "—" }}</span>
                <DeltaValue :value="board.changePct" suffix="%" size="md" />
              </p>
              <p class="muted hero-desc">
                成交 {{ yuan(board.amount) }}元 · 换手 {{ board.turnoverPct?.toFixed(2) ?? "—" }}% · 市值 {{ yuan(board.marketCap) }}元
              </p>
              <p v-if="board.leaderName" class="muted hero-desc">领涨 {{ board.leaderName }} {{ pctText(board.leaderChangePct) }}</p>
            </div>
          </div>
        </PanelCard>
        <StatCard
          title="区间表现"
          :value="trend?.returns.d20 != null ? pctText(trend.returns.d20) : null"
          :caption="trend ? `20日 · 60日 ${pctText(trend.returns.d60)} · 一年 ${pctText(trend.returns.d250)}` : ''"
          info="按板块日线收盘价计算的区间涨跌幅"
          tone="accent"
          more-text=""
        >
          <template #icon><DataLine /></template>
        </StatCard>
        <StatCard
          :title="`相对${trend?.benchmark ?? '沪深300'}超额`"
          :value="trend?.returns.excessD60 != null ? `${trend.returns.excessD60 > 0 ? '+' : ''}${trend.returns.excessD60.toFixed(2)}` : null"
          unit="pct"
          :caption="trend ? `60日 · 20日 ${trend.returns.excessD20?.toFixed(2) ?? '—'} · 一年 ${trend.returns.excessD250?.toFixed(2) ?? '—'}` : ''"
          info="板块区间涨跌幅 − 沪深300 同期涨跌幅，单位百分点"
          tone="orange"
          more-text=""
        >
          <template #icon><Histogram /></template>
        </StatCard>
        <StatCard
          title="估值分位"
          :value="valuation?.peTtm ? valuation.peTtm.percentile.toFixed(0) : board.peTtm !== null ? board.peTtm.toFixed(1) : null"
          :unit="valuation?.peTtm ? '%' : '倍'"
          :caption="
            valuation?.peTtm
              ? `PE(TTM) ${valuation.peTtm.current.toFixed(1)} · 近${valuation.windowYears}年${percentileWord(valuation.peTtm.percentile)}`
              : `当前市盈率；${valuation?.section.reason || '暂无历史估值'}`
          "
          info="当前 PE(TTM) 在近 5 年交易日中的分位，越低越便宜；行业整体亏损的日子不参与"
          :tone="valuation?.peTtm ? (percentileTone(valuation.peTtm.percentile) === 'ok' ? 'ok' : percentileTone(valuation.peTtm.percentile) === 'danger' ? 'danger' : 'warn') : 'violet'"
          more-text=""
        >
          <template #icon><PieChart /></template>
        </StatCard>
      </div>

      <template v-if="tab === 'overview'">
        <div class="grid-2-1 section">
          <PanelCard title="走势与均线" info="板块日K（不复权）+ 20 / 60 日均线，下方为成交额；默认显示近半年，可拖动缩放">
            <template #actions>
              <StatusPill v-if="trendBadge" :text="trendBadge.text" :tone="trendBadge.tone" />
            </template>
            <VChart v-if="trend?.bars.length" class="kline" :option="klineOption" autoresize />
            <StateBlock v-else state="empty" :message="trend?.section.reason || '暂无日线'" />
          </PanelCard>
          <div class="stack">
            <PanelCard title="成分股涨跌分布" :info="`共 ${members?.total ?? 0} 只成分，停牌或无报价 ${members?.suspended ?? 0} 只`">
              <VChart v-if="members?.distribution.length" class="dist" :option="distributionOption" autoresize />
              <StateBlock v-else state="empty" :message="members?.section.reason || '暂无成分股行情'" />
            </PanelCard>
            <PanelCard title="资金与活跃度">
              <template #icon><Coin /></template>
              <dl class="kv">
                <div><dt>今日主力净流入</dt><dd :class="(board.mainNetInflow ?? 0) >= 0 ? 'up' : 'down'">{{ yuan(board.mainNetInflow) }}元</dd></div>
                <div><dt>5日主力净流入</dt><dd :class="(board.mainNetInflow5d ?? 0) >= 0 ? 'up' : 'down'">{{ yuan(board.mainNetInflow5d) }}元</dd></div>
                <div><dt>上涨 / 下跌 / 平盘</dt><dd class="tabular">{{ board.advancers ?? "—" }} / {{ board.decliners ?? "—" }} / {{ board.unchanged ?? "—" }}</dd></div>
                <div><dt>5日 / 10日涨跌</dt><dd class="tabular">{{ pctText(board.change5dPct) }} / {{ pctText(board.change10dPct) }}</dd></div>
                <div><dt>年初至今</dt><dd><DeltaValue :value="board.changeYtdPct" suffix="%" size="sm" /></dd></div>
                <div><dt>最近收盘</dt><dd class="tabular">{{ latestBar?.date ?? "—" }}</dd></div>
              </dl>
            </PanelCard>
          </div>
        </div>

        <div class="grid-2 section">
          <PanelCard :title="`相对${trend?.benchmark ?? '沪深300'}强弱`" info="近一年板块与沪深300各自以首日为 100，橙色为超额（右轴，百分点）">
            <VChart v-if="trend?.relative.length" class="chart" :option="relativeOption" autoresize />
            <StateBlock v-else state="empty" message="基准或板块日线不可用，暂时无法比较" />
          </PanelCard>
          <PanelCard :title="compositionTitle" :info="members?.subIndustries.length ? '按成分股所属二级行业聚合：面积 = 总市值，颜色 = 市值加权涨跌幅；点击进入子行业' : '市值前 60 的成分股：面积 = 总市值，颜色 = 今日涨跌'">
            <VChart v-if="members?.members.length" class="chart" :option="compositionOption" autoresize @click="onCompositionClick" />
            <StateBlock v-else state="empty" :message="members?.section.reason || '暂无成分股'" />
          </PanelCard>
        </div>

        <div class="grid-2 section">
          <PanelCard title="估值 × 动量" info="横轴为成分股动态市盈率（对数，剔除亏损与 300 倍以上），纵轴 60 日涨跌；左上角是相对便宜且走强的公司">
            <VChart v-if="members?.members.length" class="chart" :option="bubbleOption" autoresize />
            <StateBlock v-else state="empty" :message="members?.section.reason || '暂无成分股'" />
          </PanelCard>
          <PanelCard title="成分股异动" flush>
            <template #actions>
              <el-radio-group v-model="leaderTab" size="small">
                <el-radio-button value="gainers">领涨</el-radio-button>
                <el-radio-button value="losers">领跌</el-radio-button>
                <el-radio-button value="inflows">主力流入</el-radio-button>
              </el-radio-group>
            </template>
            <StateBlock v-if="!leaderRows.length" state="empty" :message="members?.section.reason || '暂无成分股'" />
            <ul v-else class="mover-list">
              <li v-for="row in leaderRows" :key="row.code">
                <div>
                  <RouterLink v-if="companySymbol(row.code, row.exchange)" class="name-link" :to="`/company/${companySymbol(row.code, row.exchange)}`">{{ row.name }}</RouterLink>
                  <span v-else class="name-link">{{ row.name }}</span>
                  <small class="muted tabular">{{ row.code }}</small>
                  <span v-if="row.subIndustry" class="chip">{{ row.subIndustry }}</span>
                </div>
                <span class="tabular muted small">主力 {{ yuan(row.mainNetInflow) }}</span>
                <DeltaValue :value="row.changePct" suffix="%" size="sm" />
              </li>
            </ul>
          </PanelCard>
        </div>
      </template>

      <PanelCard v-else-if="tab === 'members'" title="成分股" :info="`共 ${members?.total ?? 0} 只；点表头排序`" flush class="section">
        <template #actions>
          <el-input v-model="memberKeyword" size="small" clearable placeholder="名称 / 代码 / 子行业" class="search" @input="memberPage = 1" />
        </template>
        <StateBlock v-if="!members?.members.length" state="empty" :message="members?.section.reason || '暂无成分股'" />
        <div v-else class="table-wrap">
          <table class="rank">
            <thead>
              <tr>
                <th class="left">公司</th>
                <th class="left">子行业</th>
                <th>最新价</th>
                <th @click="sortMembers('changePct')">今日 {{ mark("changePct") }}</th>
                <th @click="sortMembers('change5dPct')">5日 {{ mark("change5dPct") }}</th>
                <th @click="sortMembers('change60dPct')">60日 {{ mark("change60dPct") }}</th>
                <th @click="sortMembers('changeYtdPct')">年初至今 {{ mark("changeYtdPct") }}</th>
                <th @click="sortMembers('mainNetInflow')">主力净流入 {{ mark("mainNetInflow") }}</th>
                <th @click="sortMembers('amount')">成交额 {{ mark("amount") }}</th>
                <th @click="sortMembers('marketCap')">总市值 {{ mark("marketCap") }}</th>
                <th @click="sortMembers('peDynamic')">市盈率(动) {{ mark("peDynamic") }}</th>
                <th @click="sortMembers('pb')">市净率 {{ mark("pb") }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in pagedMembers" :key="row.code">
                <td class="left">
                  <RouterLink v-if="companySymbol(row.code, row.exchange)" class="name-link" :to="`/company/${companySymbol(row.code, row.exchange)}`">{{ row.name }}</RouterLink>
                  <span v-else class="name-link">{{ row.name }}</span>
                  <small class="muted tabular"> {{ row.code }}</small>
                </td>
                <td class="left muted">{{ row.subIndustry || "—" }}</td>
                <td class="tabular">{{ row.last?.toFixed(2) ?? "—" }}</td>
                <td><DeltaValue :value="row.changePct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.change5dPct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.change60dPct" suffix="%" size="sm" /></td>
                <td><DeltaValue :value="row.changeYtdPct" suffix="%" size="sm" /></td>
                <td :class="(row.mainNetInflow ?? 0) >= 0 ? 'up' : 'down'" class="tabular">{{ yuan(row.mainNetInflow) }}</td>
                <td class="tabular">{{ yuan(row.amount) }}</td>
                <td class="tabular">{{ yuan(row.marketCap) }}</td>
                <td class="tabular">{{ row.peDynamic?.toFixed(1) ?? "—" }}</td>
                <td class="tabular">{{ row.pb?.toFixed(2) ?? "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="sortedMembers.length > MEMBER_PAGE" class="pager">
          <el-pagination v-model:current-page="memberPage" :page-size="MEMBER_PAGE" :total="sortedMembers.length" layout="total, prev, pager, next" small background />
        </div>
      </PanelCard>

      <template v-else-if="tab === 'valuation'">
        <template v-if="valuation?.section.status !== 'unavailable'">
          <div class="grid-2 section">
            <PanelCard v-for="item in [{ key: 'pe', label: 'PE(TTM)', data: valuation?.peTtm }, { key: 'pb', label: 'PB(MRQ)', data: valuation?.pbMrq }]" :key="item.key" :title="`${item.label} 历史分位`" compact>
              <template v-if="item.data">
                <div class="pct-row">
                  <span class="pct-num tabular">{{ item.data.percentile.toFixed(1) }}<small>%</small></span>
                  <StatusPill :text="percentileWord(item.data.percentile)" :tone="percentileTone(item.data.percentile)" />
                  <span class="muted small">当前 {{ item.data.current.toFixed(2) }} · 样本 {{ item.data.samples }} 天（{{ item.data.start }} 起）</span>
                </div>
                <div class="pct-track">
                  <i :style="{ left: `${item.data.percentile}%` }" />
                </div>
                <p class="pct-scale muted tabular">
                  <span>最低 {{ item.data.minimum.toFixed(1) }}</span>
                  <span>20% {{ item.data.p20.toFixed(1) }}</span>
                  <span>中位 {{ item.data.median.toFixed(1) }}</span>
                  <span>80% {{ item.data.p80.toFixed(1) }}</span>
                  <span>最高 {{ item.data.maximum.toFixed(1) }}</span>
                </p>
              </template>
              <StateBlock v-else state="empty" message="有效样本不足 20 天，暂不计算分位" />
            </PanelCard>
          </div>
          <div class="grid-2 section">
            <PanelCard title="PE(TTM) 估值通道" :info="`近 ${valuation?.windowYears ?? 5} 年每日行业 PE(TTM)，虚线为 20% / 50% / 80% 分位`">
              <VChart class="chart" :option="peBand" autoresize />
            </PanelCard>
            <PanelCard title="PB(MRQ) 估值通道" :info="`近 ${valuation?.windowYears ?? 5} 年每日行业市净率`">
              <VChart class="chart" :option="pbBand" autoresize />
            </PanelCard>
          </div>
          <p v-if="valuation?.lossMembers != null" class="muted small section">
            最新一期 {{ valuation.members ?? "—" }} 家成分中有 {{ valuation.lossMembers }} 家亏损（TTM）。
          </p>
        </template>
        <PanelCard v-else title="估值分位" class="section">
          <StateBlock state="empty" :message="valuation?.section.reason || '暂无估值序列'" />
          <div v-if="members?.subIndustries.some((item) => item.code)" class="sub-links">
            <p class="muted">可查看估值分位的二级子行业：</p>
            <RouterLink v-for="item in members.subIndustries.filter((s) => s.code)" :key="item.code" :to="`/industry/board/${item.code}`" class="chip link">
              {{ item.name }}
            </RouterLink>
          </div>
        </PanelCard>
      </template>

      <BoardThesesPanel v-else-if="tab === 'theses'" :code="props.code" class="section" />

      <BoardReportsPanel v-else :reports="reports" :section="reportSection" class="section" />

      <PanelCard title="口径说明" class="section">
        <ul class="notes">
          <li v-for="rule in detail.rules" :key="rule">{{ rule }}</li>
          <li>计算口径版本：{{ detail.analyticsVersion }}</li>
        </ul>
      </PanelCard>

      <SourceFooter
        :as-of="shortTime(detail.quoteSection.fetchedAt)"
        :sources="detail.sources.map((item) => item.name)"
        latency="行情约 15 秒延迟；日线缓存 30 分钟、估值缓存 6 小时"
        :stale="detail.quoteSection.status === 'stale'"
        note="板块数据为东方财富公开行情；收盘后快照与日线会入库，盘后优先读库、上游失败时回退到库；均线、超额与分位由后端计算"
      />
    </template>
  </main>
</template>

<style scoped>
.section {
  margin-bottom: var(--gap);
}

.back {
  font-size: 13px;
  color: var(--muted);
  text-decoration: none;
  margin-right: 4px;
}

.board-select {
  width: 200px;
}

.notice {
  margin: 0 0 12px;
  font-size: 13px;
}

.notice.warn {
  color: var(--warn);
}

.notice.danger {
  color: var(--danger);
}

.hero-body {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.hero-mark {
  flex: none;
  width: 52px;
  height: 52px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 12px;
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 40%, transparent), var(--panel-deep));
  border: 1px solid var(--edge-light);
  color: var(--text-strong);
  font-size: 22px;
  font-weight: 700;
}

.hero-text h2 {
  margin: 0 0 4px;
  font-size: 20px;
  color: var(--text-strong);
}

.hero-price {
  margin: 0 0 4px;
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 22px;
  font-weight: 600;
  color: var(--text-strong);
  font-family: var(--font-num);
}

.hero-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
}

.kline {
  width: 100%;
  height: 430px;
}

.dist {
  width: 100%;
  height: 190px;
}

.chart {
  width: 100%;
  height: 330px;
}

.kv {
  margin: 0;
  display: grid;
  gap: 8px;
}

.kv div {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
}

.kv dt {
  color: var(--muted);
}

.kv dd {
  margin: 0;
  color: var(--text-strong);
  font-family: var(--font-num);
}

.up {
  color: var(--rise) !important;
}

.down {
  color: var(--fall) !important;
}

.mover-list {
  list-style: none;
  margin: 0;
  padding: 0 16px;
}

.mover-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 80px;
  align-items: center;
  gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid var(--line-soft);
  font-size: 13px;
}

.name-link {
  color: var(--text-strong);
  font-weight: 600;
  text-decoration: none;
  margin-right: 6px;
}

a.name-link:hover {
  color: var(--accent);
}

.chip {
  margin-left: 6px;
  font-size: 11px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 4px;
  background: var(--panel-raise);
  color: var(--muted);
  white-space: nowrap;
}

.chip.link {
  text-decoration: none;
  display: inline-block;
  margin: 0 6px 6px 0;
  padding: 2px 8px;
}

.chip.link:hover {
  color: var(--accent);
}

.small {
  font-size: 12px;
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
}

.rank .left {
  text-align: left;
}

.rank tbody tr:hover {
  background: var(--panel-soft);
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 10px 16px 4px;
}

.pct-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.pct-num {
  font-size: 32px;
  font-weight: 600;
  color: var(--text-strong);
  font-family: var(--font-num);
}

.pct-num small {
  font-size: 14px;
  color: var(--muted);
}

.pct-track {
  position: relative;
  height: 8px;
  margin: 12px 0 6px;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--fall), var(--warn), var(--rise));
  opacity: 0.85;
}

.pct-track i {
  position: absolute;
  top: -4px;
  width: 4px;
  height: 16px;
  margin-left: -2px;
  border-radius: 2px;
  background: var(--text-strong);
  box-shadow: 0 0 0 2px var(--panel);
}

.pct-scale {
  margin: 0;
  display: flex;
  justify-content: space-between;
  font-size: 11px;
}

.sub-links {
  margin-top: 12px;
}

.sub-links p {
  margin: 0 0 8px;
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
