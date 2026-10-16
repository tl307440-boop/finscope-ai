import { describe, expect, it } from "vitest"
import { chartableMetrics, headlineMetrics, parseReportBody, sideText } from "../../src/api/reportBody"

const url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
const body = [
  "苹果 研究记录",
  "本地样例，不是本次从 SEC 拉取。",
  "未配置模型，本记录只整理已入库数字。",
  "报告期 2023-09-30 与 2024-09-28",
  `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax USD 早期 383285000000 申报号 A-24 来源 ${url} 事实 f1 后期 391035000000 申报号 A-25 来源 ${url} 事实 f2 变化 无法计算`,
  `us-gaap:EarningsPerShareDiluted USD/shares 早期 6.13 申报号 A-25 来源 ${url} 事实 f3 后期 6.08 申报号 A-25 来源 ${url} 事实 f4 变化 无法计算`,
  `us-gaap:NetIncomeLoss USD 早期 暂无 后期 93736000000 申报号 A-25 来源 ${url} 事实 f5 变化 无法计算`,
  "没有可引用的上传文档。",
].join("\n")

describe("研究记录正文", () => {
  it("拆出标题、口径、报告期和逐条指标", () => {
    const parsed = parseReportBody(body)
    expect(parsed.heading).toBe("苹果 研究记录")
    expect(parsed.notes).toHaveLength(2)
    expect(parsed.periods).toEqual({ earlier: "2023-09-30", later: "2024-09-28" })
    expect(parsed.metrics.map((metric) => metric.label)).toEqual(["营业收入", "稀释每股收益", "净利润"])
    expect(parsed.metrics[0]?.later?.value).toBe(391035000000)
    expect(parsed.metrics[0]?.change).toBeNull()
    expect(parsed.metrics[2]?.earlier).toBeNull()
    expect(parsed.documentNote).toBe("没有可引用的上传文档。")
  })

  it("来源按申报号去重", () => {
    expect(parseReportBody(body).sources.map((item) => item.accession)).toEqual(["A-24", "A-25"])
  })

  it("数字带量级，缺失写暂无", () => {
    const [revenue, eps, net] = parseReportBody(body).metrics
    expect(sideText(revenue?.later ?? null, "USD")).toBe("3,910.35 亿美元")
    expect(sideText(eps?.later ?? null, "USD/shares")).toBe("6.08 美元/股")
    expect(sideText(net?.earlier ?? null, "USD")).toBe("暂无")
  })

  it("图表只放两期都有的金额类指标，KPI 不重复", () => {
    const parsed = parseReportBody(body)
    expect(chartableMetrics(parsed.metrics).map((metric) => metric.label)).toEqual(["营业收入"])
    expect(headlineMetrics(parsed.metrics).map((metric) => metric.label)).toEqual(["营业收入", "净利润"])
  })

  it("不是这种格式的正文不会被误判成指标", () => {
    const parsed = parseReportBody("标题\n一段普通说明")
    expect(parsed.metrics).toEqual([])
    expect(parsed.notes).toEqual(["一段普通说明"])
  })
})
