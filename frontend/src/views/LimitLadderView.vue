<script setup lang="ts">
/**
 * 连板分析：晋级率 / 断板率、连板梯队矩阵、昨日连板今日断板个股。
 * 口径：N 进 N+1 晋级率 = 昨日 N 板个股今日继续涨停的比例；断板率 = 1 − 整体晋级率。
 */
import { computed, watch } from "vue"
import { Trophy, Warning } from "@element-plus/icons-vue"

import { fetchLadder, type LadderStock } from "../api/aSharePanorama"
import { fmtNum, fmtPct, fmtWan, toneClass } from "../api/aShareFormat"
import { usePanoramaResource } from "../api/usePanoramaResource"
import MarketSplitLayout from "../components/market/MarketSplitLayout.vue"
import MarketPanel from "../components/market/MarketPanel.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StatCard from "../components/ui/StatCard.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import { useMarketLinkageStore } from "../stores/marketLinkage"
import JygsActionsPanel from "./parts/market/JygsActionsPanel.vue"

const linkageStore = useMarketLinkageStore()
const { data, state, error, reload } = usePanoramaResource(fetchLadder, { pollMs: 60_000 })

const kpis = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { label: "整体晋级率", value: d.overallPromotionRatePct, sub: "昨日连板今日继续涨停", tone: "danger" },
    { label: "整体断板率", value: d.overallBrokenRatePct, sub: "1 − 整体晋级率", tone: "ok" },
    { label: "1进2", value: d.rate1To2Pct, sub: d.count1To2Text, tone: "accent" },
    { label: "2进3", value: d.rate2To3Pct, sub: d.count2To3Text, tone: "accent" },
    { label: "3进4+", value: d.rate3To4PlusPct, sub: d.count3To4PlusText, tone: "accent" },
  ]
})

const highestTier = computed(() => data.value?.tiers[0] ?? null)

// 第一次进入时默认选中最高板龙头；用户已经选过则不覆盖。
watch(
  highestTier,
  (tier) => {
    const leader = tier?.stocks[0]
    if (leader && !linkageStore.selectedStockCode) linkageStore.selectStock(leader.code)
  },
  { immediate: true },
)

function sealText(stock: LadderStock): string {
  return stock.sealAmountWan ? `封单 ${fmtWan(stock.sealAmountWan)}` : "未封板/无封单"
}
</script>

<template>
  <MarketSplitLayout>
    <PageHeader title="连板分析" subtitle="晋级率 · 空间高度 · 连板梯队 · 断板股">
      <template #actions>
        <el-button :loading="state === 'loading'" @click="reload">刷新</el-button>
      </template>
    </PageHeader>

    <StateBlock
      v-if="!data"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="连板数据加载失败"
      :rows="5"
      @retry="reload"
    />

    <template v-else>
      <!-- 晋级率 KPI -->
      <div class="ld-kpis">
        <StatCard
          v-for="k in kpis"
          :key="k.label"
          :title="k.label"
          :value="fmtPct(k.value, 1, false)"
          :caption="k.sub || ''"
          :tone="k.tone as 'danger' | 'ok' | 'accent'"
        />
      </div>

      <!-- 梯队矩阵 -->
      <MarketPanel
        box-id="ladder.tiers"
        title="连板梯队"
        :subtitle="`最高 ${highestTier ? highestTier.streak : '—'} 板 · 点击个股联动右侧详情`"
        info="N 进 N+1 晋级率 = 昨日 N 板个股今日继续涨停的比例；断板率 = 1 − 整体晋级率"
        tone="danger"
        :icon="Trophy"
        :meta="data.meta"
        :refresh-error="error"
      >
        <StateBlock
          v-if="data.tiers.length === 0"
          state="empty"
          message="当日没有连板个股，或上游涨停池暂未返回数据（原因见页面底部状态条）"
        />
        <div v-else class="ld-matrix">
          <div v-for="tier in data.tiers" :key="tier.streak" class="ld-tier">
            <div class="ld-tier-label">
              <b>{{ tier.label }}</b>
              <span>{{ tier.stocks.length }} 只</span>
            </div>
            <div class="ld-tier-stocks">
              <button
                v-for="s in tier.stocks"
                :key="s.code"
                type="button"
                class="ld-stock"
                :class="{ selected: linkageStore.selectedStockCode === s.code }"
                :title="s.reason"
                @click="linkageStore.selectStock(s.code)"
              >
                <span class="ls-top">
                  <span class="ls-name">{{ s.name }}</span>
                  <span class="ls-pct" :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</span>
                </span>
                <span class="ls-theme">{{ s.reason || s.actionSector || s.sector || "—" }}</span>
                <span class="ls-meta">
                  首封 {{ s.firstSealTime || "—" }}
                  <template v-if="s.breakCount > 0"> · 炸板 {{ s.breakCount }} 次</template>
                  · {{ sealText(s) }}
                </span>
              </button>
            </div>
          </div>
        </div>
      </MarketPanel>

      <!-- 断板股 -->
      <MarketPanel
        box-id="ladder.broken"
        title="昨日连板 · 今日断板"
        subtitle="昨日 2 板及以上、今日未能涨停的个股"
        tone="warn"
        :icon="Warning"
      >
        <StateBlock
          v-if="data.brokenStreaks.length === 0"
          state="empty"
          message="今日没有断板个股，或上游数据未返回（原因见页面底部状态条）"
        />
        <div v-else class="ld-table-wrap mk-fill">
          <table class="ld-table">
            <thead>
              <tr>
                <th>个股</th>
                <th class="num">昨日高度</th>
                <th class="num">现价</th>
                <th class="num">今日涨跌</th>
                <th class="num">距日内高点回落</th>
                <th class="num">换手</th>
                <th>板块 / 题材</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="b in data.brokenStreaks"
                :key="b.code"
                class="clickable"
                @click="linkageStore.selectStock(b.code)"
              >
                <td class="strong">{{ b.name }} <span class="code">{{ b.code }}</span></td>
                <td class="num">{{ b.yesterdayStreak }} 板</td>
                <td class="num">{{ fmtNum(b.price) }}</td>
                <td class="num" :class="toneClass(b.changePct)">{{ fmtPct(b.changePct) }}</td>
                <td class="num is-fall">{{ fmtPct(b.drawdownFromHighPct, 2, false) }}</td>
                <td class="num">{{ fmtPct(b.turnoverPct, 2, false) }}</td>
                <td class="reason" :title="b.reason">{{ b.sector || b.reason || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </MarketPanel>

      <JygsActionsPanel />
    </template>
  </MarketSplitLayout>
</template>

<style scoped>
.ld-kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: var(--gap, 14px);
}

.ld-matrix {
  display: flex;
  flex-direction: column;
}

.ld-tier {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line-soft);
}

.ld-tier:first-child {
  padding-top: 0;
}

.ld-tier:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.ld-tier-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border: 1px solid color-mix(in srgb, var(--rise) 28%, transparent);
  border-radius: var(--radius-sm);
  background: color-mix(in srgb, var(--rise) 10%, transparent);
  color: var(--rise);
}

.ld-tier-label b {
  font-size: 17px;
}

.ld-tier-label span {
  font-size: 12px;
  opacity: 0.85;
}

.ld-tier-stocks {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
}

.ld-stock {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  padding: 9px 12px;
  text-align: left;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-soft);
  font: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.ld-stock:hover {
  border-color: var(--accent);
}

.ld-stock.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent);
}

.ls-top {
  display: flex;
  justify-content: space-between;
  gap: 6px;
}

.ls-name {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-strong);
}

.ls-pct {
  font-family: var(--font-num);
  font-size: 13px;
  font-weight: 600;
}

.ls-theme {
  font-size: 12px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ls-meta {
  padding-top: 4px;
  border-top: 1px dashed var(--line-soft);
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--faint);
}

.ld-table-wrap {
  max-height: 480px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.ld-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}

.ld-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--panel-soft);
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}

.ld-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-soft);
  white-space: nowrap;
}

.ld-table tbody tr:last-child td {
  border-bottom: none;
}

.ld-table .num {
  text-align: right;
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
}

.ld-table .strong {
  font-weight: 600;
  color: var(--text-strong);
}

.ld-table .reason {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--muted);
}

.ld-table tr.clickable {
  cursor: pointer;
}

.ld-table tr.clickable:hover td {
  background: var(--accent-soft);
}

.code {
  margin-left: 4px;
  font-family: var(--font-num);
  font-size: 12px;
  font-weight: 400;
  color: var(--faint);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

/* 用户拖过高度：断板表撑满框、在框内滚动（表头吸顶），不再用固定的 480px 上限 */
.mk-box.is-h .ld-table-wrap {
  max-height: none;
}

@media (max-width: 1280px) {
  .ld-kpis {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .ld-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>