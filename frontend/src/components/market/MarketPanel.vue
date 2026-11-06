<script setup lang="ts">
/**
 * 市场行情各面板的统一外壳：沿用站内 `PanelCard`（色块图标 + 标题 + ⓘ + 右上角操作），
 * 副标题放在头部下面，底部是统一的数据状态条（交易日 / 更新时间 / 来源 / 缺项）。
 */
import type { Component } from "vue"

import type { PanoramaMeta } from "../../api/aSharePanorama"
import PanelCard from "../ui/PanelCard.vue"
import type { PanelTone } from "../ui/tone"
import PanoramaStatusBar from "./PanoramaStatusBar.vue"

withDefaults(
  defineProps<{
    title: string
    subtitle?: string
    info?: string
    icon?: Component
    tone?: PanelTone | ""
    /** 给了就在底部渲染状态条 */
    meta?: PanoramaMeta | null
    refreshError?: string
    /** 去掉内边距（表格撑满时用） */
    flush?: boolean
  }>(),
  { subtitle: "", info: "", icon: undefined, tone: "", meta: null, refreshError: "", flush: false },
)
</script>

<template>
  <PanelCard :title="title" :info="info" :tone="tone" :flush="flush" class="market-panel">
    <template v-if="icon || $slots.icon" #icon>
      <slot name="icon"><el-icon><component :is="icon" /></el-icon></slot>
    </template>
    <template v-if="$slots.actions" #actions><slot name="actions" /></template>
    <p v-if="subtitle" class="mp-subtitle">{{ subtitle }}</p>
    <slot />
    <footer v-if="meta" class="mp-foot">
      <PanoramaStatusBar :meta="meta" :refresh-error="refreshError" />
    </footer>
  </PanelCard>
</template>

<style scoped>
.mp-subtitle {
  margin: -4px 0 12px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--faint);
}

.mp-foot {
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--line-soft);
}

.market-panel.flush .mp-subtitle {
  margin-bottom: 10px;
}

.market-panel.flush .mp-foot {
  margin-top: 10px;
}
</style>
