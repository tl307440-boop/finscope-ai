import { describe, expect, it } from "vitest"
import {
  factText,
  industryFamily,
  parseFactQuote,
  parseSectors,
  strengthCaption,
  strengthTone,
} from "../../src/api/industry"
import { conceptAmount, conceptLabel } from "../../src/api/secConcepts"

const sectors = {
  data: {
    sectors: [
      { name: "银行", status: "ok", label: "中性", score: "41.8", coverage: "成分 3 只", notice: "幸存者偏差" },
      { name: "非银金融", status: "ok", label: "偏弱", score: "35.0", coverage: "成分 3 只", notice: "" },
      { name: "电子", status: "insufficient", label: "无法判断", coverage: "板块样本不足，无法判断", notice: "" },
    ],
  },
}

describe("行业强弱", () => {
  it("只有 status=ok 且有分数才算可判断，样本不足不给分数", () => {
    const rows = parseSectors(sectors)
    expect(rows.map((row) => row.scored)).toEqual([true, true, false])
    expect(rows[0]?.score).toBe(41.8)
    expect(rows[0]?.members).toBe(3)
    expect(rows[2]?.score).toBeNull()
    expect(rows[2]?.members).toBeNull()
  })

  it("结论文案是用户语言，样本不足写清门槛", () => {
    const [bank, nonBank, electronics] = parseSectors(sectors)
    expect(strengthCaption(bank)).toBe("中性 · 成分 3 只")
    expect(strengthCaption(electronics)).toBe("样本不足 3 只，暂不判断")
    expect(strengthCaption(null)).toBe("暂无强弱数据")
    expect(strengthTone(bank)).toBe("accent")
    expect(strengthTone(nonBank)).toBe("down")
    expect(strengthTone(electronics)).toBe("muted")
  })

  it("区分申万一级与目录分类", () => {
    expect(industryFamily("sw-bank")).toBe("申万一级")
    expect(industryFamily("information-technology")).toBe("目录分类")
  })
})

describe("已入库事实", () => {
  it("把 XBRL 概念名换成中文，大额数字带量级", () => {
    const view = parseFactQuote("us-gaap:Assets 352583000000 USD 申报号 0000320193-24-000123")
    expect(view).toEqual({ label: "总资产", display: "3,525.83 亿美元", accession: "0000320193-24-000123" })
    expect(factText("us-gaap:EarningsPerShareDiluted 6.08 USD/shares 申报号 x")).toBe("稀释每股收益 6.08 美元/股")
  })

  it("认不出的原文原样保留，未知科目不露概念名", () => {
    expect(factText("董事会决议公告")).toBe("董事会决议公告")
    expect(conceptLabel("us-gaap:SomethingNew")).toBe("其他财务科目")
    expect(conceptAmount(null, "USD")).toEqual({ text: "—", unit: "" })
  })
})
