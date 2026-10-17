import { describe, expect, it } from "vitest"
import { atLeftEdge, atRightEdge, wheelScrollPx, type WheelInput } from "../../src/views/parts/macro/tickerWheel"

function input(overrides: Partial<WheelInput>): WheelInput {
  return {
    deltaX: 0,
    deltaY: 0,
    deltaMode: 0,
    ctrlKey: false,
    scrollLeft: 200,
    scrollWidth: 1000,
    clientWidth: 300,
    ...overrides,
  }
}

describe("指标带滚轮判定", () => {
  it("中段纵向滚轮按像素转成横向", () => {
    expect(wheelScrollPx(input({ deltaY: 53 }))).toBe(53)
    expect(wheelScrollPx(input({ deltaY: -3 }))).toBe(-3)
  })

  it("按住 Ctrl（含触控板捏合缩放）交给浏览器", () => {
    expect(wheelScrollPx(input({ deltaY: 120, ctrlKey: true }))).toBeNull()
  })

  it("横向分量占优（触控板左右滑）交给原生横向滚动", () => {
    expect(wheelScrollPx(input({ deltaX: 40, deltaY: 10 }))).toBeNull()
    expect(wheelScrollPx(input({ deltaX: -20, deltaY: 20 }))).toBeNull()
  })

  it("没有纵向分量时不处理", () => {
    expect(wheelScrollPx(input({ deltaY: 0 }))).toBeNull()
  })

  it("按行滚动时每行换算为 16px", () => {
    expect(wheelScrollPx(input({ deltaY: 3, deltaMode: 1 }))).toBe(48)
    expect(wheelScrollPx(input({ deltaY: -1, deltaMode: 1 }))).toBe(-16)
  })

  it("按页滚动时每页换算为一屏宽", () => {
    expect(wheelScrollPx(input({ deltaY: 1, deltaMode: 2 }))).toBe(300)
  })

  it("已在最左端还往左滚，交给页面纵向滚动", () => {
    expect(wheelScrollPx(input({ deltaY: -120, scrollLeft: 0 }))).toBeNull()
    expect(wheelScrollPx(input({ deltaY: -120, scrollLeft: 1 }))).toBeNull()
    expect(wheelScrollPx(input({ deltaY: 120, scrollLeft: 0 }))).toBe(120)
  })

  it("已在最右端还往右滚，交给页面纵向滚动", () => {
    expect(wheelScrollPx(input({ deltaY: 120, scrollLeft: 700 }))).toBeNull()
    expect(wheelScrollPx(input({ deltaY: 120, scrollLeft: 699.5 }))).toBeNull()
    expect(wheelScrollPx(input({ deltaY: -120, scrollLeft: 700 }))).toBe(-120)
  })

  it("内容放得下（没有溢出）时两个方向都放行", () => {
    const fits = { scrollLeft: 0, scrollWidth: 300, clientWidth: 300 }
    expect(wheelScrollPx(input({ deltaY: 120, ...fits }))).toBeNull()
    expect(wheelScrollPx(input({ deltaY: -120, ...fits }))).toBeNull()
  })

  it("端点判定与箭头显隐共用同一规则", () => {
    expect(atLeftEdge({ scrollLeft: 1, scrollWidth: 1000, clientWidth: 300 })).toBe(true)
    expect(atLeftEdge({ scrollLeft: 2, scrollWidth: 1000, clientWidth: 300 })).toBe(false)
    expect(atRightEdge({ scrollLeft: 699, scrollWidth: 1000, clientWidth: 300 })).toBe(true)
    expect(atRightEdge({ scrollLeft: 698, scrollWidth: 1000, clientWidth: 300 })).toBe(false)
  })
})
