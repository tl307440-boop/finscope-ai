<template>
  <aside class="stock-detail-panel" aria-label="右侧个股详情联动面板">
    <StateBlock
      v-if="!linkageStore.selectedStockCode"
      state="empty"
      title="个股详情"
      message="点击左侧任意个股，在这里查看分时、日K、封单与龙虎榜席位"
    />
    <StateBlock
      v-else-if="!detail"
      :state="loadState"
      :message="loadError"
      title="个股详情加载失败"
      :rows="5"
      @retry="loadDetail(linkageStore.selectedStockCode)"
    />

    <template v-else>
      <div class="sdp-header">
        <div class="sdp-title-row">
          <div class="sdp-stock-id">
            <span class="sdp-name">{{ detail.name || detail.code }}</span>
            <span class="sdp-code">{{ detail.code }}</span>
            <span v-if="detail.streakLabel" class="sdp-streak-pill">{{ detail.streakLabel }}</span>
          </div>
          <div class="sdp-price-box" :class="toneClass(detail.changePct)">
            <span class="sdp-price">{{ fmtNum(detail.price) }}</span>
            <span class="sdp-chg">{{ fmtPct(detail.changePct) }}</span>
          </div>
        </div>

        <div v-if="detail.sectors.length" class="sdp-sectors-row">
          <span class="sdp-sec-label">所属板块：</span>
          <span v-for="sec in detail.sectors" :key="sec" class="sdp-sector-tag">{{ sec }}</span>
        </div>
      </div>

      <!-- 核心看盘与短线博弈指标网格 -->
      <div class="sdp-metrics-grid">
        <div class="sdp-metric-cell highlight-seal">
          <span class="m-label">最后封单金额</span>
          <span class="m-val" :class="detail.sealAmountWan ? 'is-rise' : ''">
            {{ detail.sealAmountWan ? fmtWan(detail.sealAmountWan) : "—" }}
          </span>
          <span class="m-sub">
            {{ detail.sealAmountWan ? "" : "未封板或无封单数据 · " }}封流比
            {{ fmtPct(detail.sealFloatRatioPct, 2, false) }}
          </span>
        </div>
        <div class="sdp-metric-cell">
          <span class="m-label">换手率</span>
          <span class="m-val">{{ fmtPct(detail.turnoverPct, 2, false) }}</span>
          <span class="m-sub">首封 {{ detail.firstSealTime || "—" }}</span>
        </div>
        <div class="sdp-metric-cell">
          <span class="m-label">流通市值</span>
          <span class="m-val">{{ fmtYi(detail.floatCapYi) }}</span>
          <span class="m-sub">昨收 {{ fmtNum(detail.preClose) }}</span>
        </div>
      </div>

      <!-- 涨停原因 / 催化逻辑 -->
      <div class="sdp-catalyst-box">
        <div class="catalyst-head">
          <span class="catalyst-badge">涨停原因 / 所属题材</span>
          <RouterLink :to="`/company/${detail.code.slice(0, 6)}`" class="sdp-deep-link">
            F10深度档案 →
          </RouterLink>
        </div>
        <p class="catalyst-text">{{ detail.catalystReason || "上游暂未提供该股的涨停原因" }}</p>
        <div v-if="detail.actionExplain || detail.actionSector" class="action-box">
          <span class="action-badge">韭研公社异动解析</span>
          <b v-if="detail.actionSector" class="action-sector">{{ detail.actionSector }}</b>
          <p v-if="detail.actionSectorReason" class="action-text action-sector-reason">
            {{ detail.actionSectorReason }}
          </p>
          <p v-if="detail.actionExplain" class="action-text">{{ detail.actionExplain }}</p>
        </div>
      </div>

      <!-- 分时走势图 & 日K线图 -->
      <div class="sdp-chart-section">
        <div class="sdp-chart-tabs">
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'both' }"
            @click="chartMode = 'both'"
          >
            分时 + 日K
          </button>
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'intraday' }"
            @click="chartMode = 'intraday'"
          >
            分时（均价+量柱）
          </button>
          <button
            type="button"
            class="chart-tab-btn"
            :class="{ active: chartMode === 'kline' }"
            @click="chartMode = 'kline'"
          >
            日K（MA5/10/20）
          </button>
        </div>

        <div v-if="chartMode === 'both' || chartMode === 'intraday'" class="sdp-chart-card">
          <div class="chart-mini-head">
            <span class="chart-mini-title">当日分时走势</span>
            <span class="chart-mini-legend">
              <i class="dot-price" /> 现价线
              <i class="dot-avg" /> 均价线
            </span>
          </div>
          <div class="sdp-echart-wrap">
            <VChart
              v-if="detail.intraday.length"
              class="sdp-echart"
              :option="intradayOption"
              autoresize
            />
            <StateBlock v-else state="empty" message="暂无分时数据（非交易日或上游未返回）" />
          </div>
        </div>

        <div v-if="chartMode === 'both' || chartMode === 'kline'" class="sdp-chart-card">
          <div class="chart-mini-head">
            <span class="chart-mini-title">日K线（含 MA 均线与成交量）</span>
            <span class="chart-mini-legend">
              <span class="ma5-txt">MA5</span>
              <span class="ma10-txt">MA10</span>
              <span class="ma20-txt">MA20</span>
            </span>
          </div>
          <div class="sdp-echart-wrap">
            <VChart
              v-if="detail.dailyBars.length"
              class="sdp-echart"
              :option="klineOption"
              autoresize
            />
            <StateBlock v-else state="empty" message="暂无日K数据" />
          </div>
        </div>
      </div>

      <!-- 龙虎榜买卖前五席位明细 -->
      <div class="sdp-lhb-section">
        <div class="lhb-sec-header">
          <span class="lhb-sec-title">
            龙虎榜买卖前五席位<template v-if="detail.lhbDate">
              （{{ lhbDateLabel }}）</template>
          </span>
          <span v-if="detail.lhbReason" class="lhb-sec-reason" :title="detail.lhbReason">
            {{ detail.lhbReason }}
          </span>
        </div>

        <StateBlock
          v-if="!detail.buySeats.length && !detail.sellSeats.length"
          state="empty"
          message="该股近期未上龙虎榜，或当日榜单尚未公布（通常收盘后 17:00 左右）"
        />
        <template v-else>
          <div class="lhb-group">
            <div class="lhb-group-label is-buy">买入前五（自动匹配游资 / 外资 / 机构标签）</div>
            <div v-for="(seat, idx) in detail.buySeats" :key="`buy-${idx}`" class="lhb-seat-row">
              <div class="seat-left">
                <span class="seat-rank is-buy">买{{ idx + 1 }}</span>
                <span class="seat-branch" :title="seat.branchName">
                  {{ shortenBranchName(seat.branchName) }}
                </span>
                <button
                  v-if="matchSeat(seat.branchName)"
                  type="button"
                  class="seat-tag-badge"
                  :class="roleBadgeClass(matchSeat(seat.branchName)!.category)"
                  title="跳转到龙虎榜分析并定位该席位"
                  @click="jumpToSeat(matchSeat(seat.branchName)!.groupId)"
                >
                  {{ matchSeat(seat.branchName)!.name }}
                </button>
              </div>
              <div class="seat-amounts">
                <span class="amt-buy">+{{ fmtWan(seat.buyWan) }}</span>
                <span class="amt-net" :class="toneClass(seat.netWan)">净 {{ fmtWan(seat.netWan, true) }}</span>
              </div>
            </div>
          </div>

          <div class="lhb-group">
            <div class="lhb-group-label is-sell">卖出前五</div>
            <div v-for="(seat, idx) in detail.sellSeats" :key="`sell-${idx}`" class="lhb-seat-row">
              <div class="seat-left">
                <span class="seat-rank is-sell">卖{{ idx + 1 }}</span>
                <span class="seat-branch" :title="seat.branchName">
                  {{ shortenBranchName(seat.branchName) }}
                </span>
                <button
                  v-if="matchSeat(seat.branchName)"
                  type="button"
                  class="seat-tag-badge"
                  :class="roleBadgeClass(matchSeat(seat.branchName)!.category)"
                  title="跳转到龙虎榜分析并定位该席位"
                  @click="jumpToSeat(matchSeat(seat.branchName)!.groupId)"
                >
                  {{ matchSeat(seat.branchName)!.name }}
                </button>
              </div>
              <div class="seat-amounts">
                <span class="amt-sell">-{{ fmtWan(seat.sellWan) }}</span>
                <span class="amt-net" :class="toneClass(seat.netWan)">净 {{ fmtWan(seat.netWan, true) }}</span>
              </div>
            </div>
          </div>
        </template>
      </div>

      <PanoramaStatusBar :meta="detail.meta" />
    </template>
  </aside>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue"
import { useRouter } from "vue-router"
import type { EChartsOption } from "echarts"

import { errorMessage, fetchStockDetail, type StockDetailData } from "../../api/aSharePanorama"
import { fmtNum, fmtPct, fmtWan, fmtYi, toneClass } from "../../api/aShareFormat"
import { VChart } from "../../charts"
import type { SeatCategory } from "../../config/hotMoneySeatsConfig"
import { useMarketLinkageStore } from "../../stores/marketLinkage"
import StateBlock from "../ui/StateBlock.vue"
import PanoramaStatusBar from "./PanoramaStatusBar.vue"

const linkageStore = useMarketLinkageStore()
const router = useRouter()
const detail = ref<StockDetailData | null>(null)
const loadState = ref<"loading" | "error">("loading")
const loadError = ref("")
const chartMode = ref<"both" | "intraday" | "kline">("both")
let loadSeq = 0

async function loadDetail(code: string): Promise<void> {
  if (!code) {
    detail.value = null
    return
  }
  const seq = ++loadSeq
  loadState.value = "loading"
  loadError.value = ""
  // 切股时先清空，避免短暂显示上一只股票的数据
  if (detail.value && detail.value.code.slice(0, 6) !== code.slice(0, 6)) detail.value = null
  try {
    const result = await fetchStockDetail(code)
    if (seq === loadSeq) detail.value = result
  } catch (e) {
    if (seq !== loadSeq) return
    loadState.value = "error"
    loadError.value = errorMessage(e)
  }
}

watch(
  () => linkageStore.selectedStockCode,
  (next) => void loadDetail(next),
)

onMounted(() => void loadDetail(linkageStore.selectedStockCode))

/** 席位明细不一定是今天的：不是当日上榜时要明确写「最近一次上榜」，避免被当成今日数据。 */
const lhbDateLabel = computed(() => {
  const d = detail.value
  if (!d?.lhbDate) return ""
  return d.lhbDate === d.meta.tradeDate ? d.lhbDate : `最近一次上榜 ${d.lhbDate}`
})

function matchSeat(branchName: string) {
  return linkageStore.matchBranchTag(branchName)
}

function jumpToSeat(groupId: string): void {
  linkageStore.focusSeat(groupId)
  void router.push("/market/dragon-tiger")
}

function shortenBranchName(raw: string): string {
  return raw
    .replace(/证券股份有限公司|证券有限责任公司|证券有限公司|股份有限公司|有限责任公司/g, "")
    .replace(/证券营业部/g, "营业部")
}

function roleBadgeClass(category: SeatCategory): string {
  if (category === "知名游资") return "role-hotmoney"
  if (category === "量化打板") return "role-quant"
  if (category === "外资") return "role-foreign"
  if (category === "机构专用") return "role-inst"
  return "role-lhasa"
}

const intradayOption = computed<EChartsOption>(() => {
  const pts = detail.value?.intraday ?? []
  const times = pts.map((p) => p.time)
  const prices = pts.map((p) => p.price)
  const avgs = pts.map((p) => p.avgPrice)
  const vols = pts.map((p) => p.volume)
  const isUp = (detail.value?.changePct ?? 0) >= 0
  const mainColor = isUp ? "#f6465d" : "#1fc37d"

  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: [
      { left: 42, right: 12, top: 10, height: "58%" },
      { left: 42, right: 12, top: "74%", height: "20%" },
    ],
    xAxis: [
      {
        type: "category",
        data: times,
        boundaryGap: false,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.25)" } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: times,
        axisLabel: { fontSize: 10, color: "#64748b" },
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.25)" } },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitNumber: 3,
        axisLabel: { fontSize: 10, color: "#64748b" },
        splitLine: { lineStyle: { color: "rgba(148,163,184,0.14)", type: "dashed" } },
      },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: "分时现价",
        type: "line",
        data: prices,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.8, color: mainColor },
        areaStyle: {
          color: isUp ? "rgba(246, 70, 93, 0.14)" : "rgba(31, 195, 125, 0.14)",
        },
      },
      {
        name: "分时均价",
        type: "line",
        data: avgs,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.3, color: "#f59e0b", type: "dashed" },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: vols,
        itemStyle: {
          color: (params) => {
            const idx = params.dataIndex
            if (idx === 0) return mainColor
            return (prices[idx] ?? 0) >= (prices[idx - 1] ?? 0) ? "#f6465d" : "#1fc37d"
          },
        },
      },
    ],
  }
})

function calcMA(dayCount: number, closes: number[]): (number | null)[] {
  return closes.map((_, idx) => {
    if (idx < dayCount - 1) return null
    let sum = 0
    for (let i = 0; i < dayCount; i++) {
      sum += closes[idx - i] ?? 0
    }
    return Number((sum / dayCount).toFixed(2))
  })
}

const klineOption = computed<EChartsOption>(() => {
  const bars = detail.value?.dailyBars ?? []
  const dates = bars.map((b) => b.date.slice(5))
  const ohlc = bars.map((b) => [b.open, b.close, b.low, b.high])
  const closes = bars.map((b) => b.close)

  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: [
      { left: 42, right: 12, top: 10, height: "58%" },
      { left: 42, right: 12, top: "74%", height: "20%" },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.25)" } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: dates,
        axisLabel: { fontSize: 10, color: "#64748b" },
        axisLine: { lineStyle: { color: "rgba(148,163,184,0.25)" } },
      },
    ],
    yAxis: [
      {
        scale: true,
        splitNumber: 3,
        axisLabel: { fontSize: 10, color: "#64748b" },
        splitLine: { lineStyle: { color: "rgba(148,163,184,0.14)", type: "dashed" } },
      },
      { gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        name: "日K",
        type: "candlestick",
        data: ohlc,
        itemStyle: {
          color: "#f6465d",
          color0: "#1fc37d",
          borderColor: "#f6465d",
          borderColor0: "#1fc37d",
        },
      },
      {
        name: "MA5",
        type: "line",
        data: calcMA(5, closes),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#f59e0b" },
      },
      {
        name: "MA10",
        type: "line",
        data: calcMA(10, closes),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#3b82f6" },
      },
      {
        name: "MA20",
        type: "line",
        data: calcMA(20, closes),
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 1.2, color: "#a855f7" },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: bars.map((b) => b.volume),
        itemStyle: {
          color: (params) => {
            const b = bars[params.dataIndex]
            return b && b.close >= b.open ? "#f6465d" : "#1fc37d"
          },
        },
      },
    ],
  }
})
</script>

<style scoped>
.stock-detail-panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 28px);
  overflow-y: auto;
}

.sdp-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.sdp-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sdp-stock-id {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sdp-name {
  font-size: 17px;
  font-weight: 700;
  color: var(--text);
}

.sdp-code {
  font-family: var(--font-num);
  font-size: 12px;
  color: var(--faint);
}

.sdp-streak-pill {
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  background: rgba(246, 70, 93, 0.14);
  color: var(--rise);
  border: 1px solid rgba(246, 70, 93, 0.35);
}

.sdp-price-box {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: var(--font-num);
}

.sdp-price {
  font-size: 18px;
  font-weight: 700;
}

.sdp-chg {
  font-size: 13px;
  font-weight: 600;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

.sdp-sectors-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.sdp-sec-label {
  font-size: 11px;
  color: var(--faint);
}

.sdp-sector-tag {
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
  background: var(--panel-soft);
  color: var(--muted);
  border: 1px solid var(--line);
}

.sdp-metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.sdp-metric-cell {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sdp-metric-cell.highlight-seal {
  border-color: rgba(246, 70, 93, 0.3);
  background: rgba(246, 70, 93, 0.04);
}

.m-label {
  font-size: 11px;
  color: var(--faint);
}

.m-val {
  font-family: var(--font-num);
  font-size: 14px;
  font-weight: 700;
  color: var(--text);
}

.m-sub {
  font-size: 10.5px;
  color: var(--muted);
}

.sdp-catalyst-box {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 9px 11px;
}

.catalyst-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 5px;
}

.catalyst-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
}

.sdp-deep-link {
  font-size: 11px;
  color: var(--muted);
  text-decoration: none;
}

.sdp-deep-link:hover {
  color: var(--accent);
}

.catalyst-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text);
}

.action-box {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--line);
}

.action-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--muted);
}

.action-sector {
  margin-left: 6px;
  font-size: 12px;
  color: var(--text);
}

.action-text {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text);
  white-space: pre-line;
  max-height: 160px;
  overflow-y: auto;
}

.action-sector-reason {
  color: var(--muted);
}

.sdp-chart-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sdp-chart-tabs {
  display: flex;
  gap: 6px;
}

.chart-tab-btn {
  flex: 1;
  padding: 5px 8px;
  font-size: 11px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
}

.chart-tab-btn.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.35);
  font-weight: 600;
}

.sdp-chart-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px;
  background: var(--panel-soft);
}

.chart-mini-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.chart-mini-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text);
}

.chart-mini-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10.5px;
  color: var(--faint);
}

.dot-price,
.dot-avg {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.dot-price {
  background: #f6465d;
}

.dot-avg {
  background: #f59e0b;
}

.ma5-txt {
  color: #f59e0b;
}

.ma10-txt {
  color: #3b82f6;
}

.ma20-txt {
  color: #a855f7;
}

.sdp-echart-wrap {
  height: 155px;
  width: 100%;
}

.sdp-echart {
  width: 100%;
  height: 100%;
}

.sdp-lhb-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--line);
  padding-top: 10px;
}

.lhb-sec-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.lhb-sec-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--text);
}

.lhb-sec-reason {
  font-size: 10.5px;
  color: var(--faint);
  max-width: 170px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.lhb-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.lhb-group-label {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
}

.lhb-group-label.is-buy {
  color: var(--rise);
  background: rgba(246, 70, 93, 0.08);
}

.lhb-group-label.is-sell {
  color: var(--fall);
  background: rgba(31, 195, 125, 0.08);
}

.lhb-seat-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 5px 7px;
  border-radius: 6px;
  background: var(--panel-soft);
  border: 1px solid var(--line);
  font-size: 11.5px;
}

.seat-left {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  flex-wrap: wrap;
}

.seat-rank {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 4px;
  border-radius: 4px;
}

.seat-rank.is-buy {
  background: rgba(246, 70, 93, 0.16);
  color: var(--rise);
}

.seat-rank.is-sell {
  background: rgba(31, 195, 125, 0.16);
  color: var(--fall);
}

.seat-branch {
  color: var(--text);
  font-weight: 500;
  max-width: 155px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.seat-tag-badge {
  cursor: pointer;
  font-family: inherit;
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
}

.role-hotmoney {
  background: rgba(246, 70, 93, 0.14);
  color: var(--rise);
  border: 1px solid rgba(246, 70, 93, 0.35);
}

.role-quant {
  background: rgba(168, 85, 247, 0.14);
  color: #9333ea;
  border: 1px solid rgba(168, 85, 247, 0.35);
}

.role-foreign {
  background: rgba(37, 99, 235, 0.14);
  color: #2563eb;
  border: 1px solid rgba(37, 99, 235, 0.35);
}

.role-inst {
  background: rgba(245, 158, 11, 0.14);
  color: #d97706;
  border: 1px solid rgba(245, 158, 11, 0.35);
}

.role-lhasa {
  background: rgba(16, 185, 129, 0.14);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.35);
}

.seat-amounts {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  font-family: var(--font-num);
  font-size: 11px;
  flex-shrink: 0;
}

.amt-buy {
  color: var(--rise);
  font-weight: 600;
}

.amt-sell {
  color: var(--fall);
  font-weight: 600;
}

.amt-net {
  font-size: 10px;
}
</style>
