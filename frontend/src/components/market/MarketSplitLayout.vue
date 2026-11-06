<script setup lang="ts">
/**
 * 市场行情四个模块共用的外壳：顶部模块切换 + 左侧内容 + 右侧可折叠的个股详情联动面板。
 * 席位字典抽屉也挂在这里，各模块都能从顶部按钮打开。
 *
 * 可拖动：
 * - 右侧面板宽度：中间的分隔条左右拖，默认按屏幕宽度取 22%（夹在 400–520px 之间），双击恢复。
 * - 左侧每个框：右边缘拖宽度、下边缘拖高度（见 ResizableBox.vue）；主栏是 flex-wrap 流式布局。
 * 所有尺寸记在浏览器本地（panelLayout.ts），工具栏里的「重置布局」一键全部恢复默认。
 */
import { onBeforeUnmount, ref } from "vue"

import SeatManagerDrawer from "../../views/parts/market/SeatManagerDrawer.vue"
import MarketModuleTabs from "./MarketModuleTabs.vue"
import { MAX_SIDE, MIN_SIDE, layoutState, persistLayout, resetAllLayout, setSideWidth } from "./panelLayout"
import StockDetailPanel from "./StockDetailPanel.vue"

const detailOpen = ref(true)
const dragging = ref(false)
let dragStartX = 0
let dragStartWidth = 0
const sideEl = ref<HTMLElement | null>(null)

function bodyStyle(): Record<string, string> | undefined {
  return layoutState.side === null ? undefined : { "--msl-side": `${layoutState.side}px` }
}

function onMove(event: PointerEvent): void {
  // 面板在右边：鼠标往左拖 = 面板变宽
  setSideWidth(dragStartWidth + (dragStartX - event.clientX), false)
}

function stopDrag(): void {
  if (!dragging.value) return
  dragging.value = false
  window.removeEventListener("pointermove", onMove)
  window.removeEventListener("pointerup", stopDrag)
  window.removeEventListener("pointercancel", stopDrag)
  persistLayout()
  window.dispatchEvent(new Event("resize"))
}

function startDrag(event: PointerEvent): void {
  event.preventDefault()
  dragging.value = true
  dragStartX = event.clientX
  dragStartWidth = sideEl.value?.getBoundingClientRect().width ?? MIN_SIDE
  window.addEventListener("pointermove", onMove)
  window.addEventListener("pointerup", stopDrag)
  window.addEventListener("pointercancel", stopDrag)
}

function resetWidth(): void {
  setSideWidth(null)
}

function nudge(delta: number): void {
  const current = layoutState.side ?? sideEl.value?.getBoundingClientRect().width ?? MIN_SIDE
  setSideWidth(Math.min(MAX_SIDE, Math.max(MIN_SIDE, current + delta)))
}

function resetLayout(): void {
  resetAllLayout()
  window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")))
}

onBeforeUnmount(() => {
  window.removeEventListener("pointermove", onMove)
  window.removeEventListener("pointerup", stopDrag)
  window.removeEventListener("pointercancel", stopDrag)
})
</script>

<template>
  <div class="page market-split-page">
    <MarketModuleTabs />

    <div class="msl-toolbar">
      <span class="msl-tip">各框的右边缘 / 下边缘 / 右下角可以拖动；双击边缘恢复默认</span>
      <button
        type="button"
        class="msl-toggle msl-reset"
        title="把四个行情页里所有框的大小、右侧个股详情的宽度全部恢复默认"
        @click="resetLayout"
      >
        重置布局
      </button>
      <button type="button" class="msl-toggle" @click="detailOpen = !detailOpen">
        {{ detailOpen ? "收起个股详情 ▶" : "◀ 展开个股详情" }}
      </button>
    </div>

    <div
      class="msl-body"
      :class="{ 'is-collapsed': !detailOpen, 'is-dragging': dragging }"
      :style="bodyStyle()"
    >
      <div class="msl-main">
        <slot />
      </div>
      <div
        v-if="detailOpen"
        class="msl-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整个股详情宽度，双击恢复默认"
        tabindex="0"
        title="拖动调整宽度，双击恢复默认"
        @pointerdown="startDrag"
        @dblclick="resetWidth"
        @keydown.left.prevent="nudge(24)"
        @keydown.right.prevent="nudge(-24)"
      >
        <span class="msl-grip" aria-hidden="true" />
      </div>
      <div v-if="detailOpen" ref="sideEl" class="msl-side">
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
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.msl-tip {
  margin-right: auto;
  font-size: 11.5px;
  color: var(--faint);
}

.msl-toggle {
  padding: 3px 10px;
  font-size: 11.5px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  cursor: pointer;
}

.msl-toggle:hover {
  color: var(--accent);
  border-color: var(--accent);
}

.msl-body {
  /* 右侧面板默认宽度：随屏幕宽度在 400–520px 之间变，拖动后固定成具体像素 */
  --msl-side: clamp(400px, 22vw, 520px);
  display: grid;
  grid-template-columns: minmax(0, 1fr) 12px var(--msl-side);
  gap: 0;
  align-items: start;
}

.msl-body.is-collapsed {
  grid-template-columns: minmax(0, 1fr);
}

.msl-body.is-dragging {
  cursor: col-resize;
  user-select: none;
}

/*
 * 主栏是 flex-wrap 流式布局：每个可拖的框（.mk-flow）宽度 = 占一行的百分比，
 * 默认 100%（整行）；框窄了，后面的框自动流到旁边。其余直接子元素（页头、状态块等）一律占满整行。
 */
.msl-main {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  align-content: flex-start;
  gap: var(--gap, 14px);
}

.msl-main > :deep(:not(.mk-flow)) {
  flex: 0 0 100%;
  min-width: 0;
  max-width: 100%;
}

.msl-side {
  min-width: 0;
}

/* 分隔条：平时只是一条淡线，悬停 / 拖动时亮起来；盖在各框右边缘的拖动热区之上 */
.msl-resizer {
  position: sticky;
  z-index: 1;
  top: 12px;
  align-self: start;
  height: calc(100vh - 28px);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: col-resize;
  outline: none;
}

.msl-grip {
  width: 4px;
  height: 56px;
  border-radius: 2px;
  background: var(--line);
  transition:
    background 0.15s ease,
    height 0.15s ease;
}

.msl-resizer:hover .msl-grip,
.msl-resizer:focus-visible .msl-grip,
.is-dragging .msl-grip {
  height: 96px;
  background: var(--accent);
}

@media (max-width: 1280px) {
  .msl-body {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .msl-resizer {
    display: none;
  }

  .msl-tip {
    display: none;
  }
}
</style>
