<script setup lang="ts">
/**
 * 市场行情「每个框的边缘都能拖」的外壳。
 *
 * - 右边缘：拖宽度。主栏是 flex-wrap 流式布局，框的宽度记成「占一行的百分比」；
 *   右边紧挨着另一个框时，这条边就是两个框共用的分隔条（一增一减，总和不变）；
 *   右边没有框时只动自己，松手后下一个还是默认整行宽的框会自动流上来填满剩下的空位。
 * - 下边缘：拖高度。框里内容放不下就在框内滚动，图表会跟着变高。
 * - 右下角：宽高一起拖。
 * - 双击任意一条边：恢复这个框（和共用分隔条的邻居）的默认；聚焦后用方向键微调。
 * 尺寸统一记在 panelLayout.ts（localStorage `finscope.market.layout.v1`）。
 *
 * 样式没加 scoped：框内各面板要按 `.mk-box.is-h`（用户拖过高度）切换成「内部滚动 / 图表撑满」。
 */
import { computed, onBeforeUnmount, ref } from "vue"

import {
  FALLBACK_GAP,
  clampHeight,
  dragHeightGain,
  computeWidthDrag,
  layoutState,
  minPctFor,
  persistLayout,
  resetPanel,
  roundPct,
  setPanelSize,
  widthToBasis,
} from "./panelLayout"

const props = withDefaults(
  defineProps<{
    /** 稳定 id（记忆尺寸用）；不给就不出手柄 */
    id?: string
    /** 允许拖哪几个方向：wh 宽高 / h 只能拖高度 / w 只能拖宽度 */
    axes?: "wh" | "h" | "w"
    /** 默认宽度，占主栏一行的百分比 */
    defaultW?: number
    /** 最小高度（像素） */
    minH?: number
    /** 参与主栏的流式换行布局；默认只要允许拖宽度就参与 */
    flow?: boolean
  }>(),
  { id: "", axes: "wh", defaultW: 100, minH: 120, flow: undefined },
)

type Edge = "e" | "s" | "c"

const root = ref<HTMLElement | null>(null)
const active = ref<Edge | null>(null)

const canW = computed(() => props.axes.includes("w"))
const canH = computed(() => props.axes.includes("h"))
const isFlow = computed(() => props.flow ?? canW.value)
const size = computed(() => (props.id ? layoutState.panels[props.id] : undefined))
const pct = computed(() => size.value?.w ?? props.defaultW)
const hasCustomH = computed(() => size.value?.h !== undefined)

const boxStyle = computed(() => {
  const style: Record<string, string> = {}
  if (isFlow.value) style.flexBasis = widthToBasis(pct.value)
  if (size.value?.h !== undefined) style.height = `${size.value.h}px`
  return style
})

const labels: Record<Edge, string> = {
  e: "拖动调整宽度（右边紧挨着的框一起变），双击恢复默认",
  s: "拖动调整高度，双击恢复默认",
  c: canW.value && canH.value ? "拖动同时调整宽和高，双击恢复默认" : "拖动调整高度，双击恢复默认",
}

// ———————————————————— 测量 ————————————————————

interface Neighbor {
  el: HTMLElement
  id: string
  startPct: number
}

interface Measure {
  containerWidth: number
  gap: number
  freePx: number
  startPct: number
  startH: number
  neighbor: Neighbor | null
}

function asFlowBox(node: Element | null): HTMLElement | null {
  return node instanceof HTMLElement && node.classList.contains("mk-flow") && node.dataset.mkId ? node : null
}

function measure(): Measure | null {
  const el = root.value
  const container = el?.parentElement
  if (!el || !container) return null
  const style = window.getComputedStyle(container)
  const gap = Number.parseFloat(style.columnGap)
  const padL = Number.parseFloat(style.paddingLeft) || 0
  const padR = Number.parseFloat(style.paddingRight) || 0
  const containerWidth = container.clientWidth - padL - padR
  const rect = el.getBoundingClientRect()
  const crect = container.getBoundingClientRect()

  let neighbor: Neighbor | null = null
  const next = isFlow.value ? asFlowBox(el.nextElementSibling) : null
  // 同一行才算邻居：下一个框 offsetTop 跟自己一样
  if (next && Math.abs(next.offsetTop - el.offsetTop) < 2) {
    const id = next.dataset.mkId as string
    const dw = Number(next.dataset.mkDw) || 100
    neighbor = { el: next, id, startPct: layoutState.panels[id]?.w ?? dw }
  }
  return {
    containerWidth,
    gap: Number.isFinite(gap) ? gap : FALLBACK_GAP,
    freePx: Math.max(0, crect.right - padR - rect.right),
    startPct: pct.value,
    startH: rect.height,
    neighbor,
  }
}

/** 按横向位移 deltaPx 改宽度；有邻居时邻居一起变。 */
function applyWidth(m: Measure, deltaPx: number): number {
  const result = computeWidthDrag({
    startPct: m.startPct,
    neighborStartPct: m.neighbor ? m.neighbor.startPct : null,
    deltaPx,
    containerWidth: m.containerWidth,
    gap: m.gap,
    freePx: m.freePx,
  })
  setPanelSize(props.id, { w: result.self }, false)
  if (m.neighbor && result.neighbor !== null) setPanelSize(m.neighbor.id, { w: result.neighbor }, false)
  return result.self
}

/**
 * 右边没有框、把自己拖窄之后：下一个还是默认整行宽的框自动流上来，宽度 = 剩余空位。
 * 用户自己调过的框（有记录）不动。
 */
function adoptNext(m: Measure, selfPct: number): void {
  if (m.neighbor || !root.value || selfPct >= 99.95) return
  const next = asFlowBox(root.value.nextElementSibling)
  if (!next) return
  const id = next.dataset.mkId as string
  if (layoutState.panels[id]?.w !== undefined) return
  if ((Number(next.dataset.mkDw) || 100) < 99.95) return
  const rest = roundPct(100 - selfPct)
  if (rest < minPctFor(m.containerWidth, m.gap)) return
  setPanelSize(id, { w: rest }, false)
}

function notifyResize(): void {
  // vue-echarts 的 autoresize 靠 ResizeObserver；再补一个 window resize，保证图表一定重画
  window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")))
}

// ———————————————————— 拖动 ————————————————————

interface DragState {
  edge: Edge
  pointerId: number
  startX: number
  startY: number
  m: Measure
  lastPct: number
  /** 高框（比如热点消息列表）的拖动增益：鼠标动 1px，高度动 gain px，一把就能拖到合适高度 */
  gain: number
  moved: boolean
}

let drag: DragState | null = null
function setBodyDragging(edge: Edge | null): void {
  const cls = document.body.classList
  cls.remove("mk-resizing", "mk-resizing-e", "mk-resizing-s", "mk-resizing-c")
  if (edge) cls.add("mk-resizing", `mk-resizing-${edge}`)
}

function usesW(edge: Edge): boolean {
  return canW.value && (edge === "e" || edge === "c")
}

function usesH(edge: Edge): boolean {
  return canH.value && (edge === "s" || edge === "c")
}

function onDown(event: PointerEvent, edge: Edge): void {
  if (!props.id || event.button !== 0) return
  const m = measure()
  if (!m) return
  event.preventDefault()
  event.stopPropagation()
  ;(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)
  drag = {
    edge,
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    m,
    lastPct: m.startPct,
    gain: dragHeightGain(m.startH, window.innerHeight),
    moved: false,
  }
  active.value = edge
  setBodyDragging(edge)
}

function onMove(event: PointerEvent): void {
  const d = drag
  if (!d || event.pointerId !== d.pointerId) return
  if (event.clientX === d.startX && event.clientY === d.startY && !d.moved) return
  d.moved = true
  if (usesW(d.edge)) d.lastPct = applyWidth(d.m, event.clientX - d.startX)
  if (usesH(d.edge)) {
    setPanelSize(props.id, { h: clampHeight(d.m.startH + (event.clientY - d.startY) * d.gain, props.minH) }, false)
  }
}

function onUp(event: PointerEvent): void {
  const d = drag
  if (!d || event.pointerId !== d.pointerId) return
  drag = null
  active.value = null
  setBodyDragging(null)
  ;(event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId)
  if (!d.moved) return
  if (usesW(d.edge)) adoptNext(d.m, d.lastPct)
  persistLayout()
  notifyResize()
}

function resetEdge(edge: Edge): void {
  const neighbor = usesW(edge) && isFlow.value ? measure()?.neighbor : null
  if (usesW(edge)) resetPanel(props.id, "w", false)
  if (usesH(edge)) resetPanel(props.id, "h", false)
  // 共用分隔条的邻居一起恢复，否则邻居会孤零零留在奇怪的宽度上
  if (neighbor) resetPanel(neighbor.id, "w", false)
  persistLayout()
  notifyResize()
}

const KEY_STEP_PX = 24

function onKey(event: KeyboardEvent, edge: Edge): void {
  if (!props.id) return
  const big = event.shiftKey ? 5 : 1
  let dx = 0
  let dy = 0
  if (event.key === "ArrowRight") dx = KEY_STEP_PX * 2 * big
  else if (event.key === "ArrowLeft") dx = -KEY_STEP_PX * 2 * big
  else if (event.key === "ArrowDown") dy = KEY_STEP_PX * big
  else if (event.key === "ArrowUp") dy = -KEY_STEP_PX * big
  else if (event.key === "Enter" || event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault()
    resetEdge(edge)
    return
  } else return
  event.preventDefault()
  const m = measure()
  if (!m) return
  if (dx !== 0 && usesW(edge)) adoptNext(m, applyWidth(m, dx))
  if (dy !== 0 && usesH(edge)) {
    setPanelSize(props.id, { h: clampHeight(m.startH + dy, props.minH) }, false)
  }
  persistLayout()
  notifyResize()
}

onBeforeUnmount(() => {
  if (drag) setBodyDragging(null)
  drag = null
})

function handleProps(edge: Edge) {
  return {
    onPointerdown: (e: PointerEvent) => onDown(e, edge),
    onPointermove: onMove,
    onPointerup: onUp,
    onPointercancel: onUp,
    onDblclick: () => resetEdge(edge),
    onKeydown: (e: KeyboardEvent) => onKey(e, edge),
    onClick: (e: MouseEvent) => e.stopPropagation(),
  }
}
</script>

<template>
  <div
    ref="root"
    class="mk-box"
    :class="{
      'mk-flow': isFlow,
      'is-h': hasCustomH,
      'is-resizing': active !== null,
    }"
    :style="boxStyle"
    :data-mk-id="id || undefined"
    :data-mk-dw="isFlow ? defaultW : undefined"
  >
    <slot />
    <template v-if="id">
      <div
        v-if="canW"
        class="mk-rz mk-rz-e"
        :class="{ active: active === 'e' }"
        role="separator"
        aria-orientation="vertical"
        tabindex="0"
        :aria-label="labels.e"
        :title="labels.e"
        v-bind="handleProps('e')"
      />
      <div
        v-if="canH"
        class="mk-rz mk-rz-s"
        :class="{ active: active === 's' }"
        role="separator"
        aria-orientation="horizontal"
        tabindex="0"
        :aria-label="labels.s"
        :title="labels.s"
        v-bind="handleProps('s')"
      />
      <div
        v-if="canH"
        class="mk-rz mk-rz-c"
        :class="{ active: active === 'c', 'is-v-only': !canW }"
        role="separator"
        tabindex="0"
        :aria-label="labels.c"
        :title="labels.c"
        v-bind="handleProps('c')"
      >
        <svg class="mk-grip" viewBox="0 0 8 8" aria-hidden="true">
          <path d="M7 1 1 7M7 4.5 4.5 7" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" fill="none" />
        </svg>
      </div>
    </template>
  </div>
</template>

<style>
.mk-box {
  position: relative;
  box-sizing: border-box;
  min-width: 0;
  /* 框自己当容器：里面的网格 / 表格按框的实际宽度（而不是屏幕宽度）决定怎么排 */
  container: mk / inline-size;
  display: flex;
  flex-direction: column;
}

/* 框里的内容撑满整个框（拖高度之后，内容区在框内滚动；没拖过时按内容自然撑高） */
.mk-box > :not(.mk-rz) {
  flex: 1 1 auto;
  min-height: 0;
}

.mk-box.is-h > :not(.mk-rz) {
  overflow: auto;
}

/* 用户拖定了高度：在 flex 纵向的父容器里（右侧详情、右栏三张卡）也不许被挤回去 */
.mk-box.is-h {
  flex-shrink: 0;
}

/* 主栏流式布局里的框：flex-basis 由内联样式给（百分比），最小 320px */
.mk-box.mk-flow {
  flex: 0 0 100%;
  min-width: min(320px, 100%);
  max-width: 100%;
  /* 行尾的框，右边缘手柄有一小截伸到主栏外，要压在右侧面板的分隔条（z-index: 1）之上 */
  z-index: 2;
}

/* 默认两个框并排时底部对齐拉齐高度；个别面板（高度本来就不一样）加 mk-start 顶部对齐、各自撑高 */
.mk-box.mk-start {
  align-self: flex-start;
}

/* —— 手柄：边缘热区约 8px，一半在框外的间距里，不盖内容里的按钮 / 滚动条 —— */
.mk-rz {
  position: absolute;
  z-index: 4;
  touch-action: none;
  outline: none;
}

.mk-rz-e {
  top: 14px;
  bottom: 14px;
  right: -5px;
  width: 8px;
  cursor: col-resize;
}

.mk-rz-s {
  left: 14px;
  right: 14px;
  bottom: -5px;
  height: 8px;
  cursor: row-resize;
}

.mk-rz-c {
  z-index: 5;
  right: -5px;
  bottom: -5px;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  color: var(--faint);
}

.mk-rz-c.is-v-only {
  cursor: row-resize;
}

.mk-rz-e::after,
.mk-rz-s::after {
  content: "";
  position: absolute;
  border-radius: 2px;
  background: var(--accent);
  opacity: 0;
  transition: opacity 0.12s ease;
}

.mk-rz-e::after {
  top: 0;
  bottom: 0;
  left: 3px;
  width: 2px;
}

.mk-rz-s::after {
  left: 0;
  right: 0;
  top: 3px;
  height: 2px;
}

/* 鼠标移到框上，边缘先淡淡露出来提示「这里能拉」；移到手柄上再亮起 */
.mk-box:hover > .mk-rz-e::after,
.mk-box:hover > .mk-rz-s::after {
  opacity: 0.22;
}

.mk-rz-e:hover::after,
.mk-rz-s:hover::after,
.mk-rz-e:focus-visible::after,
.mk-rz-s:focus-visible::after,
.mk-rz-e.active::after,
.mk-rz-s.active::after {
  opacity: 0.9;
}

.mk-grip {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 8px;
  height: 8px;
  opacity: 0.4;
  transition:
    opacity 0.12s ease,
    color 0.12s ease;
}

.mk-box:hover > .mk-rz-c .mk-grip {
  opacity: 0.75;
}

.mk-rz-c:hover,
.mk-rz-c:focus-visible,
.mk-rz-c.active {
  color: var(--accent);
}

.mk-rz-c:hover .mk-grip,
.mk-rz-c:focus-visible .mk-grip,
.mk-rz-c.active .mk-grip {
  opacity: 1;
}

.mk-box.is-resizing {
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 55%, transparent);
}

/* 拖动期间：全页统一光标、不选中文字 */
body.mk-resizing,
body.mk-resizing * {
  user-select: none !important;
}

body.mk-resizing-e,
body.mk-resizing-e * {
  cursor: col-resize !important;
}

body.mk-resizing-s,
body.mk-resizing-s * {
  cursor: row-resize !important;
}

body.mk-resizing-c,
body.mk-resizing-c * {
  cursor: nwse-resize !important;
}

/* 窄屏（右侧个股详情已掉到下面）：一律单列，不能拖宽度，只留拖高度 */
@media (max-width: 1280px) {
  .mk-box.mk-flow {
    flex-basis: 100% !important;
  }

  .mk-rz-e {
    display: none;
  }

  .mk-rz-c:not(.is-v-only) {
    display: none;
  }
}
</style>
