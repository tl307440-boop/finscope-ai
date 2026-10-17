/**
 * 指标带滚轮判定：纯函数，便于离线测试。
 * 只把「纵向滚轮」转成横向滚动；横向滑动、Ctrl/捏合缩放、已到端点时都交给浏览器，
 * 避免拦截了事件却没有滚动，把页面滚动卡住。
 */

export interface ScrollBox {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
}

export interface WheelInput extends ScrollBox {
  deltaX: number
  deltaY: number
  /** WheelEvent.deltaMode：0 像素、1 行、2 页 */
  deltaMode: number
  ctrlKey: boolean
}

/** 按行滚动时每行折算的像素 */
export const LINE_PX = 16

/** 端点留 1px 容差：缩放后 scrollLeft 可能是小数 */
export function atLeftEdge(box: ScrollBox): boolean {
  return box.scrollLeft <= 1
}

export function atRightEdge(box: ScrollBox): boolean {
  return box.scrollLeft + box.clientWidth >= box.scrollWidth - 1
}

/** 返回应横向滚动的像素；null 表示不拦截，让浏览器按默认方式处理 */
export function wheelScrollPx(input: WheelInput): number | null {
  if (input.ctrlKey) {
    return null
  }
  if (Math.abs(input.deltaX) >= Math.abs(input.deltaY) || input.deltaY === 0) {
    return null
  }
  const unit = input.deltaMode === 1 ? LINE_PX : input.deltaMode === 2 ? input.clientWidth : 1
  const delta = input.deltaY * unit
  if ((delta < 0 && atLeftEdge(input)) || (delta > 0 && atRightEdge(input))) {
    return null
  }
  return delta
}
