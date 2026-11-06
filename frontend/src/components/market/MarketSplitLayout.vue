<script setup lang="ts">
/**
 * 市场行情四个模块共用的外壳：顶部模块切换 + 左侧内容 + 右侧可折叠的个股详情联动面板。
 * 席位字典抽屉也挂在这里，各模块都能从顶部按钮打开。
 */
import { ref } from "vue"

import SeatManagerDrawer from "../../views/parts/market/SeatManagerDrawer.vue"
import MarketModuleTabs from "./MarketModuleTabs.vue"
import StockDetailPanel from "./StockDetailPanel.vue"

const detailOpen = ref(true)
</script>

<template>
  <div class="page market-split-page">
    <MarketModuleTabs />

    <div class="msl-toolbar">
      <button type="button" class="msl-toggle" @click="detailOpen = !detailOpen">
        {{ detailOpen ? "收起个股详情 ▶" : "◀ 展开个股详情" }}
      </button>
    </div>

    <div class="msl-body" :class="{ 'is-collapsed': !detailOpen }">
      <div class="msl-main">
        <slot />
      </div>
      <div v-if="detailOpen" class="msl-side">
        <StockDetailPanel />
      </div>
    </div>

    <SeatManagerDrawer />
  </div>
</template>

<style scoped>
.market-split-page {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.msl-toolbar {
  display: flex;
  justify-content: flex-end;
}

.msl-toggle {
  padding: 3px 10px;
  font-size: 11.5px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  cursor: pointer;
}

.msl-toggle:hover {
  color: var(--accent);
}

.msl-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 12px;
  align-items: start;
}

.msl-body.is-collapsed {
  grid-template-columns: minmax(0, 1fr);
}

.msl-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap, 12px);
}

.msl-side {
  min-width: 0;
}

@media (max-width: 1280px) {
  .msl-body {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
