<script setup lang="ts">
/**
 * 市场行情各面板的统一外壳：沿用站内 `PanelCard`（色块图标 + 标题 + ⓘ + 右上角操作），
 * 副标题放在头部下面，底部是统一的数据状态条（交易日 / 更新时间 / 来源 / 缺项）。
 *
 * 给了 `boxId` 就是一个「边缘可拖」的框（ResizableBox）：拖右边缘改宽度、拖下边缘改高度。
 * 用户拖过高度后，头部和底部状态条固定，中间的内容区在框内滚动；需要撑满的图表加 `mk-fill` 类。
 */
import type { Component } from "vue"

import type { PanoramaMeta } from "../../api/aSharePanorama"
import PanelCard from "../ui/PanelCard.vue"
import type { PanelTone } from "../ui/tone"
import PanoramaStatusBar from "./PanoramaStatusBar.vue"
import ResizableBox from "./ResizableBox.vue"

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
    /** 可拖尺寸的稳定 id，例如 `indices.global`；不给就是普通面板 */
    boxId?: string
    /** 默认宽度，占主栏一行的百分比（两个并排的面板各给 50） */
    defaultW?: number
    /** 允许拖哪几个方向 */
    axes?: "wh" | "h" | "w"
    /** 最小高度（像素） */
    minH?: number
  }>(),
  {
    subtitle: "",
    info: "",
    icon: undefined,
    tone: "",
    meta: null,
    refreshError: "",
    flush: false,
    boxId: "",
    defaultW: 100,
    axes: "wh",
    minH: 140,
  },
)
</script>

<template>
  <ResizableBox :id="boxId" :axes="axes" :default-w="defaultW" :min-h="minH">
    <PanelCard :title="title" :info="info" :tone="tone" :flush="flush" class="market-panel">
      <template v-if="icon || $slots.icon" #icon>
        <slot name="icon"><el-icon><component :is="icon" /></el-icon></slot>
      </template>
      <template v-if="$slots.actions" #actions><slot name="actions" /></template>
      <div class="mp-body">
        <p v-if="subtitle" class="mp-subtitle">{{ subtitle }}</p>
        <slot />
      </div>
      <footer v-if="meta" class="mp-foot">
        <PanoramaStatusBar :meta="meta" :refresh-error="refreshError" />
      </footer>
    </PanelCard>
  </ResizableBox>
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

/* —— 用户拖过高度：头 / 尾固定，中间内容区内部滚动；子元素不被压扁，mk-fill 的元素（图表）吃掉多出来的高度 —— */
.mk-box.is-h > .market-panel.card {
  display: flex;
  flex-direction: column;
  overflow: visible;
}

.mk-box.is-h > .market-panel > :deep(.head),
.mk-box.is-h > .market-panel > .mp-foot {
  flex: none;
}

.mk-box.is-h > .market-panel > .mp-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
}

.mk-box.is-h > .market-panel > .mp-body > * {
  flex: none;
}

/* 插槽里的元素带的是父组件的 scope id，所以这里要 :deep */
.mk-box.is-h > .market-panel > .mp-body > :deep(.mk-fill) {
  flex: 1 1 auto;
}
</style>
