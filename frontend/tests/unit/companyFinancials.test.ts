import { describe, expect, it } from "vitest"
import { parseFinancials } from "../../src/api/companyFinancials"

describe("公司财务总览解析", () => {
  it("保留后端算好的同比与来源，缺失值保持 null", () => {
    const parsed = parseFinancials({
      data: {
        data_status: "edgar",
        notice: "",
        reference_end: "2026-06-27",
        compare_end: "2025-06-28",
        delta_label: "同比",
        tiles: [
          {
            key: "revenue",
            label: "营业收入",
            kind: "amount",
            value: "109417000000",
            unit: "USD",
            delta: "16.36",
            delta_unit: "%",
            spark: ["94036000000", "109417000000"],
            spark_kind: "bar",
            formula: "单季营业收入",
            source: { form: "10-Q", filed: "2026-07-31", accession_no: "a", source_url: "https://www.sec.gov/x" },
          },
          { key: "grossMargin", label: "毛利率", kind: "ratio", value: null, delta: null, delta_unit: "pct" },
        ],
        trend: { quarter: [{ key: "revenue", label: "营业收入", unit: "USD", points: [{ period_end: "2026-06-27", value: "1", yoy: null }] }] },
        table: { annual: { columns: ["FY2025", "FY2024"], rows: [{ key: "revenue", label: "营业收入", cells: ["3"], yoy: "6.43" }] } },
        cash_vs_profit: [
          { period_end: "2025-09-27", unit: "USD", operating_cash_flow: "111", net_income: "100", multiple: "1.11" },
          { period_end: "2024-09-28", unit: "USD", operating_cash_flow: "50", net_income: "-10", multiple: null },
        ],
        rule_observations: { R002: "经营现金流为净利润的 1.11 倍", R999: 3 },
      },
    })
    expect(parsed?.dataStatus).toBe("edgar")
    expect(parsed?.tiles[0]).toMatchObject({ value: 109417000000, delta: 16.36, deltaUnit: "%" })
    expect(parsed?.tiles[0]?.source?.sourceUrl).toBe("https://www.sec.gov/x")
    expect(parsed?.tiles[1]).toMatchObject({ value: null, delta: null, deltaUnit: "pct", source: null })
    expect(parsed?.trend.annual).toEqual([])
    expect(parsed?.table.annual.rows[0]?.cells).toEqual([3, null])
    expect(parsed?.table.quarter).toEqual({ columns: [], rows: [] })
    expect(parsed?.cashVsProfit[1]).toMatchObject({ netIncome: -10, multiple: null })
    expect(parsed?.ruleObservations).toEqual({ R002: "经营现金流为净利润的 1.11 倍" })
  })

  it("没有 data 时返回 null，未知状态按空处理", () => {
    expect(parseFinancials({})).toBeNull()
    expect(parseFinancials({ data: { data_status: "weird" } })?.dataStatus).toBe("empty")
  })
})
