import { describe, expect, it } from "vitest"
import {
  companyLink,
  parseAnnouncements,
  parseBoards,
  parseIndexBars,
  parseMarket,
  parseScore,
  parseWatch,
} from "../../src/api/dashboard"
import { axisAmount, klineOption, minuteOption, VISIBLE_BARS } from "../../src/views/parts/dashboard/charts"

describe("驾驶舱规则分解析", () => {
  it("Decimal 字符串转数字，丢弃缺权重或缺得分的因子", () => {
    const block = parseScore({
      method: "market-temperature-v2",
      score: "38.9",
      label: "中性偏弱",
      coverage: "1.00",
      factors: [
        { code: "trend", label: "趋势", weight: "0.30", score: "0.97", raw: "中证全指", formula: "f", value: "-9.6117", contribution: "0.29" },
        { code: "broken", label: "坏因子", weight: "0.35", score: null },
      ],
      missing: ["广度（涨跌分布不可用）"],
      previous: "40.0",
      previous_date: "2026-10-15",
      change: "-1.1",
    })
    expect(block.score).toBe(38.9)
    expect(block.factors.map((item) => item.code)).toEqual(["trend"])
    expect(block.factors[0]).toMatchObject({ weight: 0.3, score: 0.97, value: -9.6117, contribution: 0.29 })
    expect(block.missing).toEqual(["广度（涨跌分布不可用）"])
    expect(block.change).toBe(-1.1)
  })

  it("缺数据时不出分，标签回落为「无法判断」", () => {
    const block = parseScore(undefined)
    expect(block.score).toBeNull()
    expect(block.label).toBe("无法判断")
    expect(block.factors).toEqual([])
    expect(block.change).toBeNull()
  })
})

describe("驾驶舱行情解析", () => {
  it("两市全景与涨跌分布原样收窄", () => {
    const market = parseMarket({
      trade_date: "2026-10-16",
      temperature: { score: "38.9", label: "中性偏弱", coverage: "1", factors: [], missing: [] },
      quotes_section: { status: "ok", fetched_at: "2026-10-17T11:21:45Z" },
      breadth_section: { status: "ok" },
      limits_section: { status: "ok" },
      history_section: { status: "ok" },
      indices: [{ symbol: "sh000001", name: "上证指数", last: "3716.28", change_pct: "0.24" }, { name: "无代码" }],
      breadth: { up: 3197, down: 2058, flat: 115, total: 5370, strong: 563, weak: 376, limit_up: 61, limit_down: 10, buckets: [{ bucket: 11, label: "涨停", count: 61 }] },
      limits: { limit_up: 61, broken: 49, limit_down: 10, seal_rate: "55.45", max_streak: 11 },
      market_amount: "1555420779700.00",
      amount_vs_base_pct: "-10.04",
      amounts: [{ day: "2026-10-16", amount: "1555420779700.00", ma20: null }],
      rules: ["温度口径"],
      sources: [{ name: "腾讯行情", url: "https://gu.qq.com/", fetched_at: "2026-10-17T11:21:45Z" }],
    })
    expect(market.indices).toHaveLength(1)
    expect(market.indices[0]).toMatchObject({ symbol: "sh000001", last: 3716.28, changePct: 0.24, open: null })
    expect(market.breadth?.buckets).toEqual([{ bucket: 11, label: "涨停", count: 61 }])
    expect(market.limits?.sealRate).toBe(55.45)
    expect(market.marketAmount).toBe(1555420779700)
    expect(market.amounts[0]?.ma20).toBeNull()
  })

  it("上游失败时分布与涨停池为 null、分区标为不可用", () => {
    const market = parseMarket({ breadth_section: { status: "unavailable", reason: "东财涨跌分布超时" }, breadth: null, limits: null })
    expect(market.breadth).toBeNull()
    expect(market.limits).toBeNull()
    expect(market.breadthSection).toMatchObject({ status: "unavailable", reason: "东财涨跌分布超时" })
    expect(market.quotesSection.status).toBe("unavailable")
    expect(market.temperature.score).toBeNull()
  })

  it("K 线丢弃缺价字段的行，分时成交额缺失按 0", () => {
    const bars = parseIndexBars({
      symbol: "sh000001",
      period: "week",
      prev_close: "3707.48",
      bars: [
        { day: "2026-10-16", open: "3723.8", close: "3716.28", high: "3750.77", low: "3681.28", amount: "7.3e11", ma5: "3757.82" },
        { day: "2026-10-09", open: "1", close: null, high: "1", low: "1" },
      ],
      minutes: [{ clock: "09:30", price: "3723.80" }, { clock: "09:31" }],
    })
    expect(bars.period).toBe("week")
    expect(bars.bars).toHaveLength(1)
    expect(bars.bars[0]).toMatchObject({ close: 3716.28, ma5: 3757.82, ma10: null })
    expect(bars.minutes).toEqual([{ clock: "09:30", price: 3723.8, amount: 0 }])
  })
})

describe("驾驶舱板块、公告与自选解析", () => {
  it("板块热度与较上一交易日变化", () => {
    const boards = parseBoards({
      hot_count: 5,
      previous_hot_count: 3,
      hot_count_change: 2,
      previous_date: "2026-10-15",
      boards: [{ code: "886066", name: "飞行汽车", kind: "概念", index: 99, hot: true, limit_ups: 17, heat_score: "100.0" }, { code: "x", name: "无指数" }],
      gainers: [{ code: "new_fjzz", name: "飞机制造", members: 14, change_pct: "4.20" }],
    })
    expect(boards.boards).toHaveLength(1)
    expect(boards.boards[0]).toMatchObject({ index: 99, hot: true, limitUps: 17, heatScore: 100, leaderName: "" })
    expect(boards.hotCountChange).toBe(2)
    expect(boards.gainers[0]?.changePct).toBe(4.2)
  })

  it("没有入库记录时较昨日为 null", () => {
    const boards = parseBoards({ hot_count: null, boards: [], gainers: [] })
    expect(boards.hotCount).toBeNull()
    expect(boards.hotCountChange).toBeNull()
    expect(boards.heatSection.status).toBe("unavailable")
  })

  it("公告只放行 http(s) 链接", () => {
    const parsed = parseAnnouncements({
      window_start: "2026-10-16",
      window_end: "2026-10-17",
      scanned: 179,
      important: [
        { id: "1", code: "300152", name: "*ST动力", title: "行政处罚决定书", url: "https://static.cninfo.com.cn/a.pdf", published_at: "2026-10-16T16:00:00Z", kind: "风险", level: 3, tone: "fall" },
        { id: "2", title: "恶意链接", url: "javascript:alert(1)", level: 2 },
        { id: "3" },
      ],
      hot: [],
      counts: [{ name: "业绩", count: 29 }],
    })
    expect(parsed.important.map((item) => item.url)).toEqual(["https://static.cninfo.com.cn/a.pdf", ""])
    expect(parsed.important[0]?.heatLevel).toBeNull()
    expect(parsed.counts).toEqual([{ name: "业绩", count: 29 }])
  })

  it("自选：强弱分组计数、走势与信号", () => {
    const watch = parseWatch({
      group_id: "g1",
      group_name: "自选关注",
      groups: [{ id: "g1", name: "自选关注", count: 2 }],
      quote_section: { status: "ok" },
      total: 2,
      strong: 1,
      neutral: 0,
      weak: 0,
      unknown: 1,
      rows: [
        { code: "600519", name: "贵州茅台", last: "1329.00", change_pct: "1.22", change_5d_pct: "5.23", state: "强势", closes: ["1300", "x", "1329"], signals: [{ tone: "violet", text: "热榜#41" }] },
        { code: "830799", name: "北交所", state: null, closes: [], signals: [] },
      ],
    })
    expect(watch.rows[0]).toMatchObject({ change5dPct: 5.23, state: "强势", closes: [1300, 1329] })
    expect(watch.rows[0]?.signals).toEqual([{ tone: "violet", text: "热榜#41" }])
    expect(watch.rows[1]).toMatchObject({ state: "", last: null })
    expect(watch.unknown).toBe(1)
  })

  it("没有分组时为空列表", () => {
    const watch = parseWatch({ groups: [], rows: [], quote_section: { status: "unavailable", reason: "还没有自选分组" } })
    expect(watch.groupId).toBe("")
    expect(watch.rows).toEqual([])
    expect(watch.quoteSection.reason).toBe("还没有自选分组")
  })

  it("公司页链接按代码段区分沪深，北交所不给链接", () => {
    expect(companyLink("600519")).toBe("/company/600519.SH")
    expect(companyLink("300750")).toBe("/company/300750.SZ")
    expect(companyLink("920006")).toBe("")
  })
})

describe("驾驶舱图表配置", () => {
  const bars = Array.from({ length: 150 }, (_, index) => ({
    day: `2026-${String(Math.floor(index / 28) + 1).padStart(2, "0")}-${String((index % 28) + 1).padStart(2, "0")}`,
    open: 100,
    close: 100 + index,
    high: 101 + index,
    low: 99,
    amount: 5e11,
    ma5: null,
    ma10: null,
    ma20: null,
  }))

  it("日 K 只画最近 120 根，并用 ECharts 内置的最高 / 最低标注", () => {
    const option = klineOption(bars, "day")
    const series = option.series as { type: string; data: unknown[]; markPoint?: { data: { type: string; valueDim: string }[] } }[]
    expect(series[0]?.data).toHaveLength(VISIBLE_BARS.day)
    expect(series[0]?.markPoint?.data.map((item) => [item.type, item.valueDim])).toEqual([
      ["max", "highest"],
      ["min", "lowest"],
    ])
    expect(series.map((item) => item.type)).toEqual(["candlestick", "line", "line", "line", "bar"])
  })

  it("分时没有昨收时不画昨收线", () => {
    const option = minuteOption([{ clock: "09:30", price: 1, amount: 1 }, { clock: "09:31", price: 2, amount: 1 }], null)
    const series = option.series as { markLine?: unknown }[]
    expect(series[0]?.markLine).toBeUndefined()
  })

  it("成交额坐标轴换算成亿 / 万亿", () => {
    expect(axisAmount(7.3e11)).toBe("7300亿")
    expect(axisAmount(1.56e12)).toBe("1.6万亿")
  })
})
