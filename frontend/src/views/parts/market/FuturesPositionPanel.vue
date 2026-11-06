<template>
  <section class="futures-position-panel" aria-label="股指期货机构多空持仓监控">
    <div class="fpp-head">
      <div>
        <span class="fpp-title">股指期货机构多空持仓（IF / IC / IM / IH）</span>
        <span class="fpp-sub">
          中金所公布的各品种多空持仓排名；同一机构跨月份合约已合并，仅包含进入榜单（前 20）的席位
        </span>
      </div>

      <div class="fpp-contract-tabs">
        <button
          v-for="ct in contracts"
          :key="ct.code"
          type="button"
          class="ct-tab-btn"
          :class="{ active: activeContract === ct.code }"
          @click="activeContract = ct.code"
        >
          {{ ct.code === "ALL" ? ct.label : `${ct.code} · ${ct.label}` }}
        </button>
      </div>
    </div>

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

    <div v-else class="fpp-body">
      <!-- 左侧：机构持仓与当日增减明细表 -->
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

      <!-- 右侧：红绿正负双向条形图 -->
      <div class="fpp-chart-card">
        <div class="chart-card-title">
          {{ activeContract }} 当日净多空变动（红正：净增多 / 绿负：净增空）
        </div>
        <div class="fpp-chart-wrap">
          <VChart class="fpp-chart" :option="barChartOption" autoresize />
        </div>
      </div>
    </div>

    <PanoramaStatusBar v-if="futures" :meta="futures.meta" :refresh-error="error" />
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import type { EChartsOption } from "echarts"

import { fetchFutures, type FuturesContract } from "../../../api/aSharePanorama"
import { fmtLots, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import { VChart } from "../../../charts"
import PanoramaStatusBar from "../../../components/market/PanoramaStatusBar.vue"
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

const filteredPositions = computed(() =>
  (futures.value?.rows ?? []).filter((r) => r.contractType === activeContract.value),
)

const emptyReason = computed(() => {
  const unavailable = futures.value?.meta.unavailable ?? {}
  return Object.values(unavailable)[0] ?? "中金所尚未公布该品种持仓排名（通常收盘后更新）"
})

const barChartOption = computed<EChartsOption>(() => {
  const rows = [...filteredPositions.value].reverse()
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: 125, right: 24, top: 10, bottom: 22 },
    xAxis: {
      type: "value",
      axisLabel: { fontSize: 10, color: "#64748b" },
      splitLine: { lineStyle: { type: "dashed", color: "rgba(148,163,184,0.16)" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((r) => r.institution),
      axisLabel: { fontSize: 11, color: "#475569" },
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
        })),
        label: {
          show: true,
          position: "right",
          fontSize: 10,
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
.futures-position-panel {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 13px 14px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.fpp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.fpp-title {
  display: block;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--text-primary);
}

.fpp-sub {
  font-size: 11px;
  color: var(--text-tertiary);
}

.fpp-contract-tabs {
  display: flex;
  gap: 6px;
}

.ct-tab-btn {
  padding: 5px 11px;
  font-size: 11.5px;
  border-radius: 7px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
}

.ct-tab-btn.active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.35);
  font-weight: 700;
}

.fpp-body {
  display: grid;
  grid-template-columns: 1.45fr 1fr;
  gap: 12px;
  align-items: stretch;
}

.fpp-table-wrap {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.fpp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.fpp-table th,
.fpp-table td {
  padding: 7px 9px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  white-space: nowrap;
}

.fpp-table th {
  background: var(--bg-elevated);
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 600;
}

.fpp-table td.num,
.fpp-table th.num {
  text-align: right;
  font-family: var(--font-mono);
}

.fpp-table tr.is-key-inst {
  background: rgba(246, 70, 93, 0.03);
}

.inst-name {
  font-weight: 600;
  color: var(--text-primary);
}

.key-badge {
  margin-left: 5px;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(245, 158, 11, 0.14);
  color: #d97706;
  font-weight: 600;
}

.is-rise {
  color: var(--rise);
  font-weight: 600;
}

.is-fall {
  color: var(--fall);
  font-weight: 600;
}

.net-chg-pill {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.pill-rise {
  background: rgba(246, 70, 93, 0.14);
  color: var(--rise);
}

.pill-fall {
  background: rgba(31, 195, 125, 0.14);
  color: var(--fall);
}

.fpp-chart-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 9px 10px;
  background: var(--bg-elevated);
  display: flex;
  flex-direction: column;
}

.chart-card-title {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 4px;
}

.fpp-chart-wrap {
  flex: 1;
  min-height: 185px;
}

.fpp-chart {
  width: 100%;
  height: 100%;
}

@media (max-width: 1200px) {
  .fpp-body {
    grid-template-columns: 1fr;
  }
}
</style>
