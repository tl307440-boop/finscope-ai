/**
 * 市场行情四页的「每个框都能拖」布局状态。
 *
 * 所有框共用一份 JSON，记在浏览器本地 `finscope.market.layout.v1`：
 *   { v: 1, side: 480, panels: { "indices.global": { w: 62.5, h: 320 }, ... } }
 * - w：宽度，占主栏一行的百分比（0~100，保留一位小数）；没写 = 用该框自己的默认宽度。
 * - h：高度，像素；没写 = 随内容自动撑高。
 * - side：右侧个股详情的宽度（像素）。
 * localStorage 不可用（隐私模式 / 被禁用）时，状态只放在内存里，本次会话内依然有效。
 *
 * 这里只有状态和纯算术，不碰 DOM，方便单测；拖拽交互在 ResizableBox.vue。
 */
import { reactive } from "vue"

export const LAYOUT_STORAGE_KEY = "finscope.market.layout.v1"

/** 框的最小宽度（像素）：再窄表格和图表就没法看了 */
export const MIN_BOX_PX = 320
/**
 * 框的最小 / 最大高度（像素）。
 * 上限给得很宽：热点消息列表默认就有 1 万多像素高，拖第一下时不能被上限"弹"回去。
 */
export const MIN_BOX_H = 120
export const MAX_BOX_H = 30000
/** 右侧个股详情宽度范围（像素） */
export const MIN_SIDE = 380
export const MAX_SIDE = 860
/** 主栏里框之间的间距（跟 --gap 一致），读不到 CSS 时的兜底 */
export const FALLBACK_GAP = 14

export interface PanelSize {
  /** 宽度百分比 */
  w?: number
  /** 高度像素 */
  h?: number
}

export interface LayoutState {
  panels: Record<string, PanelSize>
  /** 右侧个股详情宽度（像素）；null = 用默认 */
  side: number | null
}

export const layoutState = reactive<LayoutState>({ panels: {}, side: null })

const MAX_ENTRIES = 300

function getStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage
  } catch {
    return null
  }
}

function isNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

/** 把任意读到的东西洗成合法布局；脏数据（手改 / 旧版本）直接丢掉，不影响页面。 */
export function sanitizeLayout(raw: unknown): LayoutState {
  const out: LayoutState = { panels: {}, side: null }
  if (!raw || typeof raw !== "object") return out
  const file = raw as { panels?: unknown; side?: unknown }
  if (isNum(file.side) && file.side >= MIN_SIDE && file.side <= MAX_SIDE) out.side = Math.round(file.side)
  if (file.panels && typeof file.panels === "object") {
    for (const [id, value] of Object.entries(file.panels as Record<string, unknown>).slice(0, MAX_ENTRIES)) {
      if (!id || !value || typeof value !== "object") continue
      const size: PanelSize = {}
      const v = value as { w?: unknown; h?: unknown }
      if (isNum(v.w) && v.w > 0 && v.w <= 100) size.w = roundPct(v.w)
      if (isNum(v.h) && v.h >= MIN_BOX_H && v.h <= MAX_BOX_H) size.h = Math.round(v.h)
      if (size.w !== undefined || size.h !== undefined) out.panels[id] = size
    }
  }
  return out
}

/** 从浏览器本地读一遍（模块加载时自动调一次；单测里可以手动再调）。 */
export function loadLayout(): void {
  let parsed: unknown = null
  try {
    const text = getStorage()?.getItem(LAYOUT_STORAGE_KEY)
    parsed = text ? JSON.parse(text) : null
  } catch {
    parsed = null
  }
  const next = sanitizeLayout(parsed)
  for (const key of Object.keys(layoutState.panels)) delete layoutState.panels[key]
  Object.assign(layoutState.panels, next.panels)
  layoutState.side = next.side
}

/** 写回浏览器本地；一个自定义都没有时直接删掉这个 key。 */
export function persistLayout(): void {
  const storage = getStorage()
  if (!storage) return
  try {
    const hasPanels = Object.keys(layoutState.panels).length > 0
    if (!hasPanels && layoutState.side === null) {
      storage.removeItem(LAYOUT_STORAGE_KEY)
      return
    }
    const file: Record<string, unknown> = { v: 1, panels: layoutState.panels }
    if (layoutState.side !== null) file.side = layoutState.side
    storage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(file))
  } catch {
    // 配额满 / 隐私模式写不进去：忽略，内存里的状态本次会话照样生效
  }
}

export function getPanelSize(id: string): PanelSize | undefined {
  return layoutState.panels[id]
}

export interface PanelPatch {
  /** 传 null 表示清掉这一项，回到默认 */
  w?: number | null
  h?: number | null
}

/** 改一个框的尺寸；拖动过程中 persist=false（只动内存），松手时再统一写盘。 */
export function setPanelSize(id: string, patch: PanelPatch, persist = true): void {
  if (!id) return
  const current: PanelSize = { ...(layoutState.panels[id] ?? {}) }
  if (patch.w === null) delete current.w
  else if (patch.w !== undefined) current.w = roundPct(patch.w)
  if (patch.h === null) delete current.h
  else if (patch.h !== undefined) current.h = Math.round(clampHeight(patch.h))
  if (current.w === undefined && current.h === undefined) delete layoutState.panels[id]
  else layoutState.panels[id] = current
  if (persist) persistLayout()
}

/** 恢复单个框的默认：宽 / 高 / 两者。 */
export function resetPanel(id: string, axes: "w" | "h" | "wh" = "wh", persist = true): void {
  const patch: PanelPatch = {}
  if (axes.includes("w")) patch.w = null
  if (axes.includes("h")) patch.h = null
  setPanelSize(id, patch, persist)
}

export function setSideWidth(px: number | null, persist = true): void {
  layoutState.side = px === null ? null : Math.min(MAX_SIDE, Math.max(MIN_SIDE, Math.round(px)))
  if (persist) persistLayout()
}

/** 「重置布局」：四页所有自定义尺寸 + 右侧面板宽度，全部回到默认。 */
export function resetAllLayout(): void {
  for (const key of Object.keys(layoutState.panels)) delete layoutState.panels[key]
  layoutState.side = null
  persistLayout()
}

/** 有没有任何自定义（用来决定「重置布局」按钮是否高亮）。 */
export function hasCustomLayout(): boolean {
  return layoutState.side !== null || Object.keys(layoutState.panels).length > 0
}

// ———————————————————————— 纯算术 ————————————————————————

/**
 * 百分比取三位小数。
 * 不能只取一位：默认三等分是 33.333，共用分隔条要保证 A+B 之和不变，
 * 一位小数的舍入误差会让同一行的第三个框差零点几像素放不下，被挤到下一行。
 */
export function roundPct(value: number): number {
  return Math.round(value * 1000) / 1000
}

export function clampHeight(value: number, min = MIN_BOX_H): number {
  return Math.min(MAX_BOX_H, Math.max(min, value))
}

/**
 * 高度拖动的增益。框比 1.5 屏还高时（热点消息列表默认 1 万多像素），鼠标一次最多拖一屏，
 * 1:1 的话要拖十来次才能缩到合适高度；这时按比例放大位移，一把拖到位。普通框恒为 1。
 */
export function dragHeightGain(startH: number, viewportH: number): number {
  if (!(viewportH > 0) || !(startH > 1.5 * viewportH)) return 1
  return startH / (viewportH * 0.8)
}

/**
 * 框在主栏里的宽度（flex-basis）。
 * 主栏有 gap，所以占 F% 的框实际宽度 = F% × (容器宽 + gap) − gap：
 * 这样两个框 40% + 60% 加上中间的 gap 正好铺满一行，不会因为差几个像素被挤到下一行。
 * 再减 0.05px 是给浏览器的小数舍入留余地。
 */
export function widthToBasis(pct: number): string {
  if (pct >= 99.95) return "100%"
  const gapShare = (1 - pct / 100).toFixed(4)
  return `calc(${pct}% - ${gapShare} * var(--gap, ${FALLBACK_GAP}px) - 0.05px)`
}

/** 最小宽度（像素）折算成百分比。 */
export function minPctFor(containerWidth: number, gap: number, minPx = MIN_BOX_PX): number {
  if (containerWidth <= 0) return 0
  return roundPct(((minPx + gap) / (containerWidth + gap)) * 100)
}

/** 鼠标横向移动 deltaPx 像素，折算成百分比变化。 */
export function pxToPctDelta(deltaPx: number, containerWidth: number, gap: number): number {
  if (containerWidth + gap <= 0) return 0
  return (deltaPx / (containerWidth + gap)) * 100
}

export interface WidthDragInput {
  /** 拖动的框起始宽度百分比 */
  startPct: number
  /** 同一行右边紧挨着的框的起始百分比；没有就 null */
  neighborStartPct: number | null
  /** 鼠标横向位移（像素，向右为正） */
  deltaPx: number
  containerWidth: number
  gap: number
  /** 这一行右边还剩多少空位（像素）；有邻居时不用 */
  freePx?: number
  minPx?: number
}

export interface WidthDragResult {
  self: number
  neighbor: number | null
}

/**
 * 拖右边缘：
 * - 右边紧挨着另一个框 → 共用分隔条，两个框一增一减，总和不变，各自不低于最小宽度；
 * - 右边没有框 → 只动自己，最多长到这一行剩余的空位，最少不低于最小宽度。
 */
export function computeWidthDrag(input: WidthDragInput): WidthDragResult {
  const { startPct, neighborStartPct, deltaPx, containerWidth, gap } = input
  const minPct = Math.min(minPctFor(containerWidth, gap, input.minPx ?? MIN_BOX_PX), startPct)
  const delta = pxToPctDelta(deltaPx, containerWidth, gap)
  if (neighborStartPct !== null) {
    const neighborMin = Math.min(minPctFor(containerWidth, gap, input.minPx ?? MIN_BOX_PX), neighborStartPct)
    const total = startPct + neighborStartPct
    const self = roundPct(Math.min(total - neighborMin, Math.max(minPct, startPct + delta)))
    return { self, neighbor: roundPct(total - self) }
  }
  const room = pxToPctDelta(Math.max(0, input.freePx ?? 0), containerWidth, gap)
  const max = Math.min(100, startPct + room)
  return { self: roundPct(Math.min(max, Math.max(minPct, startPct + delta))), neighbor: null }
}

loadLayout()
