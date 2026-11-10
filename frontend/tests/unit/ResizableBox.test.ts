import { cleanup, fireEvent, render } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import ResizableBox from "../../src/components/market/ResizableBox.vue"
import {
  LAYOUT_STORAGE_KEY,
  layoutState,
  resetAllLayout,
} from "../../src/components/market/panelLayout"

/** 把容器宽度、框的矩形固定下来：happy-dom 不排版，量出来全是 0。 */
function fakeLayout(host: HTMLElement, boxes: HTMLElement[], containerWidth = 1000): void {
  Object.defineProperty(host, "clientWidth", { configurable: true, value: containerWidth })
  host.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: containerWidth, bottom: 2000, width: containerWidth, height: 2000, toJSON: () => ({}) })
  for (const box of boxes) {
    const heightOf = () => Number.parseFloat(box.style.height) || 300
    box.getBoundingClientRect = () => ({ x: 0, y: 0, left: 0, top: 0, right: containerWidth, bottom: heightOf(), width: containerWidth, height: heightOf(), toJSON: () => ({}) })
  }
}

function pointer(el: Element, type: string, x: number, y: number): Promise<void> {
  return fireEvent(el, Object.assign(new Event(type, { bubbles: true, cancelable: true }), { clientX: x, clientY: y, button: 0, pointerId: 1 }))
}

async function dragEdge(handle: Element, dx: number, dy: number): Promise<void> {
  await pointer(handle, "pointerdown", 500, 500)
  await pointer(handle, "pointermove", 500 + dx / 2, 500 + dy / 2)
  await pointer(handle, "pointermove", 500 + dx, 500 + dy)
  await pointer(handle, "pointerup", 500 + dx, 500 + dy)
}

const Pair = defineComponent({
  setup() {
    return () =>
      h("div", { class: "host" }, [
        h(ResizableBox, { id: "t.a", defaultW: 50 }, { default: () => h("p", "A") }),
        h(ResizableBox, { id: "t.b", defaultW: 50 }, { default: () => h("p", "B") }),
      ])
  },
})

beforeEach(() => {
  window.localStorage.clear()
  resetAllLayout()
})

afterEach(() => {
  cleanup()
})

describe("ResizableBox 拖动", () => {
  it("拖下边缘：高度变高，记住，框上有「自定义高度」标记", async () => {
    const { container } = render(ResizableBox, { props: { id: "t.solo" }, slots: { default: "<p>内容</p>" } })
    const box = container.querySelector(".mk-box") as HTMLElement
    fakeLayout(container as HTMLElement, [box])

    await dragEdge(box.querySelector(".mk-rz-s") as Element, 0, 120)

    expect(layoutState.panels["t.solo"]?.h).toBe(420)
    expect(box.style.height).toBe("420px")
    expect(box.classList.contains("is-h")).toBe(true)
    expect(JSON.parse(window.localStorage.getItem(LAYOUT_STORAGE_KEY) as string).panels["t.solo"].h).toBe(420)
  })

  it("往上拖：不会矮过最小高度", async () => {
    const { container } = render(ResizableBox, { props: { id: "t.min", minH: 150 }, slots: { default: "<p>内容</p>" } })
    const box = container.querySelector(".mk-box") as HTMLElement
    fakeLayout(container as HTMLElement, [box])

    await dragEdge(box.querySelector(".mk-rz-s") as Element, 0, -5000)
    expect(layoutState.panels["t.min"]?.h).toBe(150)
  })

  it("只允许改高度的框（axes=h）没有右边缘手柄", () => {
    const { container } = render(ResizableBox, { props: { id: "t.h", axes: "h" }, slots: { default: "<p>内容</p>" } })
    expect(container.querySelector(".mk-rz-e")).toBeNull()
    expect(container.querySelector(".mk-rz-s")).not.toBeNull()
  })

  it("没有 id 的框不可拖（没有任何手柄），也不会写记录", () => {
    const { container } = render(ResizableBox, { props: { id: "" }, slots: { default: "<p>内容</p>" } })
    expect(container.querySelector(".mk-rz")).toBeNull()
    expect(Object.keys(layoutState.panels)).toHaveLength(0)
  })

  it("拖右边缘：宽度变成百分比，下限不低于最小宽度", async () => {
    const { container } = render(ResizableBox, { props: { id: "t.w" }, slots: { default: "<p>内容</p>" } })
    const box = container.querySelector(".mk-box") as HTMLElement
    fakeLayout(container as HTMLElement, [box])

    await dragEdge(box.querySelector(".mk-rz-e") as Element, -400, 0)
    const w = layoutState.panels["t.w"]?.w as number
    expect(w).toBeGreaterThan(55)
    expect(w).toBeLessThan(65)
    // flex-basis 的 calc(… var(--gap) …) 在 happy-dom 里不解析，真实浏览器里的效果由 Playwright 实拖验证

    await dragEdge(box.querySelector(".mk-rz-e") as Element, -9000, 0)
    const min = layoutState.panels["t.w"]?.w as number
    // 1000px 容器里 320px 最小宽度，占比约 32.9%
    expect(min).toBeGreaterThan(30)
    expect(min).toBeLessThan(36)
  })

  it("同一行相邻两个框共用分隔条：一个窄了另一个宽了，总和不变", async () => {
    const { container } = render(Pair)
    const host = container.querySelector(".host") as HTMLElement
    const [a, b] = Array.from(host.querySelectorAll<HTMLElement>(".mk-box"))
    fakeLayout(host, [a, b])

    await dragEdge(a.querySelector(".mk-rz-e") as Element, -200, 0)

    const wa = layoutState.panels["t.a"]?.w as number
    const wb = layoutState.panels["t.b"]?.w as number
    expect(wa).toBeLessThan(50)
    expect(wb).toBeGreaterThan(50)
    expect(wa + wb).toBeCloseTo(100, 2)
  })

  it("双击边缘：这个框和共用分隔条的邻居一起回默认，key 删掉", async () => {
    const { container } = render(Pair)
    const host = container.querySelector(".host") as HTMLElement
    const [a, b] = Array.from(host.querySelectorAll<HTMLElement>(".mk-box"))
    fakeLayout(host, [a, b])

    await dragEdge(a.querySelector(".mk-rz-e") as Element, -200, 0)
    expect(layoutState.panels["t.a"]).toBeDefined()

    await fireEvent.dblClick(a.querySelector(".mk-rz-e") as Element)
    expect(layoutState.panels["t.a"]).toBeUndefined()
    expect(layoutState.panels["t.b"]).toBeUndefined()
    expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull()
  })

  it("键盘：聚焦下边缘按 ↓ 加高、Enter 恢复", async () => {
    const { container } = render(ResizableBox, { props: { id: "t.key" }, slots: { default: "<p>内容</p>" } })
    const box = container.querySelector(".mk-box") as HTMLElement
    fakeLayout(container as HTMLElement, [box])
    const handle = box.querySelector(".mk-rz-s") as Element

    await fireEvent.keyDown(handle, { key: "ArrowDown" })
    expect(layoutState.panels["t.key"]?.h).toBe(324)
    await fireEvent.keyDown(handle, { key: "Enter" })
    expect(layoutState.panels["t.key"]).toBeUndefined()
  })

  it("只点一下不动：不产生记录", async () => {
    const { container } = render(ResizableBox, { props: { id: "t.click" }, slots: { default: "<p>内容</p>" } })
    const box = container.querySelector(".mk-box") as HTMLElement
    fakeLayout(container as HTMLElement, [box])
    const handle = box.querySelector(".mk-rz-s") as Element

    await pointer(handle, "pointerdown", 500, 500)
    await pointer(handle, "pointerup", 500, 500)
    expect(layoutState.panels["t.click"]).toBeUndefined()
    expect(window.localStorage.getItem(LAYOUT_STORAGE_KEY)).toBeNull()
  })
})
