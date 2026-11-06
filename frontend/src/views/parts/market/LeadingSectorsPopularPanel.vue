<script setup lang="ts">
/**
 * 领涨板块 + 人气股。点击任意个股联动右侧个股详情。
 * 人气股来自同花顺人气榜，板块来自东方财富概念板块涨幅榜，均为实时/当日收盘数据。
 */
import { watch } from "vue"
import { Flag, Star } from "@element-plus/icons-vue"

import { fetchLeading } from "../../../api/aSharePanorama"
import { fmtPct, fmtWan, fmtYi, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import MarketPanel from "../../../components/market/MarketPanel.vue"
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
  <section class="leading-wrap" aria-label="领涨板块与人气股">
    <StateBlock
      v-if="!data"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="领涨板块与人气股加载失败"
      :rows="4"
      @retry="reload"
    />
    <div v-else class="lp-grid">
      <MarketPanel
        title="领涨板块"
        subtitle="概念板块涨幅榜 · 点击领涨股联动右侧个股详情"
        info="板块来自东方财富概念板块涨幅榜；主力净流入单位：亿元"
        tone="orange"
        :icon="Flag"
        :meta="data.meta"
        :refresh-error="error"
      >
        <StateBlock
          v-if="data.leadingSectors.length === 0"
          state="empty"
          message="上游没有返回板块数据"
        />
        <div v-else class="lp-table-wrap">
          <table class="lp-table">
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
      </MarketPanel>

      <MarketPanel
        title="人气股"
        subtitle="同花顺人气榜 · 点击个股联动右侧详情"
        info="人气排名来自同花顺热股榜；封单金额单位：万元 / 亿元"
        tone="danger"
        :icon="Star"
      >
        <StateBlock
          v-if="data.popularStocks.length === 0"
          state="empty"
          message="上游没有返回人气榜数据"
        />
        <div v-else class="lp-table-wrap">
          <table class="lp-table">
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
      </MarketPanel>
    </div>
  </section>
</template>
<style scoped>
/* 按本栏实际宽度（而不是屏幕宽度）切单双列：右侧个股详情展开时主栏只有约 1100px */
.leading-wrap {
  container-type: inline-size;
}

.lp-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--gap, 14px);
  align-items: start;
}

@container (max-width: 1180px) {
  .lp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.lp-table-wrap {
  max-height: 460px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.lp-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}

.lp-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  background: var(--panel-soft);
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}

.lp-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--line-soft);
  white-space: nowrap;
}

.lp-table tbody tr:last-child td {
  border-bottom: none;
}

.lp-table .num {
  text-align: right;
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
}

.lp-table .strong {
  font-weight: 600;
  color: var(--text-strong);
}

.lp-table .reason {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--muted);
}

.lp-table tr.clickable {
  cursor: pointer;
}

.lp-table tbody tr:hover td,
.lp-table tr.active td {
  background: var(--accent-soft);
}

.code {
  margin-left: 4px;
  font-family: var(--font-num);
  font-size: 12px;
  font-weight: 400;
  color: var(--faint);
}

.streak {
  margin-left: 4px;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  color: var(--rise);
  background: color-mix(in srgb, var(--rise) 12%, transparent);
}

.muted {
  color: var(--faint);
}

.link-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0;
  border: 0;
  background: none;
  font: inherit;
  color: var(--text);
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
</style>