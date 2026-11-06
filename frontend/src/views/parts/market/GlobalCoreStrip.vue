<template>
  <MarketPanel
    box-id="indices.global"
    title="全球核心指数与大宗商品"
    subtitle="A 股宽基 · 港美核心指数 · 黄金 / 原油；迷你图为当日分时"
    info="价格、涨跌幅与分时均为公开行情；拿不到的品种显示「—」并在下方「缺项」里说明原因"
    tone="cyan"
    :icon="Connection"
    :meta="state === 'ready' ? (data?.meta ?? null) : null"
    :refresh-error="error"
  >
    <template #actions>
      <SectionTabs
        :model-value="activeCategory"
        :tabs="categoryTabs"
        @update:model-value="activeCategory = $event as (typeof categories)[number]"
      />
    </template>

    <StateBlock
      v-if="state !== 'ready'"
      :state="state"
      :message="error"
      title="全球指数加载失败"
      @retry="reload"
    />
    <template v-else>
      <StateBlock
        v-if="filteredQuotes.length === 0"
        state="empty"
        message="当前分类下上游没有返回报价"
      />
      <div v-else class="quotes-grid">
        <div
          v-for="item in filteredQuotes"
          :key="item.code"
          class="quote-card"
          :class="cardTone(item.changePct)"
        >
          <div class="q-top">
            <span class="q-name">{{ item.name }}</span>
            <span class="q-cat-tag">{{ item.category }}</span>
          </div>
          <div class="q-main">
            <span class="q-price" :class="toneClass(item.changePct)">{{ fmtPrice(item.price) }}</span>
            <span class="q-pct" :class="toneClass(item.changePct)">{{ fmtPct(item.changePct) }}</span>
          </div>
          <div class="q-bottom">
            <span class="q-delta">
              <span :class="toneClass(item.change)">{{ fmtSigned(item.change) }}</span>
              <span v-if="item.amountYuan" class="q-amt">成交 {{ (item.amountYuan / 1e8).toFixed(0) }} 亿</span>
            </span>
            <MiniSpark
              v-if="item.spark.length > 1"
              class="q-spark"
              :points="item.spark"
              :color="(item.changePct ?? 0) >= 0 ? '#f6465d' : '#1fc37d'"
              :width="70"
              :height="26"
            />
          </div>
        </div>
      </div>
    </template>
  </MarketPanel>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"
import { Connection } from "@element-plus/icons-vue"

import { fetchGlobalQuotes } from "../../../api/aSharePanorama"
import { fmtPct, fmtSigned, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import MarketPanel from "../../../components/market/MarketPanel.vue"
import MiniSpark from "../../../components/ui/MiniSpark.vue"
import SectionTabs from "../../../components/ui/SectionTabs.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const { data, state, error, reload } = usePanoramaResource(fetchGlobalQuotes, { pollMs: 60_000 })

const categories = ["全部", "A股宽基", "海外指数", "大宗商品"] as const
const categoryTabs = categories.map((c) => ({ key: c, label: c }))
const activeCategory = ref<(typeof categories)[number]>("全部")

const filteredQuotes = computed(() => {
  const quotes = data.value?.quotes ?? []
  if (activeCategory.value === "全部") return quotes
  return quotes.filter((q) => q.category === activeCategory.value)
})

function cardTone(pct: number | null): string {
  if (pct === null || pct === 0) return ""
  return pct > 0 ? "border-rise" : "border-fall"
}

function fmtPrice(value: number | null): string {
  if (value === null) return "—"
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<style scoped>
.quotes-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px;
}

.quote-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--line);
  border-radius: var(--radius-sm);
  background: var(--panel-soft);
  transition:
    border-color 0.15s ease,
    transform 0.15s ease;
}

.quote-card:hover {
  transform: translateY(-1px);
  border-color: color-mix(in srgb, var(--accent) 35%, var(--line));
}

.quote-card.border-rise {
  border-left-color: var(--rise);
}

.quote-card.border-fall {
  border-left-color: var(--fall);
}

.q-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.q-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-strong);
  white-space: nowrap;
}

.q-cat-tag {
  padding: 0 6px;
  border: 1px solid var(--line);
  border-radius: 4px;
  font-size: 11px;
  line-height: 18px;
  color: var(--faint);
  white-space: nowrap;
}

.q-main {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
  font-family: var(--font-num);
  font-variant-numeric: tabular-nums;
}

.q-price {
  font-size: 18px;
  font-weight: 600;
}

.q-pct {
  font-size: 13px;
  font-weight: 600;
}

.q-bottom {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 6px;
  min-height: 26px;
  padding-top: 6px;
  border-top: 1px dashed var(--line-soft);
}

.q-delta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  font-family: var(--font-num);
  font-size: 12px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.q-amt {
  font-size: 11px;
  color: var(--faint);
}

.q-dot {
  display: inline-block;
  width: 3px;
  height: 3px;
  margin: 0 6px 2px;
  border-radius: 50%;
  background: var(--faint);
}

.q-spark {
  flex: none;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

/* 按框自己的宽度换列数（用户拖宽 / 拖窄、右侧个股详情展开收起都会变） */
@container mk (max-width: 880px) {
  .quotes-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container mk (max-width: 640px) {
  .quotes-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@container mk (max-width: 480px) {
  .quotes-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
