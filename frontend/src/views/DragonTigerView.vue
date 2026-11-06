<script setup lang="ts">
/**
 * 龙虎榜分析：只看「关注名单里的游资/席位」当天上榜的个股。
 * 营业部 → 游资的归属由席位字典决定（席位管理里可增删，保存在浏览器本地），
 * 后端只提供交易所公布的原始买卖席位明细。
 */
import { computed, onMounted, ref, shallowRef } from "vue"

import { errorMessage, fetchDragonTiger, type DragonTigerData } from "../api/aSharePanorama"
import { fmtPct, fmtWan, toneClass } from "../api/aShareFormat"
import { groupDragonTiger } from "../api/dragonTigerGrouping"
import MarketSplitLayout from "../components/market/MarketSplitLayout.vue"
import ResizableBox from "../components/market/ResizableBox.vue"
import PanoramaStatusBar from "../components/market/PanoramaStatusBar.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import type { SeatCategory } from "../config/hotMoneySeatsConfig"
import { useMarketLinkageStore } from "../stores/marketLinkage"

const linkageStore = useMarketLinkageStore()

const data = shallowRef<DragonTigerData | null>(null)
const phase = ref<"loading" | "ready" | "error">("loading")
const failure = ref("")
const pickedDate = ref("")
const showAllStocks = ref(false)

async function load(): Promise<void> {
  phase.value = data.value ? "ready" : "loading"
  try {
    data.value = await fetchDragonTiger(pickedDate.value || undefined)
    failure.value = ""
    phase.value = "ready"
  } catch (e) {
    failure.value = errorMessage(e)
    phase.value = data.value ? "ready" : "error"
  }
}

onMounted(() => void load())

const grouped = computed(() =>
  data.value ? groupDragonTiger(data.value, linkageStore.seatGroups) : null,
)

const visibleRows = computed(() => {
  const rows = grouped.value?.rows ?? []
  const filter = linkageStore.activeSeatIdFilter
  return filter ? rows.filter((r) => r.groupId === filter) : rows
})

const hasNoBoard = computed(() => (data.value?.stocks.length ?? 0) === 0)

function badgeClass(category: SeatCategory): string {
  if (category === "知名游资") return "role-hotmoney"
  if (category === "量化打板") return "role-quant"
  if (category === "外资") return "role-foreign"
  if (category === "机构专用") return "role-inst"
  return "role-lhasa"
}

function capsuleTone(net: number): string {
  if (net > 0) return "cap-buy"
  if (net < 0) return "cap-sell"
  return ""
}
</script>

<template>
  <MarketSplitLayout>
    <PageHeader title="龙虎榜分析" subtitle="关注名单中的游资 / 席位当日上榜个股 · 红色买入 · 绿色卖出">
      <template #actions>
        <el-date-picker
          v-model="pickedDate"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="最新交易日"
          style="width: 150px"
          @change="load"
        />
        <el-button :loading="phase === 'loading'" @click="load">刷新</el-button>
        <el-button @click="linkageStore.openSeatManager()">席位管理</el-button>
      </template>
    </PageHeader>

    <StateBlock
      v-if="phase !== 'ready' || !data"
      :state="phase === 'error' ? 'error' : 'loading'"
      :message="failure"
      title="龙虎榜加载失败"
      :rows="5"
      @retry="load"
    />

    <template v-else>
      <StateBlock
        v-if="hasNoBoard"
        state="empty"
        title="当日龙虎榜暂无数据"
        message="交易所通常在收盘后 17:00 左右公布；非交易日也没有龙虎榜。可在右上角选择历史交易日。"
      />

      <template v-else-if="grouped">
        <!-- 席位筛选 -->
        <div class="dt-filter-bar">
          <span class="dt-filter-label">今日上榜关注席位：</span>
          <button
            type="button"
            class="dt-chip"
            :class="{ active: !linkageStore.activeSeatIdFilter }"
            @click="linkageStore.focusSeat('')"
          >
            全部 ({{ grouped.rows.length }})
          </button>
          <button
            v-for="row in grouped.rows"
            :key="row.groupId"
            type="button"
            class="dt-chip"
            :class="{ active: linkageStore.activeSeatIdFilter === row.groupId }"
            @click="linkageStore.setSeatFilter(row.groupId)"
          >
            {{ row.name }}
          </button>
          <span class="dt-coverage">
            席位字典命中 {{ grouped.matchedSeatCount }} / {{ grouped.totalSeatCount }} 个上榜席位
          </span>
        </div>

        <StateBlock
          v-if="visibleRows.length === 0"
          state="empty"
          :message="
            grouped.rows.length === 0
              ? '关注名单中的席位今天都没有上榜（可在席位管理里调整关注名单）'
              : '当前筛选下没有席位'
          "
        />

        <!-- 席位 → 个股胶囊 -->
        <template v-else>
          <ResizableBox v-for="row in visibleRows" :id="`dragon.seat.${row.groupId}`" :key="row.groupId">
            <article class="dt-card">
              <header class="dt-card-head">
                <div class="dt-card-title">
                  <span class="dt-name">{{ row.name }}</span>
                  <span class="dt-badge" :class="badgeClass(row.category)">{{ row.category }}</span>
                  <span class="dt-style">{{ row.styleTag }}</span>
                </div>
                <div class="dt-net" :class="toneClass(row.netWan)">
                  <span class="dt-net-label">席位净额</span>
                  <b>{{ fmtWan(row.netWan, true) }}</b>
                  <span class="dt-bs">买 {{ fmtWan(row.buyWan) }} · 卖 {{ fmtWan(row.sellWan) }}</span>
                </div>
              </header>

              <div class="dt-capsules">
                <button
                  v-for="s in row.stocks"
                  :key="s.code"
                  type="button"
                  class="dt-capsule"
                  :class="[
                    capsuleTone(s.netWan),
                    { selected: linkageStore.isStockActive(s.code) },
                  ]"
                  :title="`${s.reason}\n${s.branches.join('\n')}`"
                  @click="linkageStore.selectStock(s.code)"
                >
                  <span class="cap-name">{{ s.name }}</span>
                  <span class="cap-pct" :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</span>
                  <span class="cap-amt">
                    买 {{ fmtWan(s.buyWan) }} / 卖 {{ fmtWan(s.sellWan) }}
                  </span>
                  <b class="cap-net">净 {{ fmtWan(s.netWan, true) }}</b>
                </button>
              </div>
            </article>
          </ResizableBox>
        </template>

        <!-- 外资 / 机构 / 拉萨天团 汇总 -->
        <ResizableBox v-for="b in grouped.summary" :id="`dragon.sum.${b.key}`" :key="b.key" :default-w="33.333">
          <div class="dt-sum-card">
            <span class="dt-sum-label">{{ b.label }}</span>
            <b class="dt-sum-net" :class="toneClass(b.netWan)">{{ fmtWan(b.netWan, true) }}</b>
            <span class="dt-sum-sub">
              买 {{ fmtWan(b.buyWan) }} · 卖 {{ fmtWan(b.sellWan) }}
            </span>
            <span class="dt-sum-sub">{{ b.seatCount }} 个席位 · {{ b.stockCount }} 只个股</span>
          </div>
        </ResizableBox>

        <!-- 全部上榜个股（折叠） -->
        <ResizableBox :id="showAllStocks ? 'dragon.all' : ''">
          <div class="dt-all">
            <button type="button" class="dt-all-toggle" @click="showAllStocks = !showAllStocks">
              {{ showAllStocks ? "收起" : "展开" }}今日全部上榜个股（{{ data.stocks.length }} 只）
            </button>
            <div v-if="showAllStocks" class="dt-table-wrap mk-fill">
              <table class="dt-table">
                <thead>
                  <tr>
                    <th>个股</th>
                    <th class="num">涨跌幅</th>
                    <th class="num">龙虎榜买入</th>
                    <th class="num">龙虎榜卖出</th>
                    <th class="num">净买入</th>
                    <th>上榜原因</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="s in data.stocks"
                    :key="s.code"
                    class="clickable"
                    @click="linkageStore.selectStock(s.code)"
                  >
                    <td>{{ s.name }} <span class="code">{{ s.code }}</span></td>
                    <td class="num" :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</td>
                    <td class="num">{{ fmtWan(s.buyWan) }}</td>
                    <td class="num">{{ fmtWan(s.sellWan) }}</td>
                    <td class="num" :class="toneClass(s.netWan)">{{ fmtWan(s.netWan, true) }}</td>
                    <td class="reason">{{ s.reason }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </ResizableBox>
      </template>

      <div class="dt-foot">
        <PanoramaStatusBar :meta="data.meta" :refresh-error="failure" />
      </div>
    </template>
  </MarketSplitLayout>
</template>

<style scoped>
.dt-filter-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: linear-gradient(180deg, var(--panel-top), var(--panel));
  box-shadow: var(--shadow-card);
}

.dt-foot {
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-soft);
}

.dt-filter-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.dt-chip {
  padding: 4px 12px;
  font-size: 12.5px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    color 0.15s ease;
}

.dt-chip:hover {
  border-color: var(--accent);
  color: var(--text);
}

.dt-chip.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  font-weight: 600;
}

.dt-coverage {
  margin-left: auto;
  font-size: 12px;
  color: var(--faint);
}

.dt-card {
  background: linear-gradient(180deg, var(--panel-top), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.dt-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line-soft);
}

.dt-card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.dt-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-strong);
}

.dt-style {
  font-size: 12px;
  color: var(--faint);
}

.dt-badge {
  padding: 1px 8px;
  font-size: 11.5px;
  font-weight: 600;
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

.dt-net {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-family: var(--font-num);
}

.dt-net-label,
.dt-bs {
  font-size: 12px;
  color: var(--faint);
}

.dt-net b {
  font-size: 18px;
}

.dt-capsules {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.dt-capsule {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  font: inherit;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
}

.dt-capsule.cap-buy {
  background: color-mix(in srgb, var(--rise) 9%, transparent);
  border-color: color-mix(in srgb, var(--rise) 40%, transparent);
}

.dt-capsule.cap-sell {
  background: color-mix(in srgb, var(--fall) 9%, transparent);
  border-color: color-mix(in srgb, var(--fall) 40%, transparent);
}

.dt-capsule.selected {
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 35%, transparent);
}

.cap-name {
  font-weight: 700;
  color: var(--text-strong);
}

.cap-pct,
.cap-amt,
.cap-net {
  font-family: var(--font-num);
  font-size: 12.5px;
}

.cap-amt {
  color: var(--muted);
}

.cap-buy .cap-net {
  color: var(--rise);
}

.cap-sell .cap-net {
  color: var(--fall);
}

.dt-sum-card {
  background: linear-gradient(180deg, var(--panel-top), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dt-sum-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.dt-sum-net {
  font-family: var(--font-num);
  font-size: 22px;
}

.dt-sum-sub {
  font-size: 12px;
  color: var(--faint);
}

.dt-all {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.dt-all-toggle {
  align-self: flex-start;
  padding: 5px 12px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
}

/* 边框 / 圆角放在外层 wrap 上，表头才能在 wrap 里吸顶 */
.dt-table-wrap {
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel);
}

.dt-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}

.dt-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  text-align: left;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  padding: 8px 12px;
  background: var(--panel-soft);
  border-bottom: 1px solid var(--line);
}

.dt-table td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-soft);
}

.dt-table tbody tr:last-child td {
  border-bottom: none;
}

.dt-table .num {
  text-align: right;
  font-family: var(--font-num);
}

.dt-table .reason {
  color: var(--muted);
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dt-table tr.clickable {
  cursor: pointer;
}

.dt-table tr.clickable:hover td {
  background: var(--accent-soft);
}

.code {
  font-family: var(--font-num);
  font-size: 12px;
  color: var(--faint);
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

/* —— 用户拖过高度：席位卡头部固定、胶囊区在卡内滚动；全部上榜个股的表格在框内滚动（表头吸顶） —— */
.mk-box.is-h > .dt-card,
.mk-box.is-h > .dt-all {
  overflow: hidden;
}

.mk-box.is-h .dt-card-head {
  flex: none;
}

.mk-box.is-h .dt-capsules {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  align-content: flex-start;
}

.mk-box.is-h .dt-table-wrap {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
