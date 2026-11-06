<template>
  <MarketPanel
    box-id="indices.futures"
    title="股指期货机构多空持仓（IF / IC / IM / IH）"
    subtitle="中金所公布的各品种多空持仓排名；同一机构跨月份合约已合并，仅包含进入榜单（前 20）的席位"
    info="持仓单位：手；净持仓 = 多单 − 空单；当日净多空变动红色为净增多、绿色为净增空"
    tone="violet"
    :icon="Rank"
    :meta="futures?.meta ?? null"
    :refresh-error="error"
  >
    <template #actions>
      <SectionTabs
        :model-value="activeContract"
        :tabs="contractTabs"
        @update:model-value="activeContract = $event as FuturesContract"
      />
    </template>

    <StateBlock
      v-if="!futures"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="期指持仓加载失败"
      :rows="4"
      @retry="reload"
    />
    <StateBlock
      v-else-if="filteredPositions.length === 0"
      state="empty"
      :message="emptyReason"
    />

    <div v-else class="fpp-body mk-fill">
      <!-- 机构持仓与当日增减明细表 -->
      <PanelSection title="机构持仓与当日增减" :note="`共 ${filteredPositions.length} 家`">
      <div class="fpp-table-wrap">
        <table class="fpp-table">
          <thead>
            <tr>
              <th>机构席位</th>
              <th class="num">多单持仓</th>
              <th class="num">当日多单增减</th>
              <th class="num">空单持仓</th>
              <th class="num">当日空单增减</th>
              <th class="num">净持仓(多-空)</th>
              <th class="num">当日净多空变动</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in filteredPositions"
              :key="`${row.contractType}-${row.institution}`"
              :class="{ 'is-key-inst': row.isKeyInstitution }"
            >
              <td>
                <span class="inst-name">{{ row.institution }}</span>
                <span v-if="row.isKeyInstitution" class="key-badge">重点机构</span>
              </td>
              <td class="num">{{ fmtLots(row.longPos) }}</td>
              <td class="num" :class="toneClass(row.longChange)">{{ fmtLots(row.longChange, true) }}</td>
              <td class="num">{{ fmtLots(row.shortPos) }}</td>
              <td class="num" :class="toneClass(-row.shortChange)">{{ fmtLots(row.shortChange, true) }}</td>
              <td class="num" :class="toneClass(row.netPos)">{{ fmtLots(row.netPos, true) }}</td>
              <td class="num">
                <span
                  class="net-chg-pill"
                  :class="row.netChange >= 0 ? 'pill-rise' : 'pill-fall'"
                >
                  {{ row.netChange >= 0 ? "净多" : "净空" }} {{ fmtLots(row.netChange, true) }}手
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      </PanelSection>

      <!-- 红绿正负双向条形图 -->
      <PanelSection
        class="mk-fill"
        :title="`${activeContract === 'ALL' ? '四品种合计' : activeContract} 当日净多空变动`"
        note="红：净增多　绿：净增空（手）"
      >
        <div class="fpp-chart-wrap" :style="{ '--fpp-h': `${chartHeight}px` }">
          <VChart class="fpp-chart" :option="barChartOption" autoresize />
        </div>
      </PanelSection>
    </div>
  </MarketPanel>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import { Rank } from "@element-plus/icons-vue"
import type { EChartsOption } from "echarts"

import { fetchFutures, type FuturesContract } from "../../../api/aSharePanorama"
import { fmtLots, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import { VChart } from "../../../charts"
import MarketPanel from "../../../components/market/MarketPanel.vue"
import PanelSection from "../../../components/market/PanelSection.vue"
import SectionTabs from "../../../components/ui/SectionTabs.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const { data: futures, state, error, reload } = usePanoramaResource(fetchFutures, {
  pollMs: 300_000,
})
const activeContract = ref<FuturesContract>("ALL")

const contracts: { code: FuturesContract; label: string }[] = [
  { code: "ALL", label: "四品种合计" },
  { code: "IF", label: "沪深300" },
  { code: "IC", label: "中证500" },
  { code: "IM", label: "中证1000" },
  { code: "IH", label: "上证50" },
]

const contractTabs = contracts.map((ct) => ({
  key: ct.code,
  label: ct.code === "ALL" ? ct.label : `${ct.code} · ${ct.label}`,
}))

const filteredPositions = computed(() =>
  (futures.value?.rows ?? []).filter((r) => r.contractType === activeContract.value),
)

const emptyReason = computed(() => {
  const unavailable = futures.value?.meta.unavailable ?? {}
  return Object.values(unavailable)[0] ?? "中金所尚未公布该品种持仓排名（通常收盘后更新）"
})

/** 每家机构一行：行数多时图表跟着变高，标签不挤在一起。 */
const chartHeight = computed(() => Math.max(220, filteredPositions.value.length * 26 + 40))

const barChartOption = computed<EChartsOption>(() => {
  const rows = [...filteredPositions.value].reverse()
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 150, right: 60, top: 10, bottom: 24 },
    xAxis: {
      type: "value",
      // 两端各留一截，正负值的数字标签才放得下，不压到机构名
      min: (v) => Math.floor(v.min * 1.2),
      max: (v) => Math.ceil(v.max * 1.2),
      axisLabel: { fontSize: 12, color: "#64748b" },
      splitLine: { lineStyle: { type: "dashed", color: "rgba(148,163,184,0.16)" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.institution),
      axisLabel: { fontSize: 12, color: "#64748b", width: 138, overflow: "truncate" },
      axisTick: { show: false },
      // 机构名固定在图左边，不跟着零轴走
      axisLine: { show: false },
      position: "left",
    },
    series: [
      {
        name: "当日净多空变动(手)",
        type: "bar",
        barWidth: "52%",
        data: rows.map((r) => ({
          value: r.netChange,
          itemStyle: {
            color: r.netChange >= 0 ? "#f6465d" : "#1fc37d",
            borderRadius: r.netChange >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
          },
          // 负值的数字放在条的左端，不和机构名、零轴挤在一起
          label: { position: r.netChange >= 0 ? ("right" as const) : ("left" as const) },
        })),
        label: {
          show: true,
          position: "right",
          fontSize: 12,
          color: "#94a3b8",
          textBorderWidth: 0,
          formatter: (p) => {
            const v = Number(p.value)
            return v >= 0 ? `+${v}` : `${v}`
          },
        },
      },
    ],
  }
})
</script>

<style scoped>
.fpp-body {
  display: flex;
  flex-direction: column;
}

.fpp-table-wrap {
  max-height: 420px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.fpp-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}

.fpp-table th,
.fpp-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-soft);
  text-align: left;
  white-space: nowrap;
}

.fpp-table tbody tr:last-child td {
  border-bottom: none;
}

.fpp-table tbody tr:hover td {
  background: var(--accent-soft);
}

.fpp-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--panel-soft);
  border-bottom: 1px solid var(--line);
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.fpp-table td.num,
.fpp-table th.num {
  text-align: right;
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
}

.fpp-table tr.is-key-inst td {
  background: color-mix(in srgb, var(--rise) 4%, transparent);
}

.inst-name {
  font-weight: 600;
  color: var(--text-strong);
}

.key-badge {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  color: var(--warn);
  font-size: 11px;
  font-weight: 600;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

.net-chg-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}

.pill-rise {
  background: color-mix(in srgb, var(--rise) 14%, transparent);
  color: var(--rise);
}

.pill-fall {
  background: color-mix(in srgb, var(--fall) 14%, transparent);
  color: var(--fall);
}

.fpp-chart-wrap {
  width: 100%;
  height: var(--fpp-h, 220px);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  padding: 6px 8px;
}

.fpp-chart {
  width: 100%;
  height: 100%;
}

/* 用户拖过高度：表格保持自己的滚动区（表头吸顶），图表吃掉多出来的高度 */
.mk-box.is-h .fpp-body > .panel-section {
  flex: none;
}

.mk-box.is-h .fpp-body > .panel-section.mk-fill {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
}

.mk-box.is-h .fpp-chart-wrap {
  flex: 1 1 auto;
  height: auto;
  min-height: var(--fpp-h, 220px);
}
</style>