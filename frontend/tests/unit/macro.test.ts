import { describe, expect, it } from "vitest"
import {
  chartPolyline,
  describeObservations,
  frequencyLabel,
  macroSourceLabel,
  parseEvidence,
  providerStatusLabel,
  regimeText,
  scoreText,
  unitLabel,
  yearOverYear,
} from "../../src/api/macro"

describe("宏观序列展示", () => {
  it("没有密钥时显示不可用", () => {
    expect(
      providerStatusLabel({ credentialRequired: true, availability: "unavailable" }),
    ).toBe("不可用")
  })

  it("已配置密钥时不把状态写成不可用", () => {
    expect(providerStatusLabel({ credentialRequired: true, availability: "configured" })).toBe(
      "已提供密钥",
    )
  })

  it("公开 CSV 模式算可用，且不说成已提供密钥", () => {
    const label = providerStatusLabel({ credentialRequired: true, availability: "public_csv" })
    expect(label).toBe("公开数据（无需密钥）")
    expect(macroSourceLabel("public_csv")).toBe("FRED 公开 CSV（最新修订值）")
  })

  it("同比只用 12 个月前同月的值，找不到就不出点", () => {
    const rows = [
      { date: "2024-01-01", value: 100 },
      { date: "2024-02-01", value: 101 },
      { date: "2025-01-01", value: 103 },
      { date: "2025-03-01", value: 105 },
    ]
    const result = yearOverYear(rows)
    expect(result).toHaveLength(1)
    expect(result[0]?.date).toBe("2025-01-01")
    expect(result[0]?.value).toBeCloseTo(3, 6)
  })

  it("规则依据行与组合状态翻成中文，不直接展示英文枚举", () => {
    expect(parseEvidence("FEDFUNDS 2026-09-01 3.75 公布日 2026-10-15")).toMatchObject({
      code: "FEDFUNDS",
      date: "2026-09-01",
      value: 3.75,
      published: "2026-10-15",
    })
    expect(parseEvidence("无法解析").code).toBe("")
    expect(regimeText("up-up-tight")).toBe("增长上行 · 通胀上行 · 流动性偏紧")
    expect(regimeText("insufficient_data")).toBe("数据不足，暂不判断")
    expect(frequencyLabel("Monthly")).toBe("月度")
    expect(unitLabel("Index 1982-1984=100")).toBe("指数（1982-1984=100）")
    expect(unitLabel("Billions of Chained 2017 Dollars")).toBe("十亿美元（2017 年不变价）")
    expect(unitLabel("Dollars per Hour")).toBe("美元/小时")
    expect(unitLabel("Number")).toBe("人")
  })

  it("没有观测值时不编造数字", () => {
    expect(describeObservations({ emptyMessage: "该日期还看不到已公布的数值", observations: [] })).toEqual([
      "该日期还看不到已公布的数值",
    ])
  })

  it("少于两个点不画线，缺分数不编造", () => {
    expect(chartPolyline([1])).toBeNull()
    expect(chartPolyline([1, 3])).toContain("0.0,")
    expect(scoreText({ value: null, evidence: [] })).toBe("数据不足，没有编造分数")
  })

  it("展示当时可见的修订，而不是把两次修订并成一个数", () => {
    expect(
      describeObservations({
        emptyMessage: null,
        observations: [
          {
            observation_date: "1899-01-01",
            value: "1.25",
            unit: "Percent",
            realtime_start: "2020-02-07",
            revision_no: 1,
          },
        ],
      }),
    ).toEqual(["1899-01-01 · 1.25 Percent · 发布日次日可见 2020-02-07 · 第 1 次"])
  })
})
