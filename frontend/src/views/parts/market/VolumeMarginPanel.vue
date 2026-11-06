<template>
  <section class="volume-margin-panel" aria-label="多维成交量能与两融余额监控">
    <!-- 左栏：多维成交量能拆解 -->
    <div class="vm-card">
      <div class="vm-head">
        <div>
          <span class="vm-title">多维成交量能（全天 + 11:30 午盘量能对比）</span>
          <span class="vm-sub">全市场（沪+深+京）/ 上证 / 深证 / 北证 / 创业板 / 科创板</span>
        </div>
        <div v-if="volumes.length" class="vm-tabs">
          <button
            v-for="v in volumes"
            :key="v.key"
            type="button"
            class="vm-tab-btn"
            :class="{ active: currentVolume?.key === v.key }"
            @click="activeVolKey = v.key"
          >
            {{ VOLUME_TAB[v.key] }}
          </button>
        </div>
      </div>

      <StateBlock
        v-if="!volumeMargin"
        :state="state === 'error' ? 'error' : 'loading'"
        :message="error"
        title="成交量能加载失败"
        @retry="reload"
      />
      <template v-else-if="currentVolume">
        <div class="vm-kpi-row">
          <div class="vm-kpi-item">
            <span class="vk-label">{{ currentVolume.label }} · 成交额</span>
            <span class="vk-val">{{ fmtYi(currentVolume.todayAmountYi, 1) }}</span>
            <span class="vk-sub">{{ sessionHint }}</span>
          </div>
          <div class="vm-kpi-item">
            <span class="vk-label">午盘收盘（11:30）成交额</span>
            <span class="vk-val">{{ fmtYi(currentVolume.noonAmountYi, 1) }}</span>
            <span class="vk-sub">半日量占比 {{ noonShare }}</span>
          </div>
          <div class="vm-kpi-item">
            <span class="vk-label">11:30 较上一交易日同期</span>
            <span class="vk-val" :class="toneClass(currentVolume.noonDeltaYi)">
              {{ fmtYi(currentVolume.noonDeltaYi, 1, true) }}
              <template v-if="currentVolume.noonDeltaPct !== null">
                ({{ fmtPct(currentVolume.noonDeltaPct) }})
              </template>
            </span>
            <span class="vk-sub">
              {{ currentVolume.noonDeltaYi === null ? "午盘数据暂缺（见底部说明）" : "正为放量，负为缩量" }}
            </span>
          </div>
        </div>

        <div class="vm-chart-wrap">
          <VChart
            v-if="currentVolume.historyDates.length"
            class="vm-chart"
            :option="volumeChartOption"
            autoresize
          />
          <StateBlock v-else state="empty" message="近期成交额历史暂无数据" />
        </div>
      </template>
      <StateBlock v-else state="empty" message="上游没有返回成交额数据" />
    </div>

    <!-- 右栏：沪深京两融余额拆解 -->
    <div class="vm-card">
      <div class="vm-head">
        <div>
          <span class="vm-title">全市场及沪/深/京两融余额</span>
          <span class="vm-sub">融资余额 · 融券余额 · 余额日变动与历史趋势</span>
        </div>
        <div v-if="margins.length" class="vm-tabs">
          <button
            v-for="m in margins"
            :key="m.key"
            type="button"
            class="vm-tab-btn"
            :class="{ active: currentMargin?.key === m.key }"
            @click="activeMarginKey = m.key"
          >
            {{ MARGIN_TAB[m.key] }}
          </button>
        </div>
      </div>

      <StateBlock
        v-if="!volumeMargin"
        :state="state === 'error' ? 'error' : 'loading'"
        :message="error"
        title="两融数据加载失败"
        @retry="reload"
      />
      <template v-else-if="currentMargin">
        <div class="vm-kpi-row">
          <div class="vm-kpi-item">
            <span class="vk-label">{{ currentMargin.label }}</span>
            <span class="vk-val">{{ fmtYi(currentMargin.totalBalanceYi) }}</span>
            <span class="vk-sub" :class="toneClass(currentMargin.dailyChangeYi)">
              余额日变动：{{ fmtYi(currentMargin.dailyChangeYi, 2, true) }}
            </span>
          </div>
          <div class="vm-kpi-item">
            <span class="vk-label">融资余额 / 当日融资买入</span>
            <span class="vk-val">{{ fmtYi(currentMargin.rzyeYi) }}</span>
            <span class="vk-sub">当日融资买入 {{ fmtYi(currentMargin.rzmreYi) }}</span>
          </div>
          <div class="vm-kpi-item">
            <span class="vk-label">融券余额</span>
            <span class="vk-val">{{ fmtYi(currentMargin.rqyeYi) }}</span>
            <span class="vk-sub">截至 {{ currentMargin.tradeDate ?? "—" }}（两融数据 T+1 披露）</span>
          </div>
        </div>

        <div class="vm-chart-wrap">
          <VChart
            v-if="currentMargin.historyDates.length"
            class="vm-chart"
            :option="marginChartOption"
            autoresize
          />
          <StateBlock v-else state="empty" message="近期两融历史暂无数据" />
        </div>
      </template>
      <StateBlock v-else state="empty" message="上游没有返回两融数据" />
    </div>

    <div v-if="volumeMargin" class="vm-footer">
      <PanoramaStatusBar :meta="volumeMargin.meta" :refresh-error="error" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import type { EChartsOption } from "echarts"

import {
  fetchVolumeMargin,
  type MarginKey,
  type VolumeKey,
} from "../../../api/aSharePanorama"
import { fmtPct, fmtYi, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import { VChart } from "../../../charts"
import PanoramaStatusBar from "../../../components/market/PanoramaStatusBar.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const VOLUME_TAB: Record<VolumeKey, string> = {
  all: "全市场",
  sh: "上证",
  sz: "深证",
  bj: "北证",
  chinext: "创业板",
  star: "科创板",
}
const MARGIN_TAB: Record<MarginKey, string> = {
  total: "全市场",
  sh: "沪市",
  sz: "深市",
  bj: "京市(北交)",
}

const { data: volumeMargin, state, error, reload } = usePanoramaResource(fetchVolumeMargin, {
  pollMs: 120_000,
})
const activeVolKey = ref<VolumeKey>("all")
const activeMarginKey = ref<MarginKey>("total")

const volumes = computed(() => volumeMargin.value?.volumes ?? [])
const margins = computed(() => volumeMargin.value?.margins ?? [])
const currentVolume = computed(
  () => volumes.value.find((v) => v.key === activeVolKey.value) ?? volumes.value[0],
)
const currentMargin = computed(
  () => margins.value.find((m) => m.key === activeMarginKey.value) ?? margins.value[0],
)

const sessionHint = computed(() => {
  const session = volumeMargin.value?.meta.session
  if (session === "intraday") return "盘中累计，尚未收盘"
  if (session === "close") return "全天成交，已收盘"
  return ""
})

const noonShare = computed(() => {
  const v = currentVolume.value
  if (!v || v.noonAmountYi === null || !v.todayAmountYi) return "—"
  return `${((v.noonAmountYi / v.todayAmountYi) * 100).toFixed(1)}%`
})

const volumeChartOption = computed<EChartsOption>(() => {
  const cur = currentVolume.value
  if (!cur) return {}
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: { left: 48, right: 14, top: 18, bottom: 22 },
    xAxis: {
      type: "category",
      data: cur.historyDates,
      axisLabel: { fontSize: 10.5, color: "#64748b" },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { fontSize: 10.5, color: "#64748b", formatter: "{value}亿" },
      splitLine: { lineStyle: { type: "dashed", color: "rgba(148,163,184,0.16)" } },
    },
    series: [
      {
        name: `${cur.label}(亿)`,
        type: "bar",
        barWidth: "44%",
        data: cur.historyAmountYi,
        itemStyle: {
          borderRadius: [4, 4, 0, 0],
          color: (params) =>
            params.dataIndex === cur.historyAmountYi.length - 1 ? "#f6465d" : "#3b82f6",
        },
      },
    ],
  }
})

const marginChartOption = computed<EChartsOption>(() => {
  const cur = currentMargin.value
  if (!cur) return {}
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    legend: { top: 0, right: 8, textStyle: { fontSize: 10.5, color: "#64748b" } },
    grid: { left: 52, right: 44, top: 24, bottom: 22 },
    xAxis: {
      type: "category",
      data: cur.historyDates,
      axisLabel: { fontSize: 10.5, color: "#64748b" },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: "#64748b", formatter: "{value}亿" },
        splitLine: { lineStyle: { type: "dashed", color: "rgba(148,163,184,0.16)" } },
      },
      {
        type: "value",
        scale: true,
        axisLabel: { fontSize: 10, color: "#64748b", formatter: "{value}亿" },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "两融余额(亿)",
        type: "line",
        data: cur.historyTotalYi,
        smooth: true,
        lineStyle: { width: 2.2, color: "#2563eb" },
        itemStyle: { color: "#2563eb" },
      },
      {
        name: "余额日变动(亿)",
        type: "bar",
        yAxisIndex: 1,
        barWidth: "38%",
        data: cur.historyChangeYi,
        itemStyle: {
          color: (params) => ((Number(params.value) || 0) >= 0 ? "#f6465d" : "#1fc37d"),
        },
      },
    ],
  }
})
</script>

<style scoped>
.vm-footer {
  grid-column: 1 / -1;
}

.volume-margin-panel {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.vm-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 13px 14px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.vm-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.vm-title {
  display: block;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--text-primary);
}

.vm-sub {
  font-size: 11px;
  color: var(--text-tertiary);
}

.vm-tabs {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.vm-tab-btn {
  padding: 3px 8px;
  font-size: 11px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
}

.vm-tab-btn.active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.35);
  font-weight: 600;
}

.vm-kpi-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.vm-kpi-item {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.vk-label {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.vk-val {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.vk-sub {
  font-size: 10.5px;
  color: var(--text-secondary);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

.vm-chart-wrap {
  height: 156px;
  width: 100%;
}

.vm-chart {
  width: 100%;
  height: 100%;
}

@media (max-width: 1180px) {
  .volume-margin-panel {
    grid-template-columns: 1fr;
  }
}
</style>
