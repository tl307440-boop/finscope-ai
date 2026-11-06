<template>
  <MarketPanel
    title="A 股每日短线情绪与盘面广度"
    subtitle="涨跌分布 · 自然板 · 炸板与接力晋级 · 日内冲高回落 / 探底回升 ≥ 10% · 昨日涨停溢价率"
    info="全部来自公开行情；口径与缺项见底部「口径说明」「缺项」"
    tone="orange"
    :icon="Odometer"
    :meta="sentiment?.meta ?? null"
    :refresh-error="error"
  >
    <template v-if="sentiment?.maxStreakLeader" #actions>
      <button
        type="button"
        class="sbp-leader-chip"
        @click="linkageStore.selectStock(sentiment.maxStreakLeader.code)"
      >
        <span class="leader-label">最高空间板 {{ fmtInt(sentiment.maxStreakHeight) }} 连板</span>
        <span class="leader-stock">{{ sentiment.maxStreakLeader.name }}</span>
        <span class="leader-pct" :class="toneClass(sentiment.maxStreakLeader.changePct)">
          {{ fmtPct(sentiment.maxStreakLeader.changePct) }}
        </span>
        <span class="leader-theme">{{ sentiment.maxStreakLeader.sector }}</span>
      </button>
    </template>

    <StateBlock
      v-if="!sentiment"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="短线情绪加载失败"
      :rows="4"
      @retry="reload"
    />

    <template v-else>
      <!-- 涨跌平对比进度条 + 涨跌幅中位数 -->
      <PanelSection title="涨跌广度" note="全 A 涨跌平家数与涨跌幅中位数">
      <div class="breadth-bar-card">
        <div class="breadth-counts">
          <span class="b-up">
            上涨 <b>{{ fmtInt(sentiment.upCount) }}</b> 家 ({{ ratioText(sentiment.upCount) }})
          </span>
          <span class="b-median">
            全市场涨跌幅中位数：
            <b :class="toneClass(sentiment.medianChangePct)">{{ fmtPct(sentiment.medianChangePct) }}</b>
          </span>
          <span class="b-flat">平盘 <b>{{ fmtInt(sentiment.flatCount) }}</b> 家</span>
          <span class="b-down">
            下跌 <b>{{ fmtInt(sentiment.downCount) }}</b> 家 ({{ ratioText(sentiment.downCount) }})
          </span>
        </div>
        <div v-if="totalStocks > 0" class="breadth-progress">
          <div class="bp-seg seg-up" :style="{ width: `${ratioNum(sentiment.upCount)}%` }" />
          <div class="bp-seg seg-flat" :style="{ width: `${ratioNum(sentiment.flatCount)}%` }" />
          <div class="bp-seg seg-down" :style="{ width: `${ratioNum(sentiment.downCount)}%` }" />
        </div>
      </div>
      </PanelSection>

      <!-- 短线核心接力与涨跌停指标矩阵 -->
      <PanelSection title="涨停 · 炸板 · 接力" note="点击「冲高回落 / 探底回升」可展开标的，联动右侧个股详情">
      <div class="sentiment-kpi-grid">
        <div class="skpi-card">
          <span class="skpi-label">涨停 / 自然涨停</span>
          <div class="skpi-main">
            <span class="skpi-val is-rise">{{ fmtInt(sentiment.limitUpTotal) }}</span>
            <span class="skpi-slash">/</span>
            <span class="skpi-val is-rise">{{ fmtInt(sentiment.naturalLimitUp) }}</span>
          </div>
          <span class="skpi-note">自然板剔除 ST 及 N/C 开头新股</span>
        </div>

        <div class="skpi-card">
          <span class="skpi-label">跌停 / 自然跌停</span>
          <div class="skpi-main">
            <span class="skpi-val is-fall">{{ fmtInt(sentiment.limitDownTotal) }}</span>
            <span class="skpi-slash">/</span>
            <span class="skpi-val is-fall">{{ fmtInt(sentiment.naturalLimitDown) }}</span>
          </div>
          <span class="skpi-note">自然板口径同左</span>
        </div>

        <div class="skpi-card">
          <span class="skpi-label">炸板数 / 炸板率</span>
          <div class="skpi-main">
            <span class="skpi-val">{{ fmtInt(sentiment.brokenCount) }}家</span>
            <span class="skpi-badge warn">{{ fmtPct(sentiment.brokenRatePct, 2, false) }}</span>
          </div>
          <span class="skpi-note">炸板 /（涨停 + 炸板）</span>
        </div>

        <div class="skpi-card">
          <span class="skpi-label">连板数 / 连板晋级率</span>
          <div class="skpi-main">
            <span class="skpi-val is-rise">{{ fmtInt(sentiment.streakCount) }}家</span>
            <span class="skpi-badge rise">{{ fmtPct(sentiment.promotionRatePct, 2, false) }}</span>
          </div>
          <span class="skpi-note">
            最高 {{ fmtInt(sentiment.maxStreakHeight) }} 板
            <template v-if="sentiment.maxStreakLeader">（{{ sentiment.maxStreakLeader.name }}）</template>
          </span>
        </div>

        <div
          class="skpi-card clickable"
          :class="{ active: expandMode === 'pullback' }"
          @click="toggleExpand('pullback')"
        >
          <span class="skpi-label">冲高回落≥10%</span>
          <div class="skpi-main">
            <span class="skpi-val is-fall">{{ fmtInt(sentiment.pullbackOver10Count) }}家</span>
            <span class="expand-hint">{{ expandMode === 'pullback' ? '收起 ▲' : '展开标的 ▼' }}</span>
          </div>
          <span class="skpi-note">日内最高涨幅 − 收盘涨幅 ≥ 10 个点</span>
        </div>

        <div
          class="skpi-card clickable"
          :class="{ active: expandMode === 'rebound' }"
          @click="toggleExpand('rebound')"
        >
          <span class="skpi-label">探底回升≥10%</span>
          <div class="skpi-main">
            <span class="skpi-val is-rise">{{ fmtInt(sentiment.reboundOver10Count) }}家</span>
            <span class="expand-hint">{{ expandMode === 'rebound' ? '收起 ▲' : '展开标的 ▼' }}</span>
          </div>
          <span class="skpi-note">收盘涨幅 − 日内最低涨幅 ≥ 10 个点</span>
        </div>
      </div>

      <!-- 展开的极端波动标的列表 -->
      <div v-if="expandMode" class="extreme-swing-drawer">
        <div class="esd-head">
          <span class="esd-title">
            {{
              expandMode === "pullback"
                ? "日内冲高回落 ≥ 10%（点击标的联动右侧个股详情）"
                : "日内探底回升 ≥ 10%（点击标的联动右侧个股详情）"
            }}
          </span>
          <button type="button" class="esd-close" @click="expandMode = null">收起列表</button>
        </div>
        <StateBlock
          v-if="activeExtremeList.length === 0"
          state="empty"
          message="当前没有满足条件的个股"
        />
        <div v-else class="esd-list">
          <div
            v-for="st in activeExtremeList"
            :key="st.code"
            class="esd-item"
            @click="linkageStore.selectStock(st.code)"
          >
            <div class="esd-left">
              <span class="esd-name">{{ st.name }}</span>
              <span class="esd-code">{{ st.code }}</span>
              <span class="esd-sec">{{ st.sector }}</span>
            </div>
            <div class="esd-mid">
              <span>收盘：<b :class="toneClass(st.changePct)">{{ fmtPct(st.changePct) }}</b></span>
              <span>
                {{ expandMode === "pullback" ? "日内最高：" : "日内最低：" }}
                <b :class="toneClass(st.peakOrTroughPct)">{{ fmtPct(st.peakOrTroughPct) }}</b>
              </span>
              <span>
                {{ expandMode === "pullback" ? "回头波跌幅：" : "探底回升波幅：" }}
                <b>{{ fmtPct(st.swingPct, 2, false) }}</b>
              </span>
            </div>
            <div class="esd-reason">{{ st.reason }}</div>
          </div>
        </div>
      </div>

      </PanelSection>

      <!-- 昨日涨停/连板/炸板今日溢价率与趋势 -->
      <PanelSection title="昨日涨停今日表现" note="溢价率 = 昨日涨停（连板、炸板）股今日平均涨跌幅">
      <div class="premium-row">
        <div class="premium-cards">
          <div class="prem-item">
            <span class="p-label">昨日涨停溢价</span>
            <span class="p-val" :class="toneClass(sentiment.yesterdayLimitUpPremiumPct)">
              {{ fmtPct(sentiment.yesterdayLimitUpPremiumPct) }}
            </span>
            <span class="p-desc">昨日涨停股今日平均涨跌幅</span>
          </div>
          <div class="prem-item">
            <span class="p-label">昨日连板溢价</span>
            <span class="p-val" :class="toneClass(sentiment.yesterdayStreakPremiumPct)">
              {{ fmtPct(sentiment.yesterdayStreakPremiumPct) }}
            </span>
            <span class="p-desc">昨日连板股今日平均涨跌幅</span>
          </div>
          <div class="prem-item">
            <span class="p-label">昨日炸板溢价</span>
            <span class="p-val" :class="toneClass(sentiment.yesterdayBrokenPremiumPct)">
              {{ fmtPct(sentiment.yesterdayBrokenPremiumPct) }}
            </span>
            <span class="p-desc">昨日炸板股今日平均涨跌幅</span>
          </div>
        </div>
        <div class="premium-chart-box">
          <VChart
            v-if="sentiment.premiumHistory.dates.length > 0"
            class="prem-chart"
            :option="premiumChartOption"
            autoresize
          />
          <StateBlock v-else state="empty" message="近期溢价率历史暂无数据" />
        </div>
      </div>
      </PanelSection>
    </template>
  </MarketPanel>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import { Odometer } from "@element-plus/icons-vue"
import type { EChartsOption } from "echarts"

import { fetchSentiment, type SwingStock } from "../../../api/aSharePanorama"
import { fmtInt, fmtPct, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import { VChart } from "../../../charts"
import MarketPanel from "../../../components/market/MarketPanel.vue"
import PanelSection from "../../../components/market/PanelSection.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import { useMarketLinkageStore } from "../../../stores/marketLinkage"

const linkageStore = useMarketLinkageStore()
const { data: sentiment, state, error, reload } = usePanoramaResource(fetchSentiment, {
  pollMs: 60_000,
})
const expandMode = ref<"pullback" | "rebound" | null>(null)

const totalStocks = computed(() => {
  const s = sentiment.value
  return s ? (s.upCount ?? 0) + (s.downCount ?? 0) + (s.flatCount ?? 0) : 0
})

function ratioNum(count: number | null): string {
  if (count === null || totalStocks.value <= 0) return "0"
  return ((count / totalStocks.value) * 100).toFixed(1)
}

function ratioText(count: number | null): string {
  if (count === null || totalStocks.value <= 0) return "—"
  return `${ratioNum(count)}%`
}

const activeExtremeList = computed<SwingStock[]>(() => {
  if (expandMode.value === "pullback") return sentiment.value?.pullbackStocks ?? []
  if (expandMode.value === "rebound") return sentiment.value?.reboundStocks ?? []
  return []
})

function toggleExpand(mode: "pullback" | "rebound"): void {
  expandMode.value = expandMode.value === mode ? null : mode
}

const premiumChartOption = computed<EChartsOption>(() => {
  const h = sentiment.value?.premiumHistory
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 8, textStyle: { fontSize: 10.5, color: "#64748b" } },
    grid: { left: 36, right: 12, top: 24, bottom: 20 },
    xAxis: {
      type: "category",
      data: h?.dates ?? [],
      axisLabel: { fontSize: 10, color: "#64748b" },
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 10, color: "#64748b", formatter: "{value}%" },
      splitLine: { lineStyle: { type: "dashed", color: "rgba(148,163,184,0.16)" } },
    },
    series: [
      {
        name: "昨涨停溢价",
        type: "line",
        data: h?.limitUpPremium ?? [],
        smooth: true,
        lineStyle: { width: 2, color: "#f6465d" },
        itemStyle: { color: "#f6465d" },
      },
      {
        name: "昨连板溢价",
        type: "line",
        data: h?.streakPremium ?? [],
        smooth: true,
        lineStyle: { width: 2, color: "#f59e0b" },
        itemStyle: { color: "#f59e0b" },
      },
    ],
  }
})
</script>

<style scoped>
.sbp-leader-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 11px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--rise) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--rise) 38%, transparent);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
}

.sbp-leader-chip:hover {
  background: color-mix(in srgb, var(--rise) 16%, transparent);
}

.leader-label {
  color: var(--muted);
}

.leader-stock {
  font-weight: 700;
  color: var(--rise);
}

.leader-pct {
  font-family: var(--font-num);
  font-weight: 700;
}

.leader-theme {
  font-size: 12px;
  color: var(--faint);
}

.breadth-bar-card {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 9px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.breadth-counts {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  flex-wrap: wrap;
  gap: 8px;
}

.b-up b,
.is-rise {
  color: var(--rise);
}

.b-down b,
.is-fall {
  color: var(--fall);
}

.breadth-progress {
  height: 9px;
  border-radius: 999px;
  overflow: hidden;
  display: flex;
  background: var(--panel-soft);
}

.bp-seg.seg-up {
  background: linear-gradient(90deg, #f6465d, #fb7185);
}

.bp-seg.seg-flat {
  background: #94a3b8;
}

.bp-seg.seg-down {
  background: linear-gradient(90deg, #34d399, #1fc37d);
}

.sentiment-kpi-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.skpi-card {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 9px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.skpi-card.clickable {
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.skpi-card.clickable:hover,
.skpi-card.clickable.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.skpi-label {
  font-size: 12px;
  color: var(--faint);
}

.skpi-main {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-family: var(--font-num);
}

.skpi-val {
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
}

.skpi-slash {
  color: var(--faint);
}

.skpi-badge {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 999px;
  font-weight: 600;
}

.skpi-badge.warn {
  background: rgba(245, 158, 11, 0.14);
  color: #d97706;
}

.skpi-badge.rise {
  background: rgba(246, 70, 93, 0.14);
  color: var(--rise);
}

.expand-hint {
  font-size: 12px;
  color: var(--accent);
  font-family: var(--font-ui);
}

.skpi-note {
  font-size: 12px;
  color: var(--muted);
}

.extreme-swing-drawer {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.esd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.esd-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text);
}

.esd-close {
  border: none;
  background: none;
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
}

.esd-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.esd-item {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 7px 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.esd-item:hover {
  border-color: rgba(246, 70, 93, 0.45);
}

.esd-left {
  display: flex;
  align-items: center;
  gap: 6px;
}

.esd-name {
  font-weight: 700;
  font-size: 12.5px;
  color: var(--text);
}

.esd-code {
  font-family: var(--font-num);
  font-size: 12px;
  color: var(--faint);
}

.esd-sec {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--panel-soft);
  color: var(--muted);
}

.esd-mid {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--muted);
}

.esd-reason {
  font-size: 12px;
  color: var(--faint);
}

.premium-row {
  display: grid;
  grid-template-columns: 1.1fr 1.4fr;
  gap: 10px;
  align-items: stretch;
}

.premium-cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.prem-item {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 9px 10px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
}

.p-label {
  font-size: 12px;
  color: var(--faint);
}

.p-val {
  font-family: var(--font-num);
  font-size: 18px;
  font-weight: 700;
}

.p-desc {
  font-size: 12px;
  color: var(--muted);
}

.premium-chart-box {
  background: var(--panel-soft);
  border: 1px solid var(--line);
  border-radius: 8px;
  height: 118px;
  padding: 4px;
}

.prem-chart {
  width: 100%;
  height: 100%;
}

@media (max-width: 1280px) {
  .sentiment-kpi-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .premium-row {
    grid-template-columns: 1fr;
  }
  .esd-list {
    grid-template-columns: 1fr;
  }
}
</style>
