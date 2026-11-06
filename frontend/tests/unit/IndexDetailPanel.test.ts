import { createPinia, setActivePinia } from "pinia"
import { cleanup, fireEvent, render, screen } from "@testing-library/vue"
import { defineComponent, h, nextTick } from "vue"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// 图表只关心传进去的 series 有哪些：渲染成一个带 data-series 的占位 div
vi.mock("../../src/charts", () => ({
  VChart: defineComponent({
    name: "VChart",
    props: { option: { type: Object, default: () => ({}) } },
    setup(props) {
      return () => {
        const series = ((props.option as { series?: Array<{ name?: string }> }).series ?? []).map((s) => s.name)
        return h("div", { class: "chart-stub", "data-series": series.join(",") })
      }
    },
  }),
}))

vi.mock("../../src/api/aSharePanorama", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../src/api/aSharePanorama")>()
  return { ...actual, fetchIndexDetail: vi.fn(), fetchStockDetail: vi.fn() }
})

import { fetchIndexDetail, fetchStockDetail, type IndexDetailData } from "../../src/api/aSharePanorama"
import IndexDetailPanel from "../../src/components/market/IndexDetailPanel.vue"
import MarketSplitLayout from "../../src/components/market/MarketSplitLayout.vue"
import { useMarketLinkageStore } from "../../src/stores/marketLinkage"

const mockedIndex = vi.mocked(fetchIndexDetail)
const mockedStock = vi.mocked(fetchStockDetail)

function detail(overrides: Partial<IndexDetailData> = {}): IndexDetailData {
  return {
    code: "000001.SH",
    name: "上证指数",
    category: "A股宽基",
    price: 3649.18,
    change: -40.73,
    changePct: -1.1,
    open: 3612.15,
    high: 3660.49,
    low: 3610.2,
    preClose: 3689.91,
    amountYuan: 772_600_000_000,
    intraday: [
      { time: "09:30", price: 3620, avgPrice: 3620, volume: 100 },
      { time: "09:31", price: 3625, avgPrice: 3622, volume: 200 },
    ],
    dailyBars: [
      { date: "2026-11-04", open: 3700, close: 3689.91, low: 3680, high: 3710, volume: 1000, changePct: -0.3 },
      { date: "2026-11-05", open: 3690, close: 3649.18, low: 3640, high: 3695, volume: 1200, changePct: -1.1 },
    ],
    meta: { tradeDate: "2026-11-06", session: "close", fetchedAt: null, stale: false, sources: ["东方财富 行情"], notes: [], unavailable: {} },
    ...overrides,
  }
}

function renderPanel(): ReturnType<typeof render> {
  return render(IndexDetailPanel, { global: { plugins: [createPinia()] } })
}

beforeEach(() => {
  window.localStorage.clear()
  setActivePinia(createPinia())
  mockedIndex.mockReset()
  mockedStock.mockReset()
})

afterEach(() => {
  cleanup()
})

/** render 用 plugins 里自己的 pinia；这里让 store 和组件共用同一个。 */
function renderWithStore(code: string): { store: ReturnType<typeof useMarketLinkageStore>; view: ReturnType<typeof render> } {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useMarketLinkageStore()
  store.selectIndex(code)
  const view = render(IndexDetailPanel, { global: { plugins: [pinia] } })
  return { store, view }
}

describe("IndexDetailPanel", () => {
  it("用选中的 symbol 取数，显示名称 / 代码 / 分类 / 现价 / 涨跌幅和开高低收昨收成交额", async () => {
    mockedIndex.mockResolvedValue(detail())
    const { view } = renderWithStore("000001.SH")

    await screen.findByText("上证指数")
    expect(mockedIndex).toHaveBeenCalledWith("000001.SH")
    expect(view.container.querySelector(".sdp-code")?.textContent).toBe("000001.SH")
    expect(view.container.querySelector(".idx-cat")?.textContent).toBe("A股宽基")
    expect(view.container.querySelector(".sdp-price")?.textContent).toBe("3,649.18")
    expect(view.container.querySelector(".sdp-chg")?.textContent).toBe("-1.10%")

    const cells = [...view.container.querySelectorAll(".sdp-metric-cell")].map((c) => [c.querySelector(".m-label")?.textContent, c.querySelector(".m-val")?.textContent])
    expect(cells).toEqual([
      ["今开", "3,612.15"],
      ["最高", "3,660.49"],
      ["最低", "3,610.20"],
      ["昨收", "3,689.91"],
      ["涨跌额", "-40.73"],
      ["成交额", "7726亿"],
    ])
  })

  it("不出现个股专属内容：封单 / 涨停原因 / 韭研 / 龙虎榜 / 连板", async () => {
    mockedIndex.mockResolvedValue(detail())
    const { view } = renderWithStore("000001.SH")
    await screen.findByText("上证指数")
    expect(view.container.textContent).not.toMatch(/封单|涨停原因|所属题材|韭研|龙虎榜|连板/)
  })

  it("分时和日K都有数据时画两张图；分时带均价线和量柱", async () => {
    mockedIndex.mockResolvedValue(detail())
    const { view } = renderWithStore("000001.SH")
    await screen.findByText("上证指数")

    const charts = [...view.container.querySelectorAll(".chart-stub")].map((c) => c.getAttribute("data-series"))
    expect(charts).toEqual(["分时现价,分时均价,成交量", "日K,MA5,MA10,MA20,成交量"])
  })

  it("上游没给均价 / 成交量：不画均价线、量柱，图例也不写均价线", async () => {
    mockedIndex.mockResolvedValue(
      detail({
        intraday: [
          { time: "09:30", price: 3620, avgPrice: null, volume: null },
          { time: "09:31", price: 3625, avgPrice: null, volume: null },
        ],
        dailyBars: [{ date: "2026-11-05", open: 1, close: 2, low: 1, high: 2, volume: null, changePct: null }],
      }),
    )
    const { view } = renderWithStore("000001.SH")
    await screen.findByText("上证指数")

    const charts = [...view.container.querySelectorAll(".chart-stub")].map((c) => c.getAttribute("data-series"))
    expect(charts).toEqual(["分时现价", "日K,MA5,MA10,MA20"])
    expect(view.container.querySelector(".chart-mini-legend")?.textContent).not.toContain("均价线")
  })

  it("黄金 / 原油没有分时：显示空状态和上游给的原因，不画分时图，日K照常", async () => {
    const reason = "东方财富暂无分时数据；腾讯财经没有 COMEX / NYMEX 期货分时接口，分时暂不可用"
    mockedIndex.mockResolvedValue(
      detail({
        code: "COMEX黄金",
        name: "黄金",
        category: "大宗商品",
        amountYuan: null,
        intraday: [],
        meta: { tradeDate: "2026-11-06", session: "intraday", fetchedAt: null, stale: false, sources: [], notes: [], unavailable: { 指数分时: reason } },
      }),
    )
    const { view } = renderWithStore("COMEX黄金")
    await screen.findByText("黄金")

    expect(screen.getByText(reason)).toBeTruthy()
    const charts = [...view.container.querySelectorAll(".chart-stub")].map((c) => c.getAttribute("data-series"))
    expect(charts).toEqual(["日K,MA5,MA10,MA20,成交量"])
    // 没有成交额：显示 —，不是 0
    const amount = [...view.container.querySelectorAll(".sdp-metric-cell")].find((c) => c.querySelector(".m-label")?.textContent === "成交额")
    expect(amount?.querySelector(".m-val")?.textContent).toBe("—")
  })

  it("上游没说原因时，分时为空也给一句如实的提示", async () => {
    mockedIndex.mockResolvedValue(detail({ intraday: [] }))
    renderWithStore("000001.SH")
    await screen.findByText("上证指数")
    expect(screen.getByText("暂无分时数据（休市或上游未返回）")).toBeTruthy()
  })

  it("海外 / 商品分时标题注明北京时间，A 股不加", async () => {
    mockedIndex.mockResolvedValueOnce(detail({ code: "NDX.US", name: "纳斯达克", category: "海外指数" }))
    const { view } = renderWithStore("NDX.US")
    await screen.findByText("纳斯达克")
    expect(view.container.querySelector(".chart-mini-title")?.textContent).toContain("北京时间")
    cleanup()

    mockedIndex.mockResolvedValueOnce(detail())
    const cn = renderWithStore("000001.SH")
    await screen.findByText("上证指数")
    expect(cn.view.container.querySelector(".chart-mini-title")?.textContent).not.toContain("北京时间")
  })

  it("中途点了别的指数：重新取数并换成新指数的数据", async () => {
    mockedIndex.mockImplementation(async (code) =>
      code === "NDX.US" ? detail({ code: "NDX.US", name: "纳斯达克", category: "海外指数" }) : detail(),
    )
    const { store } = renderWithStore("000001.SH")
    await screen.findByText("上证指数")

    store.selectIndex("NDX.US")
    await screen.findByText("纳斯达克")
    expect(mockedIndex).toHaveBeenLastCalledWith("NDX.US")
    expect(screen.queryByText("上证指数")).toBeNull()
  })

  it("取数失败：显示错误状态，点重试后再取一次", async () => {
    mockedIndex.mockRejectedValueOnce(new Error("没有找到这个指数或商品"))
    mockedIndex.mockResolvedValueOnce(detail())
    renderWithStore("000001.SH")

    await screen.findByText("指数详情加载失败")
    await fireEvent.click(screen.getByRole("button", { name: /重试/ }))
    await screen.findByText("上证指数")
    expect(mockedIndex).toHaveBeenCalledTimes(2)
  })

  it("没有选中指数时不请求接口", async () => {
    renderPanel()
    await nextTick()
    expect(mockedIndex).not.toHaveBeenCalled()
  })
})

describe("MarketSplitLayout 右侧面板：指数 / 个股二选一", () => {
  // 个股面板换成占位：这里只验证「谁在右侧」；个股面板自己的取数不在这个测试里
  const StockPanelStub = defineComponent({ name: "StockDetailPanel", render: () => h("div", { class: "stock-panel-stub" }) })
  const STUBS = { MarketModuleTabs: true, SeatManagerDrawer: true, StockDetailPanel: StockPanelStub }

  function renderLayout(pinia = createPinia()) {
    setActivePinia(pinia)
    const store = useMarketLinkageStore()
    const view = render(MarketSplitLayout, { global: { plugins: [pinia], stubs: STUBS } })
    return { store, view }
  }

  it("选中指数时右侧是指数面板（不是个股面板），按 symbol 取指数详情", async () => {
    mockedIndex.mockResolvedValue(detail())
    const pinia = createPinia()
    setActivePinia(pinia)
    useMarketLinkageStore().selectIndex("000001.SH")
    const { view } = renderLayout(pinia)

    await screen.findByText("上证指数")
    expect(view.container.querySelector(".index-detail-panel")).not.toBeNull()
    expect(view.container.querySelector(".stock-panel-stub")).toBeNull()
    expect(mockedIndex).toHaveBeenCalledWith("000001.SH")
    expect(mockedStock).not.toHaveBeenCalled()
  })

  it("点个股和点指数互相覆盖，右侧只显示最后点的那个", async () => {
    mockedIndex.mockResolvedValue(detail())
    const { store, view } = renderLayout()
    store.selectStock("300308")
    await nextTick()
    expect(view.container.querySelector(".stock-panel-stub")).not.toBeNull()
    expect(view.container.querySelector(".index-detail-panel")).toBeNull()

    store.selectIndex("000001.SH")
    await screen.findByText("上证指数")
    expect(view.container.querySelector(".stock-panel-stub")).toBeNull()

    store.selectStock("600519")
    await nextTick()
    expect(view.container.querySelector(".stock-panel-stub")).not.toBeNull()
    expect(view.container.querySelector(".index-detail-panel")).toBeNull()
  })

  it("右侧收起时点指数会自动展开", async () => {
    mockedIndex.mockResolvedValue(detail())
    const { store, view } = renderLayout()
    store.setDetailOpen(false)
    await nextTick()
    expect(view.container.querySelector(".index-detail-panel")).toBeNull()
    expect(view.container.querySelector(".stock-panel-stub")).toBeNull()

    store.selectIndex("000001.SH")
    await screen.findByText("上证指数")
    expect(view.container.querySelector(".index-detail-panel")).not.toBeNull()
  })
})
