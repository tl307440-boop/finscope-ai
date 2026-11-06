<script setup lang="ts">
/**
 * 连板分析：晋级率 / 断板率、连板梯队矩阵、昨日连板今日断板个股。
 * 口径：N 进 N+1 晋级率 = 昨日 N 板个股今日继续涨停的比例；断板率 = 1 − 整体晋级率。
 */
import { computed, watch } from "vue"

import { fetchLadder, type LadderStock } from "../api/aSharePanorama"
import { fmtNum, fmtPct, fmtWan, toneClass } from "../api/aShareFormat"
import { usePanoramaResource } from "../api/usePanoramaResource"
import MarketSplitLayout from "../components/market/MarketSplitLayout.vue"
import PanoramaStatusBar from "../components/market/PanoramaStatusBar.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import { useMarketLinkageStore } from "../stores/marketLinkage"
import JygsActionsPanel from "./parts/market/JygsActionsPanel.vue"

const linkageStore = useMarketLinkageStore()
const { data, state, error, reload } = usePanoramaResource(fetchLadder, { pollMs: 60_000 })

const kpis = computed(() => {
  const d = data.value
  if (!d) return []
  return [
    { label: "整体晋级率", value: d.overallPromotionRatePct, sub: "昨日连板今日继续涨停", tone: "rise" },
    { label: "整体断板率", value: d.overallBrokenRatePct, sub: "1 − 整体晋级率", tone: "fall" },
    { label: "1进2", value: d.rate1To2Pct, sub: d.count1To2Text, tone: "" },
    { label: "2进3", value: d.rate2To3Pct, sub: d.count2To3Text, tone: "" },
    { label: "3进4+", value: d.rate3To4PlusPct, sub: d.count3To4PlusText, tone: "" },
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
        <div v-for="k in kpis" :key="k.label" class="ld-kpi">
          <span class="ld-kpi-label">{{ k.label }}</span>
          <b class="ld-kpi-val" :class="k.tone === 'rise' ? 'is-rise' : k.tone === 'fall' ? 'is-fall' : ''">
            {{ fmtPct(k.value, 1, false) }}
          </b>
          <span class="ld-kpi-sub">{{ k.sub || " " }}</span>
        </div>
      </div>

      <!-- 梯队矩阵 -->
      <section class="ld-card">
        <header class="ld-card-head">
          <span class="ld-title">连板梯队</span>
          <span class="ld-sub">
            最高 {{ highestTier ? highestTier.streak : "—" }} 板 · 点击个股联动右侧详情
          </span>
        </header>
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
      </section>

      <!-- 断板股 -->
      <section class="ld-card">
        <header class="ld-card-head">
          <span class="ld-title">昨日连板 · 今日断板</span>
          <span class="ld-sub">昨日 2 板及以上、今日未能涨停的个股</span>
        </header>
        <StateBlock
          v-if="data.brokenStreaks.length === 0"
          state="empty"
          message="今日没有断板个股，或上游数据未返回（原因见页面底部状态条）"
        />
        <table v-else class="ld-table">
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
      </section>

      <JygsActionsPanel />

      <PanoramaStatusBar :meta="data.meta" :refresh-error="error" />
    </template>
  </MarketSplitLayout>
</template>

<style scoped>
.ld-kpis {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.ld-kpi {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 11px 14px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.ld-kpi-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.ld-kpi-val {
  font-family: var(--font-mono);
  font-size: 22px;
  color: var(--text-primary);
}

.ld-kpi-sub {
  font-size: 11px;
  color: var(--text-tertiary);
  min-height: 1.2em;
}

.ld-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: 12px 14px;
  overflow-x: auto;
}

.ld-card-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}

.ld-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.ld-sub {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.ld-matrix {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ld-tier {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 10px;
  padding-bottom: 8px;
  border-bottom: 1px dashed var(--border);
}

.ld-tier-label {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border-radius: 8px;
  background: rgba(246, 70, 93, 0.08);
  color: var(--rise);
}

.ld-tier-label b {
  font-size: 16px;
}

.ld-tier-label span {
  font-size: 11px;
  opacity: 0.8;
}

.ld-tier-stocks {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.ld-stock {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 190px;
  padding: 7px 10px;
  text-align: left;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  font: inherit;
  cursor: pointer;
}

.ld-stock:hover {
  border-color: var(--accent);
}

.ld-stock.selected {
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.35);
}

.ls-top {
  display: flex;
  justify-content: space-between;
  gap: 6px;
}

.ls-name {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.ls-pct {
  font-family: var(--font-mono);
  font-size: 12px;
}

.ls-theme {
  font-size: 11.5px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ls-meta {
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.ld-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.ld-table th {
  text-align: left;
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 5px 6px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.ld-table td {
  padding: 5px 6px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.ld-table .num {
  text-align: right;
  font-family: var(--font-mono);
}

.ld-table .strong {
  font-weight: 600;
}

.ld-table .reason {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-secondary);
}

.ld-table tr.clickable {
  cursor: pointer;
}

.ld-table tr.clickable:hover {
  background: var(--accent-subtle);
}

.code {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-tertiary);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

@media (max-width: 1100px) {
  .ld-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
