<script setup lang="ts">
/**
 * 总览驾驶舱：每天的市场大况。
 *
 * 六个模块各自请求 /api/v1/dashboard/*，互不阻塞：某个上游失败只让该模块显示不可用 / 缓存。
 * 温度、流动性、热度指数、强弱分组等所有数字都由后端规则函数算好，这里只做布局与可视化；
 * 点击 KPI 卡可以看到逐因子的原始数据、换算公式和贡献。
 */
import { computed, onMounted, ref } from "vue"
import { Aim, Histogram, Odometer, Sunny } from "@element-plus/icons-vue"
import { VChart } from "../charts"
import {
  fetchAnnouncements,
  fetchBoards,
  fetchMacro,
  fetchMarket,
  fetchStocks,
  fetchWatch,
  type AnnouncementsOverview,
  type BoardsOverview,
  type MacroOverview,
  type MarketOverview,
  type StocksOverview,
  type WatchOverview,
} from "../api/dashboard"
import type { SourceRef } from "../api/industryMarket"
import { formatMoment } from "../api/json"
import DeltaValue from "../components/ui/DeltaValue.vue"
import MiniRing from "../components/ui/MiniRing.vue"
import MiniSpark from "../components/ui/MiniSpark.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import SourceFooter from "../components/ui/SourceFooter.vue"
import StatCard from "../components/ui/StatCard.vue"
import { restoreSession, useSessionStore } from "../stores/auth"
import AnnouncementsPanel from "./parts/dashboard/AnnouncementsPanel.vue"
import BoardsPanel from "./parts/dashboard/BoardsPanel.vue"
import FactorTable from "./parts/dashboard/FactorTable.vue"
import IndexPanel from "./parts/dashboard/IndexPanel.vue"
import MacroPanel from "./parts/dashboard/MacroPanel.vue"
import StocksPanel from "./parts/dashboard/StocksPanel.vue"
import WatchPanel from "./parts/dashboard/WatchPanel.vue"
import {
  amountOption,
  chinaRatesOption,
  distributionOption,
  dollarYieldOption,
  heatBadge,
  netLiquidityOption,
} from "./parts/dashboard/charts"
import { yuan } from "./parts/industry/chartKit"

interface Slot<T> {
  data: T | null
  loading: boolean
  error: string
}

function slot<T>() {
  return ref<Slot<T>>({ data: null, loading: false, error: "" })
}

const session = useSessionStore()
const market = slot<MarketOverview>()
const macro = slot<MacroOverview>()
const boards = slot<BoardsOverview>()
const stocks = slot<StocksOverview>()
const announcements = slot<AnnouncementsOverview>()
const watch = slot<WatchOverview>()
const dialog = ref<"" | "temperature" | "macro" | "boards">("")

async function fill<T>(target: { value: Slot<T> }, loader: () => Promise<T>): Promise<void> {
  target.value = { ...target.value, loading: true, error: "" }
  try {
    target.value = { data: await loader(), loading: false, error: "" }
  } catch (error) {
    target.value = { ...target.value, loading: false, error: error instanceof Error ? error.message : "暂时不可用" }
  }
}

async function refreshToken(): Promise<string | null> {
  return (await restoreSession()) ? session.accessToken : null
}

const signedIn = computed(() => Boolean(session.accessToken))

function loadWatch(groupId = watch.value.data?.groupId ?? ""): Promise<void> {
  if (!signedIn.value) {
    return Promise.resolve()
  }
  return fill(watch, () => fetchWatch(groupId, session.accessToken, refreshToken))
}

const loadMarket = () => fill(market, fetchMarket)
const loadMacro = () => fill(macro, fetchMacro)
const loadBoards = () => fill(boards, fetchBoards)
const loadStocks = () => fill(stocks, fetchStocks)
const loadAnnouncements = () => fill(announcements, fetchAnnouncements)

const busy = computed(() =>
  [market, macro, boards, stocks, announcements, watch].some((item) => item.value.loading),
)

function loadAll(): void {
  void loadMarket()
  void loadMacro()
  void loadBoards()
  void loadStocks()
  void loadAnnouncements()
  void loadWatch()
}

onMounted(loadAll)

/* ---------------- KPI：全球宏观状态 ---------------- */
const liquidity = computed(() => macro.value.data?.liquidity ?? null)
const growth = computed(() => macro.value.data?.growth ?? null)
const netSpark = computed(() => (macro.value.data?.netLiquidity ?? []).slice(-26).map((point) => point.value))
/** 偏离中性 50 最多的两个流动性因子，作为卡片上的「主要驱动」。 */
const liquidityDrivers = computed(() =>
  [...(liquidity.value?.factors ?? [])]
    .sort((left, right) => Math.abs(right.score - 50) - Math.abs(left.score - 50))
    .slice(0, 2)
    .map((factor) => `${factor.label.replace(/\s*\d+\s*(期|周|日)(均值)?变化$/, "")}${factor.score >= 50 ? "偏松" : "偏紧"}（${factor.score.toFixed(0)}）`),
)
const macroCaption = computed(() => {
  if (macro.value.error && !macro.value.data) {
    return macro.value.error
  }
  if (!macro.value.data) {
    return "正在拉取 FRED 与利率数据…"
  }
  return liquidityDrivers.value.length ? `主要驱动：${liquidityDrivers.value.join("；")}` : "流动性因子不足"
})

function scoreTone(score: number | null, high = 55, low = 45): string {
  if (score === null) {
    return "var(--muted)"
  }
  return score >= high ? "var(--ok)" : score < low ? "var(--warn)" : "var(--cyan)"
}

/* ---------------- KPI：A股市场温度 ---------------- */
const temperature = computed(() => market.value.data?.temperature ?? null)
const temperatureFactors = computed(() => {
  const byCode = new Map((temperature.value?.factors ?? []).map((factor) => [factor.code, factor]))
  return [
    { code: "trend", label: "趋势", weight: "30%" },
    { code: "breadth", label: "广度", weight: "35%" },
    { code: "activity", label: "活跃度", weight: "20%" },
    { code: "volatility", label: "波动", weight: "15%" },
  ].map((item) => ({ ...item, score: byCode.get(item.code)?.score ?? null }))
})
const temperatureCaption = computed(() => {
  if (market.value.error && !market.value.data) {
    return market.value.error
  }
  const data = market.value.data
  if (!data) {
    return "正在汇总全市场涨跌分布与成交额…"
  }
  const breadth = data.breadth
  const parts = [
    breadth ? `全市场 ${breadth.total} 只：涨 ${breadth.up} / 跌 ${breadth.down}` : "涨跌分布暂不可用",
    `两市成交 ${yuan(data.marketAmount)}`,
  ]
  return parts.join("，")
})

function factorColor(score: number | null): string {
  if (score === null) {
    return "var(--faint)"
  }
  return score >= 55 ? "var(--rise)" : score < 45 ? "var(--fall)" : "var(--text)"
}

/* ---------------- KPI：热点板块数 ---------------- */
const hotBoards = computed(() => (boards.value.data?.boards ?? []).filter((row) => row.hot))
const boardBars = computed(() =>
  (boards.value.data?.boards ?? []).slice(0, 12).map((row) => row.index),
)
const boardCaption = computed(() => {
  if (boards.value.error && !boards.value.data) {
    return boards.value.error
  }
  if (!boards.value.data) {
    return "正在读取同花顺热门板块…"
  }
  if (boards.value.data.hotCount === null) {
    return boards.value.data.heatSection.reason || "热门板块暂不可用"
  }
  const top = hotBoards.value.slice(0, 3).map((row) => row.name)
  return top.length ? `热度 ≥ 60 且涨幅 ≥ 1%：${top.join("、")}` : "今天没有同时满足热度 ≥ 60 与涨幅 ≥ 1% 的板块"
})

/* ---------------- KPI：重点关注股票 ---------------- */
const watchData = computed(() => watch.value.data)
const watchSegments = computed(() => {
  const data = watchData.value
  if (!data) {
    return []
  }
  return [
    { label: `强势 ${data.strong}`, ratio: data.strong, tone: "up" as const },
    { label: `震荡 ${data.neutral}`, ratio: data.neutral, tone: "accent" as const },
    { label: `弱势 ${data.weak}`, ratio: data.weak, tone: "down" as const },
    ...(data.unknown ? [{ label: `无数据 ${data.unknown}`, ratio: data.unknown, tone: "muted" as const }] : []),
  ]
})
const watchCaption = computed(() => {
  if (!signedIn.value) {
    return "登录后读取你在「自选关注」里添加的股票"
  }
  if (watch.value.error && !watchData.value) {
    return watch.value.error
  }
  if (!watchData.value) {
    return "正在读取自选分组…"
  }
  if (!watchData.value.total) {
    return "当前分组还没有股票，点右上角去添加"
  }
  return `分组「${watchData.value.groupName}」，按近 5 日涨跌幅分组`
})

/* ---------------- 页脚：数据来源与截至时间 ---------------- */
const allSources = computed<SourceRef[]>(() => [
  ...(market.value.data?.sources ?? []),
  ...(macro.value.data?.sources ?? []),
  ...(boards.value.data?.sources ?? []),
  ...(stocks.value.data?.sources ?? []),
  ...(announcements.value.data?.sources ?? []),
  ...(watch.value.data?.sources ?? []),
])
const sourceNames = computed(() => [...new Set(allSources.value.map((item) => item.name.split(/\s/)[0] ?? item.name))])
const latestFetch = computed(() => {
  const stamps = allSources.value.map((item) => item.fetchedAt).filter(Boolean).sort()
  return stamps.length ? formatMoment(stamps[stamps.length - 1]) : ""
})
const anyStale = computed(() => {
  const sections = [
    market.value.data?.quotesSection,
    market.value.data?.breadthSection,
    market.value.data?.historySection,
    macro.value.data?.usSection,
    macro.value.data?.chinaSection,
    boards.value.data?.heatSection,
    stocks.value.data?.hotSection,
    announcements.value.data?.section,
  ]
  return sections.some((section) => section && section.status !== "ok")
})
const tradeDate = computed(() => market.value.data?.tradeDate ?? "")
</script>

<template>
  <div class="page">
    <PageHeader title="智能投研驾驶舱" subtitle="每天的市场大况：全球流动性 · A股温度 · 指数 · 热点 · 自选 · 公告">
      <template #actions>
        <span v-if="tradeDate" class="trade-date">A股交易日 <b class="num">{{ tradeDate }}</b></span>
        <el-button :loading="busy" @click="loadAll">刷新数据</el-button>
      </template>
    </PageHeader>

      <div class="grid-4">
        <StatCard
        class="clickable"
        title="全球宏观状态"
        tone="cyan"
        :value="liquidity?.label ?? null"
        :unit="liquidity?.score === null || liquidity?.score === undefined ? '' : ` ${liquidity.score.toFixed(1)}`"
        :caption="macroCaption"
        info="流动性 global-liquidity-v1（越高越松）+ 经济热度 global-growth-v1，宏观趋势 = 经济热度 × CPI 同比动能；点击查看逐项计算"
        @click="dialog = 'macro'"
      >
        <template #icon><el-icon><Odometer /></el-icon></template>
        <template #chart>
          <MiniSpark v-if="netSpark.length > 1" :points="netSpark" kind="area" tone="accent" :width="96" :height="40" />
        </template>
        <template #below>
          <p v-if="macro.data" class="kv">
            <span>全球宏观趋势</span>
            <b :style="{ color: scoreTone(growth?.score ?? null) }">{{ macro.data.regime }}</b>
            <span class="faint">经济热度 {{ growth?.score?.toFixed(1) ?? "—" }}（{{ growth?.label }}）</span>
          </p>
          <p class="hint">点击查看计算依据 ›</p>
        </template>
        </StatCard>

        <StatCard
        class="clickable"
          title="A股市场温度"
        tone="orange"
          :value="temperature?.score === null || temperature?.score === undefined ? null : temperature.score.toFixed(1)"
          unit="/100"
        :caption="temperatureCaption"
        info="market-temperature-v2：趋势 30% + 广度 35% + 活跃度 20% + 波动 15%，缺失因子按权重重新归一；点击查看因子原始数据与公式"
        @click="dialog = 'temperature'"
        >
        <template #icon><el-icon><Sunny /></el-icon></template>
          <template #chart>
          <div v-if="temperature" class="temp-label">
            <b>{{ temperature.label }}</b>
            <span v-if="temperature.change !== null" class="faint">
              较 {{ temperature.previousDate.slice(5) }} <DeltaValue :value="temperature.change" :digits="1" size="sm" />
            </span>
          </div>
        </template>
        <template #below>
          <div class="tempbar"><i v-if="temperature?.score !== null && temperature?.score !== undefined" :style="{ left: `${temperature.score}%` }" /></div>
          <p class="factor-line">
            <span v-for="item in temperatureFactors" :key="item.code">
              {{ item.label }} {{ item.weight }}
              <b class="num" :style="{ color: factorColor(item.score) }">{{ item.score === null ? "缺" : item.score.toFixed(0) }}</b>
            </span>
          </p>
          </template>
        </StatCard>

        <StatCard
        class="clickable"
        title="热点板块数"
        tone="orange"
        :value="boards.data?.hotCount ?? null"
          unit="个"
        :caption="boardCaption"
        info="board-heat-v1 热度指数 ≥ 60 且当日涨幅 ≥ 1% 的板块；点击查看候选板块逐项得分"
        @click="dialog = 'boards'"
      >
        <template #icon><el-icon><Histogram /></el-icon></template>
        <template #chart>
          <MiniSpark v-if="boardBars.length > 1" :points="boardBars" kind="bar" tone="up" :width="110" :height="40" />
        </template>
        <template #below>
          <p class="hint">
            <template v-if="boards.data?.hotCountChange !== null && boards.data?.hotCountChange !== undefined">
              较 {{ boards.data.previousDate.slice(5) }}
              <DeltaValue :value="boards.data.hotCountChange" :digits="0" signed size="sm" /> 个
            </template>
            <template v-else-if="boards.data">较上一交易日：尚无入库记录，明天起可对比</template>
          </p>
        </template>
        </StatCard>

        <StatCard
        title="重点关注股票"
        tone="accent"
        :value="signedIn ? (watchData?.total ?? null) : null"
        unit="只"
        :caption="watchCaption"
        info="按近 5 个交易日涨跌幅分组：≥ +3% 强势、≤ −3% 弱势、其余震荡；缺日线的单列「无数据」"
        more-text="管理自选"
        more-to="/watchlist"
      >
        <template #icon><el-icon><Aim /></el-icon></template>
        <template #chart>
          <MiniRing
            v-if="watchData && watchData.total"
            :segments="watchSegments"
            :size="72"
            :thickness="9"
            :center-text="String(watchData.total)"
            show-legend
          />
        </template>
        </StatCard>
      </div>

      <div class="grid-2-1 section">
      <div class="stack">
        <IndexPanel :market="market.data" :loading="market.loading" :error="market.error" @retry="loadMarket" />
        <div class="grid-2">
          <BoardsPanel :boards="boards.data" :loading="boards.loading" :error="boards.error" @retry="loadBoards" @basis="dialog = 'boards'" />
          <StocksPanel :stocks="stocks.data" :loading="stocks.loading" :error="stocks.error" @retry="loadStocks" />
        </div>
      </div>
      <div class="stack">
        <MacroPanel :macro="macro.data" :loading="macro.loading" :error="macro.error" @retry="loadMacro" @basis="dialog = 'macro'" />
        <WatchPanel
          :watch="watch.data"
          :signed-in="signedIn"
          :loading="watch.loading"
          :error="watch.error"
          @retry="loadWatch()"
          @group="loadWatch"
        />
        <AnnouncementsPanel
          :announcements="announcements.data"
          :loading="announcements.loading"
          :error="announcements.error"
          @retry="loadAnnouncements"
        />
            </div>
          </div>

    <SourceFooter
      :as-of="latestFetch"
      :sources="sourceNames"
      :stale="anyStale"
      note="规则计算的描述性指标，不构成投资建议；月度宏观数据有发布滞后"
    />

    <el-dialog :model-value="dialog === 'temperature'" width="1100px" append-to-body @close="dialog = ''">
      <template #header>
        <h2 class="dialog-title">A股市场温度 {{ temperature?.score?.toFixed(1) ?? "—" }} · {{ temperature?.label }}</h2>
          </template>
      <template v-if="market.data && temperature">
        <p class="muted">交易日 {{ market.data.tradeDate || "—" }} · 口径 {{ temperature.method }} · 可用权重 {{ (temperature.coverage * 100).toFixed(0) }}% · 描述性温度，不是收益预测</p>
        <pre class="formula">{{ market.data.rules.join("\n") }}<template v-if="temperature.missing.length">{{ `\n缺失：${temperature.missing.join("；")}` }}</template></pre>
        <FactorTable :factors="temperature.factors" />
        <div class="dialog-charts">
          <div class="dialog-chart">
            <VChart v-if="market.data.breadth" :option="distributionOption(market.data.breadth.buckets)" autoresize />
            <p v-else class="faint pad">{{ market.data.breadthSection.reason || "涨跌分布不可用" }}</p>
      </div>
          <div class="dialog-chart">
            <VChart v-if="market.data.amounts.length" :option="amountOption(market.data.amounts)" autoresize />
            <p v-else class="faint pad">{{ market.data.historySection.reason || "成交额历史不可用" }}</p>
          </div>
        </div>
      </template>
      <p v-else class="faint">{{ market.error || "温度数据还在加载" }}</p>
    </el-dialog>

    <el-dialog :model-value="dialog === 'macro'" width="1100px" append-to-body @close="dialog = ''">
      <template #header>
        <h2 class="dialog-title">
          全球宏观状态：流动性 {{ liquidity?.score?.toFixed(1) ?? "—" }}（{{ liquidity?.label }}）· 经济热度 {{ growth?.score?.toFixed(1) ?? "—" }}（{{ growth?.label }}）· 趋势「{{ macro.data?.regime ?? "—" }}」
        </h2>
      </template>
      <template v-if="macro.data && liquidity && growth">
        <p class="muted">
          口径 {{ liquidity.method }} / {{ growth.method }} · 美国 CPI 同比 {{ macro.data.cpiYoy?.toFixed(2) ?? "—" }}%，较 3 个月前
          <DeltaValue :value="macro.data.inflationMomentum" suffix=" pp" :colored="false" signed size="sm" /> · 规则计算，不是经济定论
        </p>
        <pre class="formula">{{ macro.data.rules.join("\n") }}<template v-if="liquidity.missing.length || growth.missing.length">{{ `\n缺失：${[...liquidity.missing, ...growth.missing].join("；")}` }}</template></pre>
        <h3 class="sub-title">流动性因子</h3>
        <FactorTable :factors="liquidity.factors" />
        <h3 class="sub-title">经济热度因子</h3>
        <FactorTable :factors="growth.factors" />
        <div class="dialog-charts three">
          <div class="dialog-chart"><VChart :option="netLiquidityOption(macro.data.netLiquidity)" autoresize /></div>
          <div class="dialog-chart"><VChart :option="dollarYieldOption(macro.data.dollar, macro.data.realYield)" autoresize /></div>
          <div class="dialog-chart"><VChart :option="chinaRatesOption(macro.data.shiborOn, macro.data.cnUsSpread)" autoresize /></div>
          </div>
          </template>
      <p v-else class="faint">{{ macro.error || "宏观数据还在加载" }}</p>
    </el-dialog>

    <el-dialog :model-value="dialog === 'boards'" width="1000px" append-to-body @close="dialog = ''">
      <template #header>
        <h2 class="dialog-title">热点板块口径 board-heat-v1 · 今日 {{ boards.data?.hotCount ?? "—" }} 个</h2>
    </template>
      <template v-if="boards.data">
        <pre class="formula">{{ boards.data.rules.join("\n") }}
热度分 = 板块同花顺热度 / 当日榜首热度 × 100；涨幅分位 = 当日涨幅在候选板块中的百分位 × 100；涨停分 = min(涨停家数 / 10, 1) × 100</pre>
        <table class="basis-table">
          <thead>
            <tr><th>板块</th><th>类型</th><th class="r">热度分</th><th class="r">涨幅分位</th><th class="r">涨停分</th><th class="r">热度指数</th><th class="r">涨幅</th><th>热点</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in boards.data.boards" :key="row.code">
              <td>{{ row.name }}</td>
              <td class="muted">{{ row.kind }}</td>
              <td class="r num">{{ row.heatScore.toFixed(1) }}</td>
              <td class="r num">{{ row.changeScore.toFixed(1) }}</td>
              <td class="r num">{{ row.limitScore.toFixed(1) }}（{{ row.limitUps }} 家）</td>
              <td class="r"><span class="heat num" :style="{ background: heatBadge(row.index) }">{{ row.index }}</span></td>
              <td class="r"><DeltaValue :value="row.changePct" suffix="%" /></td>
              <td><span :class="row.hot ? 'up' : 'faint'">{{ row.hot ? "是" : "否" }}</span></td>
            </tr>
          </tbody>
        </table>
      </template>
      <p v-else class="faint">{{ boards.error || "板块数据还在加载" }}</p>
    </el-dialog>
  </div>
</template>

<style scoped>
.section {
  margin-top: var(--gap);
}

.trade-date {
  margin-right: 12px;
  color: var(--muted);
  font-size: 13px;
}

.trade-date b {
  color: var(--text-strong);
}

.clickable {
  cursor: pointer;
}

.kv {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--muted);
}

.kv b {
  font-size: 15px;
}

.hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--faint);
}

.temp-label {
  display: grid;
  justify-items: end;
  gap: 2px;
  font-size: 12px;
}

.temp-label b {
  font-size: 16px;
  color: var(--orange);
}

.tempbar {
  position: relative;
  height: 8px;
  margin: 10px 0 6px;
  border-radius: 5px;
  background: linear-gradient(90deg, #1e3a8a, #3b82f6 30%, #94a3b8 50%, #fb923c 72%, #f6465d);
}

.tempbar i {
  position: absolute;
  top: -4px;
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: #fff;
  box-shadow: 0 0 8px #fff;
  transform: translateX(-50%);
}

.factor-line {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 0;
  font-size: 12px;
  color: var(--muted);
}

.factor-line b {
  margin-left: 2px;
}

.faint {
  color: var(--faint);
}

.muted {
  color: var(--muted);
  font-size: 13px;
}

.dialog-title {
  margin: 0;
  font-size: 17px;
  color: var(--text-strong);
}

.formula {
  margin: 10px 0;
  padding: 10px 12px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-deep);
  color: var(--text);
  font-family: Consolas, var(--font-num);
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.sub-title {
  margin: 14px 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.dialog-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.dialog-charts.three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.dialog-chart {
  height: 260px;
  border: 1px solid var(--line-soft);
  border-radius: 8px;
  background: var(--panel-deep);
}

.dialog-chart .pad {
  padding: 16px;
}

.basis-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.basis-table th {
  padding: 6px;
  text-align: left;
  font-weight: 500;
  color: var(--muted);
  background: color-mix(in srgb, var(--text) 5%, transparent);
}

.basis-table td {
  padding: 5px 6px;
  border-bottom: 1px solid var(--line-soft);
  white-space: nowrap;
}

.basis-table .r {
  text-align: right;
}

.heat {
  display: inline-block;
  min-width: 34px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 600;
  color: #fff;
  text-align: center;
}

@media (max-width: 1280px) {
  .dialog-charts,
  .dialog-charts.three {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
