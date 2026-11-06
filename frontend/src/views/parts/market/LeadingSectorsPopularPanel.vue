<script setup lang="ts">
/**
 * 领涨板块 + 人气股。点击任意个股联动右侧个股详情。
 * 人气股来自同花顺人气榜，板块来自东方财富概念板块涨幅榜，均为实时/当日收盘数据。
 */
import { watch } from "vue"

import { fetchLeading } from "../../../api/aSharePanorama"
import { fmtPct, fmtWan, fmtYi, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import PanoramaStatusBar from "../../../components/market/PanoramaStatusBar.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"
import { useMarketLinkageStore } from "../../../stores/marketLinkage"

const linkageStore = useMarketLinkageStore()
const { data, state, error, reload } = usePanoramaResource(fetchLeading, { pollMs: 60_000 })

// 第一次进入页面时默认选中人气第一的个股，避免右侧面板空着；用户选过就不再覆盖。
watch(
  data,
  (value) => {
    const first = value?.popularStocks[0]
    if (first && !linkageStore.selectedStockCode) linkageStore.selectStock(first.code)
  },
  { immediate: true },
)
</script>

<template>
  <section class="leading-panel" aria-label="领涨板块与人气股">
    <StateBlock
      v-if="!data"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="领涨板块与人气股加载失败"
      :rows="4"
      @retry="reload"
    />
    <template v-else>
      <div class="lp-grid">
        <div class="lp-card">
          <div class="lp-head">
            <span class="lp-title">领涨板块</span>
            <span class="lp-sub">概念板块涨幅榜 · 点击领涨股联动个股详情</span>
          </div>
          <StateBlock
            v-if="data.leadingSectors.length === 0"
            state="empty"
            message="上游没有返回板块数据"
          />
          <table v-else class="lp-table">
            <thead>
              <tr>
                <th>板块</th>
                <th class="num">涨幅</th>
                <th class="num">涨停</th>
                <th class="num">主力净流入</th>
                <th>领涨股</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in data.leadingSectors" :key="s.code">
                <td class="strong">{{ s.name }}</td>
                <td class="num" :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</td>
                <td class="num">{{ s.limitUpCount ?? "—" }}</td>
                <td class="num" :class="toneClass(s.netInflowYi)">{{ fmtYi(s.netInflowYi, 2, true) }}</td>
                <td>
                  <button
                    v-if="s.leaderCode"
                    type="button"
                    class="link-btn"
                    @click="linkageStore.selectStock(s.leaderCode)"
                  >
                    {{ s.leaderName }}
                    <span :class="toneClass(s.leaderChangePct)">{{ fmtPct(s.leaderChangePct) }}</span>
                    <span v-if="s.leaderStreak" class="streak">{{ s.leaderStreak }}</span>
                  </button>
                  <span v-else class="muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="lp-card">
          <div class="lp-head">
            <span class="lp-title">人气股</span>
            <span class="lp-sub">同花顺人气榜 · 点击个股联动详情</span>
          </div>
          <StateBlock
            v-if="data.popularStocks.length === 0"
            state="empty"
            message="上游没有返回人气榜数据"
          />
          <table v-else class="lp-table">
            <thead>
              <tr>
                <th class="num">#</th>
                <th>个股</th>
                <th class="num">涨幅</th>
                <th class="num">换手</th>
                <th class="num">封单</th>
                <th>板块 / 催化</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="p in data.popularStocks"
                :key="p.code"
                class="clickable"
                :class="{ active: linkageStore.selectedStockCode === p.code }"
                @click="linkageStore.selectStock(p.code)"
              >
                <td class="num muted">{{ p.rank }}</td>
                <td class="strong">
                  {{ p.name }}
                  <span class="code">{{ p.code }}</span>
                  <span v-if="p.streakText" class="streak">{{ p.streakText }}</span>
                </td>
                <td class="num" :class="toneClass(p.changePct)">{{ fmtPct(p.changePct) }}</td>
                <td class="num">{{ fmtPct(p.turnoverPct, 2, false) }}</td>
                <td class="num">{{ p.sealAmountWan ? fmtWan(p.sealAmountWan) : "—" }}</td>
                <td class="reason" :title="p.catalyst">{{ p.sector || p.catalyst || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <PanoramaStatusBar :meta="data.meta" :refresh-error="error" />
    </template>
  </section>
</template>

<style scoped>
.leading-panel {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.lp-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.lp-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 12px 14px;
  min-width: 0;
  overflow-x: auto;
}

.lp-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.lp-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.lp-sub {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.lp-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.lp-table th {
  text-align: left;
  font-weight: 600;
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 4px 6px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.lp-table td {
  padding: 5px 6px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.lp-table .num {
  text-align: right;
  font-family: var(--font-mono);
}

.lp-table .strong {
  font-weight: 600;
  color: var(--text-primary);
}

.lp-table .reason {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-secondary);
}

.lp-table tr.clickable {
  cursor: pointer;
}

.lp-table tr.clickable:hover,
.lp-table tr.active {
  background: var(--accent-subtle);
}

.code {
  margin-left: 4px;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.streak {
  margin-left: 4px;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--rise);
  background: rgba(246, 70, 93, 0.12);
}

.muted {
  color: var(--text-tertiary);
}

.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--text-primary);
  cursor: pointer;
}

.link-btn:hover {
  color: var(--accent);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

@media (max-width: 1440px) {
  .lp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
