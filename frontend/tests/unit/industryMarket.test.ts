import { describe, expect, it } from "vitest"
import {
  companySymbol,
  levelLabel,
  momentumQuadrant,
  parseDetail,
  parseOverview,
  parseSection,
} from "../../src/api/industryMarket"
import { heatColor, pctText } from "../../src/views/parts/industry/chartKit"

const overview = {
  level: "1",
  section: { status: "ok", fetched_at: "2026-10-17T07:00:00Z" },
  breadth: { boards: 2, boards_up: 1, boards_down: 1, stocks_up: 100, stocks_down: 80, stocks_flat: 3, total_main_net_inflow: "-120000000" },
  boards: [
    { code: "BK1201", name: "电子", kind: "industry", level: 1, change_pct: "1.25", change_10d_pct: "-3.1", market_cap: "1000", advancers: 61 },
    { code: "BK1157", name: "飞行汽车", kind: "concept", level: 0, change_pct: "-", leader_name: "万丰奥威" },
    { name: "缺代码" },
  ],
  sources: [{ name: "东方财富 板块行情", url: "https://quote.eastmoney.com/" }],
  rules: ["规则一", 3],
}

describe("板块总览解析", () => {
  it("十进制字符串转数字，'-' 与缺失保持为 null，丢弃缺代码的行", () => {
    const parsed = parseOverview(overview)
    expect(parsed.boards.map((row) => row.code)).toEqual(["BK1201", "BK1157"])
    expect(parsed.boards[0]?.changePct).toBe(1.25)
    expect(parsed.boards[0]?.change10dPct).toBe(-3.1)
    expect(parsed.boards[1]?.changePct).toBeNull()
    expect(parsed.breadth?.totalMainNetInflow).toBe(-120000000)
    expect(parsed.rules).toEqual(["规则一"])
  })

  it("未知状态一律当作不可用", () => {
    expect(parseSection({ status: "weird" }).status).toBe("unavailable")
    expect(parseSection(null).status).toBe("unavailable")
    expect(parseSection({ status: "stale", reason: "上游失败" }).reason).toBe("上游失败")
  })
})

describe("板块详情解析", () => {
  it("区块缺失时给出空结构，不抛错", () => {
    const detail = parseDetail({ board: { code: "BK1036", name: "半导体", level: 2 } })
    expect(detail.trend.bars).toEqual([])
    expect(detail.members.subIndustries).toEqual([])
    expect(detail.valuation.peTtm).toBeNull()
    expect(detail.valuation.section.status).toBe("unavailable")
  })

  it("丢弃缺 OHLC 的日线，保留均线为空的早期样本", () => {
    const detail = parseDetail({
      board: { code: "BK1036", name: "半导体" },
      trend: {
        section: { status: "ok" },
        bars: [
          { trade_date: "2026-10-15", open: "1", close: "2", high: "3", low: "0.5", ma20: null },
          { trade_date: "2026-10-16", open: "1", close: null, high: "3", low: "0.5" },
        ],
        returns: { d20: "-14.59", excess_d60: "3.20" },
      },
      valuation: { section: { status: "ok" }, pe_ttm: { current: "94.1", percentile: "88.5", p20: "40" } },
    })
    expect(detail.trend.bars).toHaveLength(1)
    expect(detail.trend.bars[0]?.ma20).toBeNull()
    expect(detail.trend.returns.d20).toBe(-14.59)
    expect(detail.valuation.peTtm?.percentile).toBe(88.5)
  })

  it("没有板块信息时报错", () => {
    expect(() => parseDetail({})).toThrow()
  })
})

describe("展示辅助", () => {
  it("公司页代码只给沪深两市", () => {
    expect(companySymbol("600519", "SSE")).toBe("600519.SH")
    expect(companySymbol("000858", "SZSE")).toBe("000858.SZ")
    expect(companySymbol("830799", "BSE")).toBe("")
  })

  it("层级、象限与百分数文案", () => {
    expect(levelLabel("industry", 2)).toBe("二级")
    expect(levelLabel("concept", 0)).toBe("概念")
    expect(momentumQuadrant(2, 10)).toBe("强势延续")
    expect(momentumQuadrant(-2, 10)).toBe("高位回调")
    expect(momentumQuadrant(2, -10)).toBe("低位反弹")
    expect(momentumQuadrant(null, 10)).toBe("数据不足")
    expect(pctText(1.234)).toBe("+1.23%")
    expect(pctText(null)).toBe("—")
  })

  it("热力色：涨偏红、跌偏绿、缺值为中性色", () => {
    const up = heatColor(5, 4)
    const down = heatColor(-5, 4)
    expect(up).not.toBe(down)
    expect(heatColor(null, 4)).toBe(heatColor(0, 4))
  })
})
