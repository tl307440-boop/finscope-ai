<template>
  <!-- 左栏：多维成交量能拆解 -->
  <MarketPanel
    box-id="indices.volume"
    :default-w="50"
    title="多维成交量能"
    subtitle="全天成交额 + 11:30 午盘量能对比（全市场 = 沪 + 深 + 京）"
    info="成交额取市场口径指数；午盘（11:30）对比的是上一个交易日同一时点"
    tone="accent"
    :icon="Histogram"
    :meta="volumeMargin?.meta ?? null"
    :refresh-error="error"
  >
    <div v-if="volumes.length" class="vm-tabs">
      <SectionTabs
        :model-value="currentVolume?.key ?? ''"
        :tabs="volumes.map((v) => ({ key: v.key, label: VOLUME_TAB[v.key] }))"
        @update:model-value="activeVolKey = $event as VolumeKey"
      />
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

      <div class="vm-chart-wrap mk-fill">
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
  </MarketPanel>

  <!-- 右栏：沪深京两融余额拆解 -->
  <MarketPanel
    box-id="indices.margin"
    :default-w="50"
    title="两融余额"
    subtitle="全市场及沪 / 深 / 京 · 融资余额 · 融券余额 · 日变动与历史趋势（T+1 披露）"
    info="两融余额「日变动」是相邻交易日的余额差，不是融资净买入"
    tone="ok"
    :icon="Coin"
  >
    <div v-if="margins.length" class="vm-tabs">
      <SectionTabs
        :model-value="currentMargin?.key ?? ''"
        :tabs="margins.map((m) => ({ key: m.key, label: MARGIN_TAB[m.key] }))"
        @update:model-value="activeMarginKey = $event as MarginKey"
      />
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

      <div class="vm-chart-wrap mk-fill">
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
  </MarketPanel>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import { Coin, Histogram } from "@element-plus/icons-vue"
import type { EChartsOption } from "echarts"

import {
  fetchVolumeMargin,
  type MarginKey,
  type VolumeKey,
} from "../../../api/aSharePanorama"
import { fmtPct, fmtYi, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import { VChart } from "../../../charts"
import MarketPanel from "../../../components/market/MarketPanel.vue"
import SectionTabs from "../../../components/ui/SectionTabs.vue"
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
.vm-tabs {
  margin-bottom: 12px;
}

.vm-kpi-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.vm-kpi-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
}

.vk-label {
  font-size: 12px;
  color: var(--faint);
}

.vk-val {
  font-family: var(--font-num);
  font-size: 20px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}

.vk-sub {
  font-size: 12px;
  line-height: 1.4;
  color: var(--muted);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

.vm-chart-wrap {
  height: 180px;
  width: 100%;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--line-soft);
}

.vm-chart {
  width: 100%;
  height: 100%;
}

/* 框窄了（用户拖窄 / 屏幕小）：三个指标改成上下排，不挤成一团 */
@container mk (max-width: 560px) {
  .vm-kpi-row {
    grid-template-columns: 1fr;
  }
}

/* 用户拖高了框：图表吃掉多出来的高度，不留空白 */
.mk-box.is-h .vm-chart-wrap {
  height: auto;
  min-height: 170px;
}
</style>