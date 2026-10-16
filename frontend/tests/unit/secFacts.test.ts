import { describe, expect, it } from "vitest"
import {
  conceptLabel,
  defaultPeriods,
  describeFacts,
  factDisplay,
  formatFact,
  parseFactPoint,
  periodKind,
  periodKindLabel,
  sameShape,
  unitLabel,
  type FactPoint,
} from "../../src/api/secFacts"

function fact(
  concept: string,
  value: number,
  periodEnd: string,
  periodStart = "",
  unit = "USD",
  filed = "2026-07-31",
): FactPoint {
  return {
    concept: `us-gaap:${concept}`,
    unit,
    value,
    raw: String(value),
    periodStart,
    periodEnd,
    form: "10-Q",
    filed,
    fiscalYear: 2026,
    fiscalPeriod: "Q3",
    accessionNo: "0000320193-26-000020",
    sourceUrl: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
  }
}

describe("SEC 样例来源", () => {
  it("没有事实时不编造数字", () => {
    expect(describeFacts({ notice: "本地样例，不是本次从 SEC 拉取。", facts: [] })).toEqual([
      "本地样例，不是本次从 SEC 拉取。",
      "该日期还看不到已申报的事实",
    ])
  })

  it("展示概念、单位、申报号和来源地址", () => {
    const lines = describeFacts({
      notice: "本地样例，不是本次从 SEC 拉取。",
      facts: [
        {
          concept: "us-gaap:Revenues",
          unit: "USD",
          value: "100",
          filed: "2024-02-01",
          accessionNo: "0000320193-24-000001",
          sourceUrl: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
          form: "10-K",
        },
      ],
    })
    expect(lines[1]).toContain("0000320193-24-000001")
    expect(lines[1]).toContain("data.sec.gov")
    expect(lines[1]).toContain("USD")
  })
})

describe("XBRL 概念与单位", () => {
  it("常见概念映射成中文，未知概念只去掉前缀", () => {
    expect(conceptLabel("us-gaap:AssetsCurrent")).toBe("流动资产")
    expect(conceptLabel("us-gaap:CostOfGoodsAndServicesSold")).toBe("营业成本")
    expect(conceptLabel("us-gaap:EarningsPerShareDiluted")).toBe("稀释 EPS")
    expect(conceptLabel("us-gaap:SomethingRare")).toBe("SomethingRare")
  })

  it("金额按亿缩写，每股数保留两位小数", () => {
    expect(formatFact(21837000000, "USD")).toBe("218.37 亿美元")
    expect(factDisplay(6.88, "USD/shares")).toEqual({ text: "6.88", unit: "美元/股" })
    expect(unitLabel("USD/shares")).toBe("美元/股")
    expect(formatFact(null, "USD")).toBe("—")
  })

  it("解析接口行时丢弃没有数值或期末的记录", () => {
    expect(parseFactPoint({ concept: "us-gaap:Assets", value: "abc", period_end: "2026-06-27" })).toEqual([])
    const [row] = parseFactPoint({
      concept: "us-gaap:Assets",
      unit: "USD",
      value: "383266000000",
      period_end: "2026-06-27",
      filed: "2026-07-31",
    })
    expect(row?.value).toBe(383266000000)
    expect(row && periodKind(row)).toBe("instant")
  })
})

describe("期间口径标签", () => {
  const quarter = fact("RevenueFromContractWithCustomerExcludingAssessedTax", 94036000000, "2025-06-28", "2025-03-30")
  const ytd = fact("RevenueFromContractWithCustomerExcludingAssessedTax", 313695000000, "2025-06-28", "2024-09-29")
  const nextQuarter = fact("RevenueFromContractWithCustomerExcludingAssessedTax", 109417000000, "2026-06-27", "2026-03-29")

  it("按期间长度区分单季、累计与全年", () => {
    expect(periodKindLabel(quarter)).toBe("单季")
    expect(periodKindLabel(ytd)).toBe("前 3 季累计")
    expect(periodKind({ periodStart: "2024-09-29", periodEnd: "2025-09-27" })).toBe("annual")
  })

  it("单季和累计不算同口径", () => {
    expect(sameShape(quarter, nextQuarter)).toBe(true)
    expect(sameShape(ytd, nextQuarter)).toBe(false)
  })
})

describe("默认报告期", () => {
  it("取最新一期和上一年同期", () => {
    expect(defaultPeriods(["2024-06-29", "2025-03-29", "2025-06-28", "2026-03-28", "2026-06-27"])).toEqual({
      earlier: "2025-06-28",
      later: "2026-06-27",
    })
  })

  it("没有上年同期时退回上一期", () => {
    expect(defaultPeriods(["2026-03-28", "2026-06-27"])).toEqual({ earlier: "2026-03-28", later: "2026-06-27" })
    expect(defaultPeriods([])).toEqual({ earlier: "", later: "" })
  })
})
