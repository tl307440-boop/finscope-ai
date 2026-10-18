import { fireEvent, render, screen } from "@testing-library/vue"
import { describe, expect, it } from "vitest"
import { nextTick } from "vue"
import type { TileDirection, TileView } from "../../src/api/macroTiles"
import MacroTicker from "../../src/views/parts/macro/MacroTicker.vue"
import tickerSource from "../../src/views/parts/macro/MacroTicker.vue?raw"
import tileSource from "../../src/views/parts/macro/MacroTickerTile.vue?raw"

function tile(key: string, direction: TileDirection): TileView {
  return {
    key,
    label: `指标${key}`,
    caption: "(%)",
    value: "1.00",
    unit: "%",
    date: "2026-09",
    delta: direction === "none" ? null : 0.1,
    deltaText: direction === "none" ? "—" : "+0.10 pct",
    direction,
    spark: [1, 2],
    info: "说明",
  }
}

function track(container: Element): HTMLElement {
  const found = container.querySelector(".track")
  if (!(found instanceof HTMLElement)) {
    throw new Error("缺少滚动容器")
  }
  return found
}

function overflow(element: HTMLElement): void {
  Object.defineProperty(element, "scrollWidth", { value: 1000, configurable: true })
  Object.defineProperty(element, "clientWidth", { value: 300, configurable: true })
}

function button(container: Element, label: string): HTMLButtonElement {
  const found = container.querySelector(`[aria-label="${label}"]`)
  if (!(found instanceof HTMLButtonElement)) {
    throw new Error(`缺少按钮 ${label}`)
  }
  return found
}

describe("美国宏观指标带", () => {
  it("每格都渲染，并按最新方向着色", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down"), tile("c", "none")] } })
    expect(screen.getByText("指标a")).toBeTruthy()
    expect(container.querySelectorAll(".ticker-tile")).toHaveLength(3)
    expect(container.querySelectorAll(".ticker-tile.is-up")).toHaveLength(1)
    expect(container.querySelectorAll(".ticker-tile.is-down")).toHaveLength(1)
    expect(screen.getAllByText("▲")).toHaveLength(1)
    expect(screen.getAllByText("▼")).toHaveLength(1)
  })

  it("指标说明对读屏可见", () => {
    render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    expect(screen.getAllByText("说明")[0]?.classList.contains("sr-only")).toBe(true)
  })

  it("加载中显示骨架而不是空白，并标记忙碌", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")], loading: true } })
    expect(container.querySelectorAll(".skeleton")).toHaveLength(8)
    expect(container.querySelectorAll(".ticker-tile")).toHaveLength(0)
    expect(track(container).getAttribute("aria-busy")).toBe("true")
  })

  it("已在最左端时向上滚轮不拦截，页面照常滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    overflow(element)
    const event = new WheelEvent("wheel", { deltaY: -120, cancelable: true })
    element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(element.scrollLeft).toBe(0)
  })

  it("右侧还有内容时把纵向滚轮转成横向滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    overflow(element)
    const event = new WheelEvent("wheel", { deltaY: 120, cancelable: true })
    element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(element.scrollLeft).toBe(120)
  })

  it("触控板横向滑动不拦截，交给原生横向滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    overflow(element)
    const event = new WheelEvent("wheel", { deltaX: 40, deltaY: 5, cancelable: true })
    element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
    expect(element.scrollLeft).toBe(0)
  })

  it("容器可聚焦且有名称，箭头按钮有可读名称", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    expect(track(container).getAttribute("tabindex")).toBe("0")
    expect(track(container).getAttribute("aria-label")).toBe("美国宏观指标")
    expect(track(container).getAttribute("aria-busy")).toBe("false")
    expect(container.querySelector('[aria-label="向左滚动指标"]')).not.toBeNull()
    expect(container.querySelector('[aria-label="向右滚动指标"]')).not.toBeNull()
  })

  it("可通过 label 指定容器名称（中国页复用同一组件）", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")], label: "中国宏观指标" } })
    expect(track(container).getAttribute("aria-label")).toBe("中国宏观指标")
  })

  it("方向键按一格横移", async () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    overflow(element)
    await fireEvent.keyDown(element, { key: "ArrowRight" })
    expect(element.scrollLeft).toBe(182)
  })

  it("聚焦中的箭头因到端点而隐藏时，焦点移回滚动容器", async () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    overflow(element)
    await fireEvent.scroll(element)
    const right = button(container, "向右滚动指标")
    expect(right.style.display).toBe("")
    right.focus()
    expect(document.activeElement).toBe(right)
    element.scrollLeft = 700
    await fireEvent.scroll(element)
    await nextTick()
    expect(right.style.display).toBe("none")
    expect(document.activeElement).toBe(element)
  })

  it("不使用滚动捕捉，平滑滚动交给 CSS 并尊重减少动态效果", () => {
    expect(tickerSource).not.toMatch(/scroll-snap/)
    expect(tileSource).not.toMatch(/scroll-snap/)
    expect(tickerSource).not.toMatch(/behavior:\s*"smooth"/)
    expect(tickerSource).toMatch(/scroll-behavior:\s*smooth/)
    expect(tickerSource).toMatch(/prefers-reduced-motion: reduce\)\s*\{[^}]*\.track\s*\{\s*scroll-behavior:\s*auto/)
  })
})
