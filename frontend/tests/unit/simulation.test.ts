import { describe, expect, it } from "vitest"
import {
  parseExperiment,
  parseRun,
  parseRunList,
  ratingTone,
  signedPercent,
} from "../../src/api/simulation"

const cite = { source: "日线行情", label: "收盘 1312.97", ref: "600519.SH", observed_at: "2026-10-15" }

const run = {
  data: {
    run_id: "r1",
    status: "succeeded",
    symbol: "600519.SH",
    as_of: "2026-10-15",
    rating: "Overweight",
    decision: "增持",
    engine: "finscope-rules",
    engine_label: "规则引擎（确定性规则，未调用语言模型）",
    duration_ms: 812,
    created_at: "2026-10-16T07:00:00Z",
    config: { llm: false, holding_days: 30, max_debate_rounds: 2 },
    detail: {
      subject: { name: "贵州茅台", market: "CN", industry: "白酒" },
      analysts: [
        {
          analyst: "technical",
          role: "技术面分析师",
          coverage: "full",
          stance: "bullish",
          score: "61.5",
          summary: "均线多头排列",
          findings: [{ text: "MA20 上穿 MA60", polarity: 1, weight: "1.5", citations: [cite] }],
          gaps: [],
        },
        {
          analyst: "fundamental",
          role: "基本面分析师",
          coverage: "unavailable",
          stance: "unavailable",
          summary: "A股财报尚未接入",
          findings: [],
          gaps: ["A股财报尚未接入（NOT_IMPLEMENTED）"],
        },
      ],
      debates: [
        {
          round: 1,
          bull: "趋势向上\n反驳：回撤有限",
          bear: "估值偏高",
          bull_citations: [cite],
          bear_citations: [],
          arbitration: "多方证据更充分",
          unsupported: ["估值偏高"],
          converged: true,
        },
      ],
      memo: "研究结论偏多",
      proposals: [
        { rating: "buy", display_rating: "Buy", allocation: "6%–8%", horizon_days: 30, stop_loss: "跌破 MA60", revision: 0, rationale: "初始" },
        { rating: "overweight", display_rating: "Overweight", allocation: "3%–5%", horizon_days: 30, stop_loss: "跌破 MA60", revision: 1, rationale: "修正" },
      ],
      risks: [
        { profile: "conservative", approved: false, content: "单一维度", veto_reason: "只有一个维度有数据", scenario: "回撤", drawdown: "-0.1800", mitigations: ["降低仓位"], revision: 0 },
        { profile: "aggressive", approved: true, content: "可以", revision: 1 },
      ],
      decision: {
        confidence: "79.2",
        allocation: "3%–5%",
        horizon: "30 个交易日",
        stop_loss: "跌破 MA60",
        drivers: [{ text: "MA20 上穿 MA60", polarity: 1, citations: [cite] }],
        risks: [],
        aborted: false,
      },
      memory: [{ as_of: "2025-06-03", rating: "Hold", excess_return: "0.0123", verdict: "accurate", reflection: "验证" }],
    },
  },
}

describe("parseRun", () => {
  it("把后端字符串数字解析为数值，保留引用与修正链", () => {
    const parsed = parseRun(run)
    expect(parsed).not.toBeNull()
    expect(parsed?.confidence).toBe(79.2)
    expect(parsed?.durationMs).toBe(812)
    expect(parsed?.llm).toBe(false)
    expect(parsed?.analysts[0].score).toBe(61.5)
    expect(parsed?.analysts[0].findings[0].citations[0].observedAt).toBe("2026-10-15")
    expect(parsed?.proposals.map((item) => item.displayRating)).toEqual(["Buy", "Overweight"])
    expect(parsed?.risks[0].drawdown).toBe(-0.18)
    expect(parsed?.memory[0].excessReturn).toBe(0.0123)
    expect(parsed?.debates[0].converged).toBe(true)
    expect(parsed?.evaluation).toBeNull()
  })

  it("exclude_none 省略的字段按缺失处理，不补成 0", () => {
    const parsed = parseRun(run)
    const fundamental = parsed?.analysts[1]
    expect(fundamental?.score).toBeNull()
    expect(fundamental?.stance).toBe("unavailable")
    expect(parsed?.risks[1].vetoReason).toBeNull()
    expect(parsed?.risks[1].drawdown).toBeNull()
    expect(parsed?.risks[1].scenario).toBeNull()
  })

  it("没有 data 时返回 null", () => {
    expect(parseRun({ error: { code: "NOT_FOUND" } })).toBeNull()
  })
})

describe("parseRunList / parseExperiment", () => {
  it("未结算的记录 excessReturn 为 null", () => {
    const rows = parseRunList({
      data: { runs: [{ run_id: "a", symbol: "AAPL", as_of: "2026-01-02", rating: "Hold" }] },
    })
    expect(rows[0].excessReturn).toBeNull()
    expect(rows[0].verdict).toBeNull()
  })

  it("解析两种方案与按评级统计", () => {
    const result = parseExperiment({
      data: {
        samples: 12,
        skipped: ["600519.SH 2026-10-01：持有期尚未走完"],
        skipped_count: 1,
        truncated: false,
        modes: [{ code: "multi_agent", label: "多角色", samples: 12, directional_calls: 8, hit_rate: "0.6250" }],
        by_rating: [{ rating: "Buy", count: 3, mean_excess: "0.0210", baseline_count: 0 }],
        rows: [],
      },
    })
    expect(result?.modes[0].hitRate).toBe(0.625)
    expect(result?.modes[0].longMeanExcess).toBeNull()
    expect(result?.byRating[0].baselineMeanExcess).toBeNull()
    expect(result?.skipped).toHaveLength(1)
  })
})

describe("展示辅助", () => {
  it("signedPercent 只做比率到百分比的格式转换", () => {
    expect(signedPercent(0.0123)).toBe("+1.23%")
    expect(signedPercent(-0.18, 1)).toBe("-18.0%")
    expect(signedPercent(null)).toBe("—")
  })

  it("评级配色：看多红、看空绿、无评级中性", () => {
    expect(ratingTone("Buy")).toBe("rise")
    expect(ratingTone("Underweight")).toBe("fall")
    expect(ratingTone("Hold")).toBe("flat")
    expect(ratingTone("REVIEW")).toBe("none")
  })
})
