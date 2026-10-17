import { render, screen } from "@testing-library/vue"
import { describe, expect, it } from "vitest"
import type { TileDirection, TileView } from "../../src/api/macroTiles"
import MacroTicker from "../../src/views/parts/macro/MacroTicker.vue"

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

  it("加载中显示骨架而不是空白", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")], loading: true } })
    expect(container.querySelectorAll(".skeleton")).toHaveLength(8)
    expect(container.querySelectorAll(".ticker-tile")).toHaveLength(0)
  })

  it("已在最左端时向上滚轮不拦截，页面照常滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    const event = new WheelEvent("wheel", { deltaY: -120, cancelable: true })
    track(container).dispatchEvent(event)
    expect(event.defaultPrevented).toBe(false)
  })

  it("右侧还有内容时把纵向滚轮转成横向滚动", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up"), tile("b", "down")] } })
    const element = track(container)
    Object.defineProperty(element, "scrollWidth", { value: 1000, configurable: true })
    Object.defineProperty(element, "clientWidth", { value: 300, configurable: true })
    const event = new WheelEvent("wheel", { deltaY: 120, cancelable: true })
    element.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
  })

  it("容器可聚焦，箭头按钮有可读名称", () => {
    const { container } = render(MacroTicker, { props: { tiles: [tile("a", "up")] } })
    expect(track(container).getAttribute("tabindex")).toBe("0")
    expect(container.querySelector('[aria-label="向左滚动指标"]')).not.toBeNull()
    expect(container.querySelector('[aria-label="向右滚动指标"]')).not.toBeNull()
  })
})
