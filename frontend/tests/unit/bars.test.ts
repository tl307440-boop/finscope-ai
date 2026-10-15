import { describe, expect, it } from "vitest"
import { candleMarks, maLabel, returnLabel, summarizeBars } from "../../src/api/bars"

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
            ma5: null,
            ma10: null,
            ma20: null,
            ma60: null,
            simple_return: null,
          },
        ],
      }),
    ).toEqual(["1999-01-04 开1.1 高1.3 低1 收1.2 CNY 量10手"])
  })

  it("不补缺失日，样本不足时均线和收益率留空", () => {
    const friday = bar("2024-01-05", "10", "11")
    const monday = bar("2024-01-08", "11", "12")
    const marks = candleMarks([friday, monday])
    expect(marks).toHaveLength(2)
    expect(maLabel(20, null)).toBe("MA20 样本不足")
    expect(returnLabel(null)).toBe("收益率：样本不足")
    expect(returnLabel("0.1")).toBe("收益率 +10.00%")
    expect(marks[1].rising).toBe(true)
  })
})

function bar(date: string, open: string, close: string) {
  return {
    trade_date: date,
    open,
    high: close,
    low: open,
    close,
    volume: "10",
    currency: "CNY",
    volume_unit: "hand",
    ma5: null,
    ma10: null,
    ma20: null,
    ma60: null,
    simple_return: null,
  }
}
