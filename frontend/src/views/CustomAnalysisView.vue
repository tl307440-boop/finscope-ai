<script setup lang="ts">
/**
 * 自定义数据分析：研究者自己盯的宏观 / 市场指标。
 * 当前三组：宽基与科创50 期权 PCR、估值水位（ERP / 巴菲特指标 / 中位数 PB）、数据源与质量。
 * 页面用 ?tab= 记住当前分组，刷新或分享链接都停在同一处。
 */
import { computed } from "vue"
import { useRoute, useRouter } from "vue-router"
import PageHeader from "../components/ui/PageHeader.vue"
import PcrPanel from "./parts/custom/PcrPanel.vue"
import SourcesPanel from "./parts/custom/SourcesPanel.vue"
import ValuationPanel from "./parts/custom/ValuationPanel.vue"

const TABS = [
  { key: "pcr", label: "期权 PCR" },
  { key: "valuation", label: "估值水位" },
  { key: "sources", label: "数据源与质量" },
] as const

type TabKey = (typeof TABS)[number]["key"]

const route = useRoute()
const router = useRouter()

const active = computed<TabKey>(() => {
  const wanted = route.query.tab
  return TABS.find((tab) => tab.key === wanted)?.key ?? "pcr"
})

function select(key: TabKey): void {
  void router.replace({ query: { ...route.query, tab: key } })
}
</script>

<template>
  <main class="page">
    <PageHeader
      title="自定义数据分析"
      subtitle="期权 PCR、股债收益差、巴菲特指标、全市场中位数 PB；数据只来自交易所与指数公司的公开页面，描述性指标，不构成投资建议"
    >
      <template #tabs>
        <div class="tabs" role="tablist" aria-label="分析分组">
          <button
            v-for="tab in TABS"
            :key="tab.key"
            type="button"
            role="tab"
            class="tab"
            :class="{ active: active === tab.key }"
            :aria-selected="active === tab.key"
            @click="select(tab.key)"
          >
            {{ tab.label }}
          </button>
        </div>
      </template>
    </PageHeader>

    <PcrPanel v-if="active === 'pcr'" />
    <ValuationPanel v-else-if="active === 'valuation'" />
    <SourcesPanel v-else />
  </main>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--line);
}

.tab {
  margin-bottom: -1px;
  padding: 8px 18px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  font: inherit;
  cursor: pointer;
}

.tab:hover {
  color: var(--text-strong);
}

.tab.active {
  color: var(--text-strong);
  border-bottom-color: var(--accent);
}
</style>
