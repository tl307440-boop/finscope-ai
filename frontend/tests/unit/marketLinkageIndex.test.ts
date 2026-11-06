import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it } from "vitest"

import { useMarketLinkageStore } from "../../src/stores/marketLinkage"

describe("marketLinkage：点指数 / 点个股互相覆盖", () => {
  beforeEach(() => {
    window.localStorage.clear()
    setActivePinia(createPinia())
  })

  it("初始右侧没选任何东西", () => {
    const store = useMarketLinkageStore()
    expect(store.activeStockCode).toBe("")
    expect(store.activeIndexCode).toBe("")
    expect(store.detailOpen).toBe(true)
  })

  it("点指数：原样保存 symbol（不做 6 位截断），个股高亮清空", () => {
    const store = useMarketLinkageStore()
    store.selectStock("300308.SZ")
    expect(store.activeStockCode).toBe("300308")

    store.selectIndex("000001.SH")
    expect(store.activeIndexCode).toBe("000001.SH")
    expect(store.activeStockCode).toBe("")
    // 个股的选择还在，只是右侧现在显示指数
    expect(store.selectedStockCode).toBe("300308")
  })

  it("点个股：覆盖指数，右侧回到个股模式；上证指数 000001.SH 和平安银行 000001 不混", () => {
    const store = useMarketLinkageStore()
    store.selectIndex("000001.SH")
    store.selectStock("000001")

    expect(store.activeIndexCode).toBe("")
    expect(store.activeStockCode).toBe("000001")
    expect(store.detailFocus).toBe("stock")
    expect(store.selectedIndexCode).toBe("000001.SH")
  })

  it("空 / 纯空白代码被忽略", () => {
    const store = useMarketLinkageStore()
    store.selectIndex("   ")
    expect(store.activeIndexCode).toBe("")
    store.selectStock("")
    expect(store.activeStockCode).toBe("")
  })

  it("isStockActive：列表里带 .SZ / .SH 后缀的代码也能和 6 位选中码对上，指数模式下全部不高亮", () => {
    const store = useMarketLinkageStore()
    store.selectStock("300308")
    expect(store.isStockActive("300308.SZ")).toBe(true)
    expect(store.isStockActive("300308")).toBe(true)
    expect(store.isStockActive("600519.SH")).toBe(false)

    store.selectIndex("NDX.US")
    expect(store.isStockActive("300308.SZ")).toBe(false)
  })

  it("点指数会把已收起的右侧面板展开；点个股不改展开状态", () => {
    const store = useMarketLinkageStore()
    store.setDetailOpen(false)
    store.selectStock("300308")
    expect(store.detailOpen).toBe(false)

    store.selectIndex("HSI.HK")
    expect(store.detailOpen).toBe(true)

    store.toggleDetailOpen()
    expect(store.detailOpen).toBe(false)
  })
})

describe("marketLinkage：行情总览页默认选中上证指数", () => {
  beforeEach(() => {
    window.localStorage.clear()
    setActivePinia(createPinia())
  })

  it("什么都没选时默认选指数，但不会去展开已收起的面板", () => {
    const store = useMarketLinkageStore()
    store.setDetailOpen(false)
    store.ensureDefaultIndex("000001.SH")

    expect(store.activeIndexCode).toBe("000001.SH")
    expect(store.detailOpen).toBe(false)
  })

  it("已经点过个股 / 指数时，不覆盖用户的选择", () => {
    const withStock = useMarketLinkageStore()
    withStock.selectStock("300308")
    withStock.ensureDefaultIndex("000001.SH")
    expect(withStock.activeStockCode).toBe("300308")
    expect(withStock.activeIndexCode).toBe("")

    setActivePinia(createPinia())
    const withIndex = useMarketLinkageStore()
    withIndex.selectIndex("NDX.US")
    withIndex.ensureDefaultIndex("000001.SH")
    expect(withIndex.activeIndexCode).toBe("NDX.US")
  })

  it("离开总览页：默认选中的指数撤掉（龙虎榜 / 连板不会凭空出现指数）", () => {
    const store = useMarketLinkageStore()
    store.ensureDefaultIndex("000001.SH")
    store.releaseDefaultIndex()

    expect(store.activeIndexCode).toBe("")
    expect(store.selectedIndexCode).toBe("")
    expect(store.detailFocus).toBe("stock")
  })

  it("离开总览页：用户自己点的指数保留，哪怕他点的正好是默认那个", () => {
    const store = useMarketLinkageStore()
    store.ensureDefaultIndex("000001.SH")
    store.selectIndex("COMEX黄金")
    store.releaseDefaultIndex()
    expect(store.activeIndexCode).toBe("COMEX黄金")

    setActivePinia(createPinia())
    const again = useMarketLinkageStore()
    again.ensureDefaultIndex("000001.SH")
    again.selectIndex("000001.SH")
    again.releaseDefaultIndex()
    expect(again.activeIndexCode).toBe("000001.SH")
  })

  it("默认指数之后用户点个股：右侧切到个股，再离开总览页也不会被撤掉", () => {
    const store = useMarketLinkageStore()
    store.ensureDefaultIndex("000001.SH")
    store.selectStock("300308")
    store.releaseDefaultIndex()

    expect(store.activeStockCode).toBe("300308")
    expect(store.detailFocus).toBe("stock")
  })
})
