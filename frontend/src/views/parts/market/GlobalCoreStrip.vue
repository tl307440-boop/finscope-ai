<template>
  <section class="global-core-strip" aria-label="全球核心指数与大宗商品走势">
    <div class="strip-head">
      <div class="strip-title-wrap">
        <span class="strip-title">全球核心指数与大宗商品</span>
        <span class="strip-sub">A股宽基 · 港美核心指数 · 黄金 / 原油</span>
      </div>
      <div class="strip-filter-pills">
        <button
          v-for="cat in categories"
          :key="cat"
          type="button"
          class="cat-pill"
          :class="{ active: activeCategory === cat }"
          @click="activeCategory = cat"
        >
          {{ cat }}
        </button>
      </div>
    </div>

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
          class="quote-mini-card"
          :class="cardTone(item.changePct)"
        >
          <div class="q-top">
            <div class="q-name-box">
              <span class="q-name">{{ item.name }}</span>
              <span class="q-cat-tag">{{ item.category }}</span>
            </div>
            <span class="q-pct" :class="toneClass(item.changePct)">
              {{ fmtPct(item.changePct) }}
            </span>
          </div>

          <div class="q-bottom">
            <div class="q-price-col">
              <span class="q-price" :class="toneClass(item.changePct)">
                {{ fmtPrice(item.price) }}
              </span>
              <span class="q-delta" :class="toneClass(item.change)">
                {{ fmtSigned(item.change) }}
                <template v-if="item.amountYuan">
                  · 成交 {{ (item.amountYuan / 1e8).toFixed(0) }}亿
                </template>
              </span>
            </div>
            <div v-if="item.spark.length > 1" class="q-spark-wrap">
              <MiniSpark
                :points="item.spark"
                :color="(item.changePct ?? 0) >= 0 ? '#f6465d' : '#1fc37d'"
                :width="76"
                :height="28"
              />
            </div>
          </div>
        </div>
      </div>
      <PanoramaStatusBar :meta="data?.meta ?? null" :refresh-error="error" />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue"

import { fetchGlobalQuotes } from "../../../api/aSharePanorama"
import { fmtPct, fmtSigned, toneClass } from "../../../api/aShareFormat"
import { usePanoramaResource } from "../../../api/usePanoramaResource"
import PanoramaStatusBar from "../../../components/market/PanoramaStatusBar.vue"
import MiniSpark from "../../../components/ui/MiniSpark.vue"
import StateBlock from "../../../components/ui/StateBlock.vue"

const { data, state, error, reload } = usePanoramaResource(fetchGlobalQuotes, { pollMs: 60_000 })

const categories = ["全部", "A股宽基", "海外指数", "大宗商品"] as const
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
.global-core-strip {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 12px 14px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.strip-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.strip-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.strip-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.strip-sub {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.strip-filter-pills {
  display: flex;
  gap: 6px;
}

.cat-pill {
  padding: 3px 9px;
  font-size: 11px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
}

.cat-pill.active {
  background: var(--accent-subtle);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.32);
  font-weight: 600;
}

.quotes-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.quote-mini-card {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 9px 11px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  transition: transform 0.14s ease;
}

.quote-mini-card:hover {
  transform: translateY(-1px);
}

.quote-mini-card.border-rise {
  border-left: 3px solid var(--rise);
}

.quote-mini-card.border-fall {
  border-left: 3px solid var(--fall);
}

.q-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.q-name-box {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.q-name {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--text-primary);
  white-space: nowrap;
}

.q-cat-tag {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--bg-card);
  color: var(--text-tertiary);
  border: 1px solid var(--border);
}

.q-pct {
  font-family: var(--font-mono);
  font-size: 12.5px;
  font-weight: 700;
}

.q-bottom {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 6px;
}

.q-price-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: var(--font-mono);
}

.q-price {
  font-size: 15px;
  font-weight: 700;
}

.q-delta {
  font-size: 10.5px;
  opacity: 0.9;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

@media (max-width: 1280px) {
  .quotes-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .quotes-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
