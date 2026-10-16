import { describe, expect, it } from "vitest"
import {
  describeResearch,
  parseReportBody,
  ruleName,
  ruleStatusLabel,
  ruleTone,
  runStatusLabel,
} from "../../src/api/research"

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

describe("规则与状态的中文说明", () => {
  it("规则编号和状态码不直接展示", () => {
    expect(ruleName("R001")).toBe("应收增速快于收入")
    expect(ruleName("R999")).toBe("规则 R999")
    expect(ruleStatusLabel("not_evaluated")).toBe("未评估")
    expect(ruleTone("triggered")).toBe("danger")
    expect(runStatusLabel("succeeded")).toBe("已完成")
  })
})

describe("研究记录正文解析", () => {
  it("拆出报告期与指标行，保留来源链接", () => {
    const parsed = parseReportBody(
      [
        "苹果 研究记录",
        "报告期 2023-09-30 与 2024-09-28",
        "us-gaap:Assets USD 早期 352583000000 申报号 0000320193-24-000123 来源 https://data.sec.gov/a.json 事实 6d67 后期 364980000000 申报号 0000320193-25-000079 来源 https://data.sec.gov/b.json 事实 987b 变化 无法计算",
        "us-gaap:Revenues USD 早期 暂无 后期 120 申报号 x 来源 https://data.sec.gov/c.json 变化 0.5",
        "没有可引用的上传文档。",
      ].join("\n"),
    )
    expect(parsed.periods).toEqual({ earlier: "2023-09-30", later: "2024-09-28" })
    expect(parsed.metrics).toHaveLength(2)
    expect(parsed.metrics[0]).toMatchObject({
      concept: "us-gaap:Assets",
      earlier: "352583000000",
      later: "364980000000",
      laterSource: "https://data.sec.gov/b.json",
      change: null,
    })
    expect(parsed.metrics[1]).toMatchObject({ earlier: null, later: "120", change: "0.5" })
    expect(parsed.lines).toEqual(["苹果 研究记录", "没有可引用的上传文档。"])
  })
})
