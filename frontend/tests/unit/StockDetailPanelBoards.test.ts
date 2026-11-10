import { createPinia, setActivePinia } from "pinia"
import { cleanup, render, screen, waitFor } from "@testing-library/vue"
import { defineComponent, h } from "vue"
import { createMemoryHistory, createRouter } from "vue-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../src/charts", () => ({
  VChart: defineComponent({ name: "VChart", props: { option: { type: Object, default: () => ({}) } }, setup: () => () => h("div") }),
}))

vi.mock("../../src/api/aSharePanorama", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../src/api/aSharePanorama")>()
  return { ...actual, fetchStockDetail: vi.fn() }
})

import { fetchStockDetail, type StockDetailData } from "../../src/api/aSharePanorama"
import StockDetailPanel from "../../src/components/market/StockDetailPanel.vue"
import { useMarketLinkageStore } from "../../src/stores/marketLinkage"

const mockedStock = vi.mocked(fetchStockDetail)

function detail(overrides: Partial<StockDetailData> = {}): StockDetailData {
  return {
    code: "002578.SZ",
    name: "闽发铝业",
    price: 5.51,
    preClose: 5.01,
    changePct: 9.98,
    turnoverPct: 3.2,
    floatCapYi: 30,
    sealAmountWan: null,
    sealFloatRatioPct: null,
    streakLabel: "6连板",
    firstSealTime: "",
    sectors: ["有色金属", "工业金属", "新能源车", "光伏概念"],
    industries: ["有色金属", "工业金属"],
    concepts: ["新能源车", "光伏概念"],
    catalystReason: "",
    actionSector: "",
    actionSectorReason: "",
    actionExplain: "",
    intraday: [],
    dailyBars: [],
    lhbDate: null,
    lhbReason: "",
    buySeats: [],
    sellSeats: [],
    meta: { tradeDate: "2026-11-09", session: "close", fetchedAt: null, stale: false, sources: [], notes: [], unavailable: {} },
    ...overrides,
  }
}

async function mountPanel(): Promise<void> {
  const pinia = createPinia()
  setActivePinia(pinia)
  useMarketLinkageStore().selectStock("002578.SZ")
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: "/:rest(.*)*", component: { render: () => null } }] })
  render(StockDetailPanel, { global: { plugins: [pinia, router] } })
}

beforeEach(() => {
  window.localStorage.clear()
  mockedStock.mockReset()
})

afterEach(() => {
  cleanup()
})

describe("StockDetailPanel 所属板块", () => {
  it("分行业与概念两类展示", async () => {
    mockedStock.mockResolvedValue(detail())
    await mountPanel()
    const row = await screen.findByTestId("sdp-sectors")
    await waitFor(() => expect(row.textContent).toContain("光伏概念"))
    expect(row.querySelectorAll(".is-industry")).toHaveLength(2)
    expect(row.querySelectorAll(".sdp-sector-tag")).toHaveLength(4)
  })

  it("旧后端只有 sectors 时，去掉行业后的剩余项当概念", async () => {
    mockedStock.mockResolvedValue(detail({ industries: ["有色金属"], concepts: [], sectors: ["有色金属", "新能源车"] }))
    await mountPanel()
    const row = await screen.findByTestId("sdp-sectors")
    await waitFor(() => expect(row.textContent).toContain("新能源车"))
    expect(row.querySelectorAll(".sdp-sector-tag")).toHaveLength(2)
  })

  it("取不到时整行仍在，写「暂无」而不是静默消失", async () => {
    mockedStock.mockResolvedValue(
      detail({
        sectors: [],
        industries: [],
        concepts: [],
        meta: { tradeDate: null, session: "unknown", fetchedAt: null, stale: false, sources: [], notes: [], unavailable: { 所属板块: "各来源都没有这只股票的板块" } },
      }),
    )
    await mountPanel()
    const row = await screen.findByTestId("sdp-sectors")
    await waitFor(() => expect(row.textContent).toContain("暂无"))
    expect(row.querySelectorAll(".sdp-sector-tag")).toHaveLength(0)
  })
})
