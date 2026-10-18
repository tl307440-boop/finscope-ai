import { describe, expect, it } from "vitest"
import {
  BATCH_LIMIT,
  buildQuadrant,
  buildReadout,
  chinaObservationCodes,
  chunkCodes,
  classify,
  median,
  trendLines,
} from "../../src/api/chinaPanels"

function rows(...pairs: [string, number][]) {
  return pairs.map(([date, value]) => ({ date, value }))
}

const GDP = rows(["2025-07-01", 4.8], ["2025-10-01", 4.5], ["2026-01-01", 4.6], ["2026-04-01", 4.3])
const CORE = rows(["2025-09-01", 0.9], ["2025-12-01", 1.2], ["2026-03-01", 1.1], ["2026-06-01", 1.0])

describe("中国页面板逻辑", () => {
  it("页面序列 = 指标带 18 个 + 走势图额外 2 个（CPI、M2），去重且恰好一批", () => {
    const codes = chinaObservationCodes()
    expect(new Set(codes).size).toBe(codes.length)
    expect(codes).toHaveLength(20)
    expect(codes.slice(18)).toEqual(["CPI_CN", "M2_CN"])
    expect(chunkCodes(codes)).toEqual([codes])
  })

  it("超过批量上限时按 20 分块且不丢不重", () => {
    const many = Array.from({ length: 45 }, (_, index) => `S${index}`)
    const chunks = chunkCodes(many)
    expect(chunks.map((chunk) => chunk.length)).toEqual([BATCH_LIMIT, BATCH_LIMIT, 5])
    expect(chunks.flat()).toEqual(many)
    expect(chunkCodes([])).toEqual([])
  })

  it("中位数：奇数、偶数与空输入", () => {
    expect(median([3, 1, 2])).toBe(2)
    expect(median([4, 1, 2, 3])).toBe(2.5)
    expect(median([])).toBeNull()
  })

  it("象限：核心 CPI 取该季最后一个月配对，分界为中位数，并给出最新点所在象限", () => {
    const model = buildQuadrant({ GDP_CN: GDP, CORE_CPI_CN: CORE }, 3)
    expect(model?.points).toHaveLength(4)
    expect(model?.latest.period).toBe("2026 Q2")
    expect(model?.latest.inflation).toBe(1.0)
    expect(model?.growthMid).toBeCloseTo(4.55, 10)
    expect(model?.inflationMid).toBeCloseTo(1.05, 10)
    // 4.3 < 4.55 且 1.0 < 1.05：增长低、通胀低
    expect(model?.code).toBe("weak")
  })

  it("象限：缺少某季最后一个月的核心 CPI 时跳过该季，不用邻月替代", () => {
    const sparse = CORE.filter((row) => row.date !== "2026-03-01")
    const model = buildQuadrant({ GDP_CN: GDP, CORE_CPI_CN: sparse }, 3)
    // 只剩 3 个配对季度，低于下限 4 → 不定位
    expect(model).toBeNull()
  })

  it("象限：没有数据或只有一侧数据时返回空", () => {
    expect(buildQuadrant({}, 3)).toBeNull()
    expect(buildQuadrant({ GDP_CN: GDP }, 3)).toBeNull()
  })

  it("象限分类四种组合", () => {
    expect(classify(5, 2, 4, 1)).toBe("overheat")
    expect(classify(5, 0, 4, 1)).toBe("goldilocks")
    expect(classify(3, 2, 4, 1)).toBe("stagflation")
    expect(classify(3, 0, 4, 1)).toBe("weak")
  })

  it("走势：只保留已选且至少两期的序列，并按最新观测日裁剪窗口", () => {
    const monthly = rows(["2021-01-01", 1], ["2024-01-01", 2], ["2026-09-01", 3])
    const lines = trendLines({ CPI_CN: monthly, PPI_CN: rows(["2026-09-01", 5]) }, ["CPI_CN", "PPI_CN", "M2_CN"], 2)
    expect(lines.map((line) => line.spec.code)).toEqual(["CPI_CN"])
    expect(lines[0]?.rows.map((row) => row.date)).toEqual(["2026-09-01"])
    expect(trendLines({}, ["CPI_CN"], 2)).toEqual([])
  })

  it("规则解读：方向取最近两期、按 1 位小数取整；缺数据的指标列入未参与判断", () => {
    const readout = buildReadout({
      GDP_CN: GDP,
      INDUS_CN: rows(["2026-08-01", 5.0], ["2026-09-01", 5.04]),
      CPI_CN: rows(["2026-08-01", 0.8], ["2026-09-01", 0.9]),
      PPI_CN: rows(["2026-08-01", 5.0], ["2026-09-01", 4.6]),
      PMI_CN: rows(["2026-09-01", 49.4]),
    })
    const byKey = Object.fromEntries(readout.map((item) => [item.key, item]))
    // GDP 回落（4.6→4.3），工业增加值取整后持平
    expect(byKey.growth?.verdict).toBe("回落")
    expect(byKey.growth?.indicators.find((item) => item.code === "INDUS_CN")?.direction).toBe("flat")
    expect(byKey.growth?.missing).toEqual(["社零当月同比"])
    // CPI 上升、PPI 下降 → 分化
    expect(byKey.inflation?.verdict).toBe("分化")
    expect(byKey.pmi?.verdict).toBe("收缩区间")
    expect(byKey.pmi?.indicators[0]?.text).toContain("低于 50 荣枯线")
    // 货币两条序列都没有数据
    expect(byKey.money?.verdict).toBe("数据不足")
    expect(byKey.money?.missing).toEqual(["M1 同比", "M2 同比"])
  })

  it("规则解读：只有一期的指标不判断方向；全部缺失时每个维度都是数据不足", () => {
    const single = buildReadout({ CPI_CN: rows(["2026-09-01", 0.9]) })
    expect(single.find((item) => item.key === "inflation")?.verdict).toBe("数据不足")
    const empty = buildReadout({})
    expect(empty.every((item) => item.verdict === "数据不足")).toBe(true)
  })
})

