import { describe, expect, it } from "vitest"
import { describeResearch } from "../../src/api/research"

describe("公司研究对比", () => {
  it("两个报告期都保留来源，没有数据不编造风险", () => {
    const lines = describeResearch({
      notice: "本地样例，不是本次从 SEC 拉取。没有数据时不会编造财务数字。",
      metrics: [
        {
          concept: "us-gaap:Revenues",
          earlier: {
            value: "80",
            accessionNo: "0000320193-23-000001",
            sourceUrl: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            filed: "2023-02-01",
          },
          later: {
            value: "120",
            accessionNo: "0000320193-24-000099",
            sourceUrl: "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
            filed: "2024-06-01",
          },
          change: "0.5",
        },
      ],
      unevaluated: ["R006"],
    })
    expect(lines.join("\n")).toContain("0000320193-23-000001")
    expect(lines.join("\n")).toContain("0000320193-24-000099")
    expect(lines.join("\n")).toContain("data.sec.gov")
    expect(lines.join("\n")).toContain("不表示没有风险")
  })
})
