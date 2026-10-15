import { describe, expect, it } from "vitest"
import { summarizeBars } from "../../src/api/bars"

describe("日线展示", () => {
  it("没有同步时不编造价格", () => {
    expect(
      summarizeBars({
        emptyMessage: "暂无已同步的日线。未同步的日期不会用演示价格填补。",
        bars: [],
      }),
    ).toEqual(["暂无已同步的日线。未同步的日期不会用演示价格填补。"])
  })

  it("按日期列出开高低收，成交量单位是手", () => {
    expect(
      summarizeBars({
        emptyMessage: null,
        bars: [
          {
            trade_date: "1999-01-04",
            open: "1.1",
            high: "1.3",
            low: "1",
            close: "1.2",
            volume: "10",
            currency: "CNY",
            volume_unit: "hand",
          },
        ],
      }),
    ).toEqual(["1999-01-04 开1.1 高1.3 低1 收1.2 CNY 量10手"])
  })
})
