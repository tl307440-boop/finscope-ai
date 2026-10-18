import { describe, expect, it } from "vitest"
import { buildCompare, indexRows, toCsv, type CompareSpec } from "../../src/api/macroCompare"

const SPECS: CompareSpec[] = [
  { code: "CPI", label: "CPI", unit: "指数", indexable: true, tone: "accent" },
  { code: "JOBS", label: "就业", unit: "千人", indexable: true, tone: "teal" },
  { code: "RATE", label: "失业率", unit: "%", indexable: false, tone: "violet" },
  { code: "PMI", label: "PMI", unit: "点", indexable: false, tone: "cyan" },
  { code: "OLD", label: "新序列", unit: "指数", indexable: true, tone: "pink" },
]

const ROWS = {
  CPI: [
    { date: "2019-12-01", value: 250 },
    { date: "2020-01-01", value: 200 },
    { date: "2021-01-01", value: 210 },
    { date: "2026-09-01", value: 300 },
  ],
  JOBS: [
    { date: "2020-01-01", value: 150000 },
    { date: "2026-09-01", value: 159000 },
  ],
  RATE: [
    { date: "2020-01-01", value: 3.6 },
    { date: "2026-09-01", value: 4.3 },
  ],
  PMI: [
    { date: "2025-09-01", value: 49.4 },
    { date: "2026-09-01", value: 50.1 },
  ],
  // 没有 2020-01 的基期
  OLD: [
    { date: "2021-01-01", value: 100 },
    { date: "2026-09-01", value: 120 },
  ],
}

describe("多指标对比", () => {
  it("指数化：基期当月 = 100，基期之前的观测被丢弃", () => {
    const rows = indexRows(ROWS.CPI)
    expect(rows?.[0]).toEqual({ date: "2020-01-01", value: 100 })
    expect(rows?.map((row) => row.date)).not.toContain("2019-12-01")
    expect(rows?.[rows.length - 1]?.value).toBe(150)
  })

  it("指数化：没有基期当月数据或基期值非正时返回空", () => {
    expect(indexRows(ROWS.OLD)).toBeNull()
    expect(indexRows([{ date: "2020-01-01", value: 0 }])).toBeNull()
    expect(indexRows([])).toBeNull()
  })

  it("指数化视图：只保留水平值且有基期的序列，其余写明原因而不是硬算", () => {
    const result = buildCompare(ROWS, SPECS, ["CPI", "JOBS", "RATE", "OLD"], "index", 5)
    expect(result.lines.map((line) => line.spec.code)).toEqual(["CPI", "JOBS"])
    expect(result.lines.every((line) => line.axis === 0)).toBe(true)
    const reasons = Object.fromEntries(result.skipped.map((item) => [item.code, item.reason]))
    expect(reasons.RATE).toContain("不能指数化")
    expect(reasons.OLD).toContain("2020-01")
  })

  it("原值视图：按单位分左右轴，最多两种单位，第三种单位被跳过并说明", () => {
    const result = buildCompare(ROWS, SPECS, ["CPI", "RATE", "PMI"], "raw", 0)
    expect(result.axisUnits).toEqual(["指数", "%"])
    expect(Object.fromEntries(result.lines.map((line) => [line.spec.code, line.axis]))).toEqual({ CPI: 0, RATE: 1 })
    expect(result.skipped).toEqual([{ code: "PMI", label: "PMI", reason: "最多同时显示两种单位" }])
  })

  it("原值视图：按窗口年数裁剪（0 表示全部），不足两期的序列被跳过", () => {
    const windowed = buildCompare(ROWS, SPECS, ["CPI"], "raw", 1)
    expect(windowed.lines[0]?.rows.map((row) => row.date)).toEqual(["2026-09-01"])
    const all = buildCompare(ROWS, SPECS, ["CPI"], "raw", 0)
    expect(all.lines[0]?.rows).toHaveLength(4)
    const thin = buildCompare({ CPI: [{ date: "2026-09-01", value: 1 }] }, SPECS, ["CPI"], "raw", 0)
    expect(thin.lines).toEqual([])
    expect(thin.skipped[0]?.reason).toContain("不足两期")
  })

  it("CSV：日期并集升序，没有观测的日期留空，表头带单位，含逗号的名称被转义", () => {
    const specs: CompareSpec[] = [
      { code: "A", label: "甲,乙", unit: "%", indexable: false, tone: "accent" },
      { code: "B", label: "丙", unit: "%", indexable: false, tone: "teal" },
    ]
    const result = buildCompare(
      {
        A: [
          { date: "2026-01-01", value: 1.123456 },
          { date: "2026-03-01", value: 2 },
        ],
        B: [
          { date: "2026-02-01", value: 5 },
          { date: "2026-03-01", value: 6 },
        ],
      },
      specs,
      ["A", "B"],
      "raw",
      0,
    )
    expect(toCsv(result).split("\n")).toEqual([
      '日期,"甲,乙（%）",丙（%）',
      "2026-01-01,1.1235,",
      "2026-02-01,,5",
      "2026-03-01,2,6",
    ])
  })

  it("CSV：指数化视图的表头注明基期", () => {
    const result = buildCompare(ROWS, SPECS, ["CPI"], "index", 0)
    expect(toCsv(result).split("\n")[0]).toBe("日期,CPI（指数，2020-01=100）")
  })

  it("没有选中任何序列时结果为空", () => {
    const result = buildCompare(ROWS, SPECS, [], "raw", 5)
    expect(result.lines).toEqual([])
    expect(toCsv(result)).toBe("日期")
  })
})
