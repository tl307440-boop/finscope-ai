import { describe, expect, it } from "vitest"
import { chartPolyline, describeObservations, providerStatusLabel, scoreText } from "../../src/api/macro"

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
