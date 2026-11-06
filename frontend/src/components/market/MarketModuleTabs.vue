<template>
  <div class="market-module-tabs">
    <div class="module-nav-group" role="tablist" aria-label="A股核心分析模块切换">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.to"
        :to="tab.to"
        class="module-tab-item"
        :class="{ active: isTabActive(tab.to) }"
      >
        <span class="tab-badge-dot" :style="{ background: tab.dotColor }" />
        <span class="tab-title">{{ tab.label }}</span>
        <span class="tab-subtitle">{{ tab.subLabel }}</span>
      </RouterLink>
    </div>

    <div class="module-quick-actions">
      <button
        type="button"
        class="seat-config-btn"
        @click="linkageStore.openSeatManager()"
      >
        席位字典 ({{ linkageStore.watchedSeatGroups.length }})
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from "vue-router"
import { useMarketLinkageStore } from "../../stores/marketLinkage"

const route = useRoute()
const linkageStore = useMarketLinkageStore()

const tabs = [
  {
    to: "/market/indices",
    label: "市场行情",
    subLabel: "宏观·情绪·量能·两融·期指·板块·温度",
    dotColor: "#f6465d",
  },
  {
    to: "/market/dragon-tiger",
    label: "龙虎榜分析",
    subLabel: "重点游资席位追踪·自定义席位池",
    dotColor: "#f59e0b",
  },
  {
    to: "/market/ladder",
    label: "连板分析",
    subLabel: "晋级率·空间高度·连板梯队·断板复盘",
    dotColor: "#3b82f6",
  },
  {
    to: "/market/news",
    label: "热点消息",
    subLabel: "全网高信噪比电报·板块与异动股挂载",
    dotColor: "#10b981",
  },
]



function isTabActive(target: string): boolean {
  if (target === "/market/indices") {
    return (
      route.path === "/market/indices" ||
      route.path === "/market/sectors" ||
      route.path === "/market/temperature"
    )
  }
  return route.path.startsWith(target)
}
</script>

<style scoped>
.market-module-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-card);
  flex-wrap: wrap;
}

.module-nav-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.module-tab-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-radius: 10px;
  border: 1px solid transparent;
  background: var(--panel-soft);
  color: var(--muted);
  text-decoration: none;
  transition: all 0.16s ease;
}

.module-tab-item:hover {
  color: var(--text);
  border-color: var(--line);
}

.module-tab-item.active {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: rgba(37, 99, 235, 0.32);
  font-weight: 600;
}

.tab-badge-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tab-title {
  font-size: 13px;
  font-weight: 600;
}

.tab-subtitle {
  font-size: 11px;
  color: var(--faint);
}

.module-tab-item.active .tab-subtitle {
  color: var(--accent);
  opacity: 0.85;
}

.module-quick-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.stock-quick-pick {
  display: flex;
  align-items: center;
  gap: 5px;
  flex-wrap: wrap;
}

.quick-label {
  font-size: 11px;
  color: var(--faint);
}

.quick-stock-pill {
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.14s ease;
}

.quick-stock-pill:hover,
.quick-stock-pill.active {
  border-color: rgba(246, 70, 93, 0.45);
  background: rgba(246, 70, 93, 0.1);
  color: var(--rise);
  font-weight: 600;
}

.seat-config-btn {
  padding: 5px 11px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.seat-config-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

@media (max-width: 1180px) {
  .tab-subtitle {
    display: none;
  }
}
</style>
