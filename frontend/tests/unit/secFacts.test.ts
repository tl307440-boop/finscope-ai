import { describe, expect, it } from "vitest"
import { describeFacts } from "../../src/api/secFacts"

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
