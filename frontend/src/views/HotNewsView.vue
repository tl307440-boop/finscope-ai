<script setup lang="ts">
/**
 * 热点消息：财联社 / 东方财富 / 同花顺 / 金十 电报聚合。
 * 影响级别（S/A/B）与利好/利空/催化是按来源给的重要度 + 关键词规则做的判断，仅供快速筛选，
 * 不是投资建议；规则见后端 `ashare_calc.classify_news`。
 */
import { computed, ref } from "vue"

import {
  fetchNews,
  type NewsItem,
  type NewsLevel,
  type NewsSentiment,
  type NewsSource,
} from "../api/aSharePanorama"
import { fmtPct, toneClass } from "../api/aShareFormat"
import { usePanoramaResource } from "../api/usePanoramaResource"
import MarketSplitLayout from "../components/market/MarketSplitLayout.vue"
import ResizableBox from "../components/market/ResizableBox.vue"
import PanoramaStatusBar from "../components/market/PanoramaStatusBar.vue"
import PageHeader from "../components/ui/PageHeader.vue"
import StateBlock from "../components/ui/StateBlock.vue"
import { useMarketLinkageStore } from "../stores/marketLinkage"
import HotspotsSection from "./parts/market/HotspotsSection.vue"

const linkageStore = useMarketLinkageStore()
const { data, state, error, reload } = usePanoramaResource(fetchNews, { pollMs: 30_000 })

const SOURCES: NewsSource[] = ["财联社", "东方财富", "同花顺", "金十数据"]
const LEVELS: ("全部" | NewsLevel)[] = ["全部", "S", "A", "B"]
const SENTIMENTS: ("全部" | NewsSentiment)[] = ["全部", "利好", "利空", "催化", "中性"]

const denoise = ref(true)
const level = ref<(typeof LEVELS)[number]>("全部")
const sentiment = ref<(typeof SENTIMENTS)[number]>("全部")
const region = ref<"all" | "domestic" | "overseas">("all")
const enabledSources = ref<Set<NewsSource>>(new Set(SOURCES))
const keyword = ref("")
const showHistory = ref(false)

function toggleSource(source: NewsSource): void {
  const next = new Set(enabledSources.value)
  if (next.has(source)) next.delete(source)
  else next.add(source)
  enabledSources.value = next
}

const filtered = computed<NewsItem[]>(() => {
  const kw = keyword.value.trim().toLowerCase()
  return (data.value?.items ?? []).filter((n) => {
    if (denoise.value && !n.isHighSnr) return false
    if (!enabledSources.value.has(n.source)) return false
    if (level.value !== "全部" && n.impactLevel !== level.value) return false
    if (sentiment.value !== "全部" && n.sentiment !== sentiment.value) return false
    if (region.value !== "all" && n.region !== region.value) return false
    if (kw) {
      const hay = `${n.title}${n.summary}${n.sectors.join("")}${n.stocks.map((s) => s.name).join("")}`
      if (!hay.toLowerCase().includes(kw)) return false
    }
    return true
  })
})

const badSources = computed(() =>
  Object.entries(data.value?.sourceStatus ?? {}).filter(([, status]) => status !== "ok"),
)

function sentimentClass(s: NewsSentiment): string {
  if (s === "利好") return "st-good"
  if (s === "利空") return "st-bad"
  if (s === "催化") return "st-catalyst"
  return "st-neutral"
}
</script>

<template>
  <MarketSplitLayout>
    <PageHeader title="热点消息" subtitle="财联社 / 东方财富 / 同花顺 / 金十 多源电报 · 板块与个股挂载">
      <template #actions>
        <el-switch v-model="denoise" active-text="降噪（仅高信噪比）" />
        <el-button :loading="state === 'loading'" @click="reload">刷新</el-button>
      </template>
    </PageHeader>

    <ResizableBox id="news.filters" :min-h="80">
      <div class="hn-filters">
        <div class="hn-row">
          <span class="hn-label">来源</span>
          <button
            v-for="s in SOURCES"
            :key="s"
            type="button"
            class="hn-chip"
            :class="{ active: enabledSources.has(s) }"
            @click="toggleSource(s)"
          >
            {{ s }}
          </button>
          <span class="hn-label sep">级别</span>
          <button
            v-for="l in LEVELS"
            :key="l"
            type="button"
            class="hn-chip"
            :class="{ active: level === l }"
            @click="level = l"
          >
            {{ l }}
          </button>
          <span class="hn-label sep">倾向</span>
          <button
            v-for="s in SENTIMENTS"
            :key="s"
            type="button"
            class="hn-chip"
            :class="{ active: sentiment === s }"
            @click="sentiment = s"
          >
            {{ s }}
          </button>
        </div>
        <div class="hn-row">
          <span class="hn-label">范围</span>
          <button type="button" class="hn-chip" :class="{ active: region === 'all' }" @click="region = 'all'">全部</button>
          <button type="button" class="hn-chip" :class="{ active: region === 'domestic' }" @click="region = 'domestic'">国内</button>
          <button type="button" class="hn-chip" :class="{ active: region === 'overseas' }" @click="region = 'overseas'">海外</button>
          <input v-model="keyword" type="text" class="hn-search" placeholder="搜索标题 / 板块 / 个股" />
          <span class="hn-count">{{ filtered.length }} 条</span>
        </div>
      </div>
    </ResizableBox>

    <StateBlock
      v-if="!data"
      :state="state === 'error' ? 'error' : 'loading'"
      :message="error"
      title="热点消息加载失败"
      :rows="6"
      @retry="reload"
    />

    <template v-else>
      <div v-if="badSources.length" class="hn-warn" role="status">
        部分来源暂时不可用：
        <span v-for="[name, status] in badSources" :key="name">{{ name }}（{{ status }}）</span>
      </div>

      <StateBlock
        v-if="filtered.length === 0"
        state="empty"
        :message="
          data.items.length === 0
            ? '所有来源当前都没有返回电报'
            : '当前筛选条件下没有消息，可以关闭降噪或放宽筛选'
        "
      />

      <ResizableBox v-else id="news.list">
        <div class="hn-list">
          <article v-for="n in filtered" :key="n.id" class="hn-item">
            <div class="hn-meta">
              <span class="hn-time">{{ n.date.slice(5) }} {{ n.time }}</span>
              <span class="hn-source">{{ n.source }}</span>
              <span class="hn-level" :class="`lv-${n.impactLevel}`">{{ n.impactLevel }}</span>
              <span class="hn-sentiment" :class="sentimentClass(n.sentiment)">{{ n.sentiment }}</span>
              <span v-if="n.region === 'overseas'" class="hn-region">海外</span>
            </div>
            <h3 class="hn-title">{{ n.title }}</h3>
            <p v-if="n.summary && n.summary !== n.title" class="hn-summary">{{ n.summary }}</p>
            <div v-if="n.sectors.length || n.stocks.length" class="hn-links">
              <span v-for="sec in n.sectors" :key="sec" class="hn-sector">{{ sec }}</span>
              <button
                v-for="s in n.stocks"
                :key="s.code"
                type="button"
                class="hn-stock"
                @click="linkageStore.selectStock(s.code)"
              >
                {{ s.name }}
                <span :class="toneClass(s.changePct)">{{ fmtPct(s.changePct) }}</span>
              </button>
            </div>
          </article>
        </div>
      </ResizableBox>

      <div class="hn-foot">
        <PanoramaStatusBar :meta="data.meta" :refresh-error="error" />
      </div>
    </template>

    <section class="hn-history">
      <button type="button" class="hn-history-toggle" @click="showHistory = !showHistory">
        {{ showHistory ? "收起" : "展开" }}行业事件热点追踪（已入库的公司事件，近 14 天）
      </button>
      <HotspotsSection v-if="showHistory" />
    </section>
  </MarketSplitLayout>
</template>

<style scoped>
.hn-filters {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: linear-gradient(180deg, var(--panel-top), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 12px 16px;
}

.hn-row + .hn-row {
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}

.hn-foot {
  padding: 8px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-soft);
}

.hn-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.hn-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--muted);
}

.hn-label.sep {
  margin-left: 12px;
}

.hn-chip {
  padding: 3px 12px;
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

.hn-chip:hover {
  border-color: var(--accent);
  color: var(--text);
}

.hn-chip.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 45%, transparent);
  font-weight: 600;
}

.hn-search {
  margin-left: 12px;
  width: 240px;
  padding: 5px 10px;
  font-size: 13px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--text);
}

.hn-count {
  margin-left: auto;
  font-size: 12.5px;
  color: var(--faint);
}

.hn-warn {
  font-size: 12.5px;
  color: var(--warn);
  background: color-mix(in srgb, var(--warn) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
  border-radius: var(--radius-sm);
  padding: 7px 12px;
}

.hn-warn span {
  margin-left: 8px;
}

.hn-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hn-item {
  background: linear-gradient(180deg, var(--panel-top), var(--panel));
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.hn-item:hover {
  border-color: color-mix(in srgb, var(--accent) 35%, var(--line));
}

.hn-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--line-soft);
  font-size: 12px;
}

.hn-time {
  font-family: var(--font-num);
  color: var(--faint);
}

.hn-source {
  color: var(--muted);
}

.hn-level,
.hn-sentiment,
.hn-region {
  padding: 0 8px;
  border-radius: 999px;
  font-weight: 700;
  font-size: 11.5px;
}

.lv-S {
  background: rgba(246, 70, 93, 0.14);
  color: var(--rise);
}

.lv-A {
  background: rgba(245, 158, 11, 0.16);
  color: #d97706;
}

.lv-B {
  background: var(--panel-soft);
  color: var(--faint);
}

.st-good {
  background: rgba(246, 70, 93, 0.1);
  color: var(--rise);
}

.st-bad {
  background: rgba(31, 195, 125, 0.12);
  color: var(--fall);
}

.st-catalyst {
  background: rgba(168, 85, 247, 0.12);
  color: #9333ea;
}

.st-neutral,
.hn-region {
  background: var(--panel-soft);
  color: var(--faint);
}

.hn-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.5;
  color: var(--text-strong);
}

.hn-summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--muted);
}

.hn-links {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.hn-sector {
  padding: 2px 9px;
  font-size: 12px;
  border-radius: 6px;
  background: var(--panel-soft);
  border: 1px solid var(--line);
  color: var(--muted);
}

.hn-stock {
  padding: 2px 9px;
  font: inherit;
  font-size: 12px;
  border-radius: 6px;
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
  color: var(--accent);
  cursor: pointer;
}

.hn-history {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.hn-history-toggle {
  align-self: flex-start;
  padding: 5px 12px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
}

.is-rise {
  color: var(--rise);
}

.is-fall {
  color: var(--fall);
}

/* 用户拖过高度：消息列表就是一个带滚动条的视窗，筛选区内容放不下也在框内滚动 */
.mk-box.is-h > .hn-list {
  overflow: auto;
  padding: 2px 4px 2px 2px;
}
</style>
