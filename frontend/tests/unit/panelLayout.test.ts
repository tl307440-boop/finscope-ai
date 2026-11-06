import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  LAYOUT_STORAGE_KEY,
  MAX_BOX_H,
  MAX_SIDE,
  MIN_BOX_H,
  MIN_SIDE,
  clampHeight,
  computeWidthDrag,
  dragHeightGain,
  hasCustomLayout,
  layoutState,
  loadLayout,
  minPctFor,
  persistLayout,
  resetAllLayout,
  resetPanel,
  roundPct,
  sanitizeLayout,
  setPanelSize,
  setSideWidth,
  widthToBasis,
} from "../../src/components/market/panelLayout"

function stored(): unknown {
  const text = window.localStorage.getItem(LAYOUT_STORAGE_KEY)
  return text ? JSON.parse(text) : null
}

beforeEach(() => {
  window.localStorage.clear()
  resetAllLayout()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("市场行情布局：尺寸计算", () => {
  it("高度夹在最小和最大之间", () => {
    expect(clampHeight(10)).toBe(MIN_BOX_H)
    expect(clampHeight(10, 200)).toBe(200)
    expect(clampHeight(500)).toBe(500)
    expect(clampHeight(MAX_BOX_H * 2)).toBe(MAX_BOX_H)
  })

  it("百分比保留三位小数，三等分的两份加起来不会多出零点几像素", () => {
    expect(roundPct(33.3333333)).toBe(33.333)
    expect(roundPct(33.333) + roundPct(100 - 33.333 - 33.333)).toBeCloseTo(66.667, 3)
  })

  it("整行宽写成 100%，其余按「占比 × (容器 + gap) − gap」折算", () => {
    expect(widthToBasis(100)).toBe("100%")
    const basis = widthToBasis(40)
    expect(basis).toContain("calc(40%")
    expect(basis).toContain("0.6000")
    expect(basis).toContain("var(--gap")
  })

  it("最小宽度折算成百分比：容器越窄占比越大", () => {
    const wide = minPctFor(1600, 14)
    const narrow = minPctFor(800, 14)
    expect(narrow).toBeGreaterThan(wide)
    expect(minPctFor(0, 14)).toBe(0)
  })

  it("高框拖动有增益，普通框 1:1", () => {
    expect(dragHeightGain(400, 900)).toBe(1)
    expect(dragHeightGain(900 * 1.5, 900)).toBe(1)
    expect(dragHeightGain(10000, 1000)).toBeCloseTo(12.5, 5)
    expect(dragHeightGain(10000, 0)).toBe(1)
  })
})

describe("市场行情布局：横向拖动", () => {
  const base = { containerWidth: 1000, gap: 14 }

  it("共用分隔条：一增一减，总和不变", () => {
    const r = computeWidthDrag({ ...base, startPct: 50, neighborStartPct: 50, deltaPx: -200 })
    expect(r.neighbor).not.toBeNull()
    expect(r.self + (r.neighbor as number)).toBeCloseTo(100, 3)
    expect(r.self).toBeLessThan(50)
  })

  it("共用分隔条：任何一边都不会小于最小宽度", () => {
    const min = minPctFor(1000, 14)
    const left = computeWidthDrag({ ...base, startPct: 50, neighborStartPct: 50, deltaPx: -5000 })
    expect(left.self).toBeCloseTo(min, 3)
    const right = computeWidthDrag({ ...base, startPct: 50, neighborStartPct: 50, deltaPx: 5000 })
    expect(right.neighbor as number).toBeCloseTo(min, 3)
  })

  it("三等分共用分隔条后，同一行的三个框总宽不超过一行", () => {
    const third = 33.333
    const r = computeWidthDrag({ ...base, startPct: third, neighborStartPct: third, deltaPx: -137 })
    const sum = r.self + (r.neighbor as number) + third
    expect(sum).toBeLessThanOrEqual(third * 3 + 0.0011)
  })

  it("右边没有框：只动自己，最多长到剩余空位", () => {
    const grow = computeWidthDrag({ ...base, startPct: 40, neighborStartPct: null, deltaPx: 9999, freePx: 0 })
    expect(grow.self).toBe(40)
    expect(grow.neighbor).toBeNull()
    const grow2 = computeWidthDrag({ ...base, startPct: 40, neighborStartPct: null, deltaPx: 9999, freePx: 300 })
    expect(grow2.self).toBeGreaterThan(40)
    expect(grow2.self).toBeLessThanOrEqual(100)
  })

  it("右边没有框：往里拖不能窄过最小宽度", () => {
    const r = computeWidthDrag({ ...base, startPct: 60, neighborStartPct: null, deltaPx: -9999, freePx: 0 })
    expect(r.self).toBeCloseTo(minPctFor(1000, 14), 3)
  })
})

describe("市场行情布局：记住与重置", () => {
  it("setPanelSize 写进 localStorage，换一次「加载」还在", () => {
    setPanelSize("indices.volume", { h: 640.4 })
    setPanelSize("indices.volume", { w: 41.23456 })
    expect(stored()).toEqual({ v: 1, panels: { "indices.volume": { h: 640, w: 41.235 } } })

    layoutState.panels = {}
    loadLayout()
    expect(layoutState.panels["indices.volume"]).toEqual({ h: 640, w: 41.235 })
  })

  it("拖动过程中 persist=false 只动内存，松手再写盘", () => {
    setPanelSize("ladder.tiers", { h: 700 }, false)
    expect(layoutState.panels["ladder.tiers"]?.h).toBe(700)
    expect(stored()).toBeNull()
    persistLayout()
    expect(stored()).toEqual({ v: 1, panels: { "ladder.tiers": { h: 700 } } })
  })

  it("高度被夹在上下限里再保存", () => {
    setPanelSize("a", { h: 5 })
    expect(layoutState.panels.a?.h).toBe(MIN_BOX_H)
    setPanelSize("a", { h: MAX_BOX_H + 999 })
    expect(layoutState.panels.a?.h).toBe(MAX_BOX_H)
  })

  it("resetPanel：只清宽、只清高、全清；清空后这个框的记录整条消失", () => {
    setPanelSize("a", { w: 50, h: 300 })
    resetPanel("a", "w")
    expect(layoutState.panels.a).toEqual({ h: 300 })
    resetPanel("a", "h")
    expect(layoutState.panels.a).toBeUndefined()
    expect(stored()).toBeNull()
    setPanelSize("b", { w: 30, h: 200 })
    resetPanel("b")
    expect(layoutState.panels.b).toBeUndefined()
  })

  it("右侧详情宽度夹在范围内", () => {
    setSideWidth(10)
    expect(layoutState.side).toBe(MIN_SIDE)
    setSideWidth(9999)
    expect(layoutState.side).toBe(MAX_SIDE)
    setSideWidth(null)
    expect(layoutState.side).toBeNull()
  })

  it("「重置布局」：所有框和右侧宽度一起回默认，localStorage 的 key 删掉", () => {
    setPanelSize("indices.volume", { h: 640 })
    setPanelSize("news.list", { w: 40 })
    setSideWidth(600)
    expect(hasCustomLayout()).toBe(true)
    expect(stored()).not.toBeNull()

    resetAllLayout()
    expect(layoutState.panels).toEqual({})
    expect(layoutState.side).toBeNull()
    expect(hasCustomLayout()).toBe(false)
    expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull()
  })

  it("脏数据（手改 / 旧版本）不影响页面：非法的项直接丢掉", () => {
    const clean = sanitizeLayout({
      side: 100,
      panels: {
        ok: { w: 50, h: 400 },
        badW: { w: -5, h: 300 },
        badH: { w: 40, h: 3 },
        junk: "x",
        nan: { w: Number.NaN },
        "": { w: 10 },
      },
    })
    expect(clean.side).toBeNull()
    expect(clean.panels.ok).toEqual({ w: 50, h: 400 })
    expect(clean.panels.badW).toEqual({ h: 300 })
    expect(clean.panels.badH).toEqual({ w: 40 })
    expect(clean.panels.junk).toBeUndefined()
    expect(clean.panels.nan).toBeUndefined()
    expect(sanitizeLayout("garbage")).toEqual({ panels: {}, side: null })
    expect(sanitizeLayout(null)).toEqual({ panels: {}, side: null })
  })

  it("localStorage 里不是 JSON：加载时当没有记录", () => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, "{not json")
    setPanelSize("x", { h: 300 }, false)
    loadLayout()
    expect(layoutState.panels).toEqual({})
  })

  it("localStorage 写不进去（隐私模式 / 配额满）：不抛错，本次会话照样生效", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError")
    })
    expect(() => setPanelSize("a", { h: 333 })).not.toThrow()
    expect(layoutState.panels.a?.h).toBe(333)
  })
})
