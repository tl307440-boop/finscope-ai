import { describe, expect, it } from "vitest"
import { parseBoardTheses, parseSection, sectionBadge } from "../../src/api/industryMarket"
import { parseLimitUp, parseRotation, parseSurveys } from "../../src/api/marketPulse"
import { parseInsights } from "../../src/api/watchlistInsights"
import { buildRotationOption, rotationBounds } from "../../src/views/parts/pulse/rotationChart"

describe("入库快照角标", () => {
  it("stored 来源标成收盘快照并带日期，实时 ok 标实时，不可用优先", () => {
    expect(sectionBadge(parseSection({ status: "ok", origin: "stored", data_date: "2026-10-16" }))).toEqual({
      text: "收盘快照 2026-10-16",
      tone: "muted",
    })
    expect(sectionBadge(parseSection({ status: "stale", origin: "stored", data_date: "2026-10-16" })).tone).toBe("warn")
    expect(sectionBadge(parseSection({ status: "ok" })).text).toBe("实时")
    expect(sectionBadge(parseSection({ status: "unavailable", origin: "stored" })).text).toBe("暂不可用")
    expect(parseSection({ status: "ok", origin: "weird" }).origin).toBe("live")
  })
})

describe("板块轮动解析", () => {
  const data = {
    level: "1",
    section: { status: "ok", origin: "stored", data_date: "2026-10-16" },
    window: 20,
    step: 5,
    series: [
      {
        code: "BK1201",
        name: "电子",
        quadrant: "领先",
        change_pct: "1.2",
        tail: [
          { trade_date: "2026-10-09", rs_ratio: "101.5", rs_momentum: "99.2" },
          { trade_date: "2026-10-16", rs_ratio: "103.1", rs_momentum: "101.4" },
        ],
      },
      { code: "BK0001", name: "空轨迹", quadrant: "落后", tail: [] },
      { code: "BK0002", name: "坏点", quadrant: "落后", tail: [{ trade_date: "2026-10-16", rs_ratio: "-", rs_momentum: "99" }] },
    ],
  }

  it("丢弃没有有效轨迹点的板块，坐标转成数字", () => {
    const parsed = parseRotation(data)
    expect(parsed.series.map((item) => item.code)).toEqual(["BK1201"])
    expect(parsed.series[0]?.tail[1]).toEqual({ date: "2026-10-16", ratio: 103.1, momentum: 101.4 })
    expect(parsed.section.origin).toBe("stored")
  })

  it("坐标范围以 100 为中心对称，关掉轨迹时只看最新点", () => {
    const parsed = parseRotation(data)
    expect(rotationBounds(parsed.series, true).x).toBeCloseTo(3.6, 1)
    expect(rotationBounds(parsed.series, false).y).toBeCloseTo(1.7, 1)
    expect(rotationBounds([], true)).toEqual({ x: 1.2, y: 1.2 })
    const option = buildRotationOption(parsed.series, false, "") as { series: unknown[]; xAxis: { min: number; max: number } }
    expect(option.series).toHaveLength(1)
    expect(option.xAxis.min + option.xAxis.max).toBeCloseTo(200, 6)
    expect((buildRotationOption(parsed.series, false, "BK1201") as { series: unknown[] }).series).toHaveLength(2)
  })
})

describe("涨停情绪与机构调研解析", () => {
  it("炸板、跌停池缺失时保持 null，股池里缺代码的行被丢弃", () => {
    const parsed = parseLimitUp({
      section: { status: "stale", reason: "炸板池：上游超时" },
      trade_date: "2026-10-16",
      limit_up: 2,
      broken: null,
      seal_rate: null,
      max_streak: 3,
      ladder: [{ streak: 3, stocks: [{ code: "600000", name: "浦发银行", first_seal: "09:31:00", streak: 3 }] }],
      industries: [{ label: "银行", count: 2 }, { count: 1 }],
      seal_times: [{ label: "09:25-09:30", count: 1 }],
      limit_up_stocks: [{ code: "600000", name: "浦发银行", price: "10.5" }, { name: "无代码" }],
    })
    expect(parsed.broken).toBeNull()
    expect(parsed.limitDown).toBeNull()
    expect(parsed.ladder[0]?.stocks[0]?.firstSeal).toBe("09:31:00")
    expect(parsed.industries).toEqual([{ label: "银行", count: 2 }])
    expect(parsed.limitUpStocks).toHaveLength(1)
    expect(parsed.limitUpStocks[0]?.price).toBe(10.5)
  })

  it("调研行业分布不可用时单独标注，不影响主统计", () => {
    const parsed = parseSurveys({
      section: { status: "ok" },
      days: 30,
      since: "2026-09-17",
      events: 12,
      stocks: 9,
      institutions: 210,
      industries: [],
      industry_section: { status: "unavailable", reason: "行情接口限流" },
      top_stocks: [{ code: "300750", name: "宁德时代", events: 2, institutions: 80, latest: "2026-10-15", ways: ["特定对象调研", 1] }],
      recent: [{ code: "300750", name: "宁德时代", notice_date: "2026-10-15", institutions: 40 }, { code: "1", name: "缺日期" }],
    })
    expect(parsed.events).toBe(12)
    expect(parsed.industrySection.status).toBe("unavailable")
    expect(parsed.topStocks[0]?.ways).toEqual(["特定对象调研"])
    expect(parsed.recent).toHaveLength(1)
  })
})

describe("自选看板与研究假设解析", () => {
  it("缺报价的自选股保留为 null，不补零", () => {
    const parsed = parseInsights({
      group_name: "白酒",
      quote_section: { status: "stale", origin: "stored", data_date: "2026-10-16" },
      signal_section: { status: "ok" },
      summary: { count: 2, quoted: 1, up: 1, down: 0, flat: 0, average_change_pct: "1.50", limit_up: 0, surveyed: 1, hot: 0 },
      stocks: [
        { instrument_id: "a", display_symbol: "600519.SH", code: "600519", name: "贵州茅台", change_pct: "1.5", surveys: 1, origin: "stored" },
        { instrument_id: "b", display_symbol: "000858.SZ", code: "000858", name: "五粮液", origin: "stored" },
      ],
      industries: [{ name: "白酒Ⅱ", board_code: "BK0896", count: 2, market_cap: "0", weight_pct: null }],
      unsupported: ["AAPL"],
    })
    expect(parsed.summary.averageChangePct).toBe(1.5)
    expect(parsed.summary.weightedChangePct).toBeNull()
    expect(parsed.stocks[1]?.changePct).toBeNull()
    expect(parsed.industries[0]?.weightPct).toBeNull()
    expect(parsed.quoteSection.dataDate).toBe("2026-10-16")
    expect(parsed.unsupported).toEqual(["AAPL"])
  })

  it("研究假设缺标题的条目被丢弃，依据条数缺失记 0", () => {
    const parsed = parseBoardTheses({
      board_name: "白酒Ⅱ",
      industries: [
        { id: "i1", name: "白酒", match: "同名", theses: [{ id: "t1", title: "提价", status: "has_support", support: 2 }, { id: "t2" }] },
        { name: "缺 id" },
      ],
      rules: ["按名称匹配"],
    })
    expect(parsed.industries).toHaveLength(1)
    expect(parsed.industries[0]?.theses).toEqual([
      { id: "t1", title: "提价", hypothesis: "", status: "has_support", rationale: "", support: 2, challenge: 0 },
    ])
  })
})
