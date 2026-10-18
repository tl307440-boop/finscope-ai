import { describe, expect, it } from "vitest"
import {
  MIN_TRANSITIONS,
  buildQuadrantModel,
  buildScenario,
  pairQuarterly,
  quantile,
  quarterLastMonth,
} from "../../src/api/macroStats"

function quarterDate(index: number): string {
  const year = 2020 + Math.floor(index / 4)
  const month = 1 + 3 * (index % 4)
  return `${year}-${String(month).padStart(2, "0")}-01`
}

/** n 个季度：增长恒为 5，通胀在 2 / 0 之间交替 → 象限在 过热 / 良性扩张 之间交替。 */
function alternating(count: number, skipInflation: number[] = []) {
  const growth = Array.from({ length: count }, (_, index) => ({ date: quarterDate(index), value: 5 }))
  const inflation = Array.from({ length: count }, (_, index) => ({
    date: `${quarterLastMonth(quarterDate(index))}-01`,
    value: index % 2 === 0 ? 2 : 0,
  })).filter((_, index) => !skipInflation.includes(index))
  return { growth, inflation }
}

describe("宏观统计函数", () => {
  it("分位数线性插值，空输入与越界返回空", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5)
    expect(quantile([10], 0.9)).toBe(10)
    expect(quantile([1, 2, 3, 4, 5], 0.1)).toBeCloseTo(1.4, 10)
    expect(quantile([], 0.5)).toBeNull()
    expect(quantile([1, 2], 1.5)).toBeNull()
  })

  it("季度配对：通胀取该季最后一个月，缺该月就跳过该季", () => {
    const { growth, inflation } = alternating(6, [2])
    const paired = pairQuarterly(growth, inflation)
    expect(paired).toHaveLength(5)
    expect(paired.map((point) => point.period)).not.toContain("2020 Q3")
  })

  it("情景：样本足够时给出转移次数与占比，区间来自历史变化量分位数", () => {
    const { growth, inflation } = alternating(24)
    const model = buildQuadrantModel(growth, inflation, 10)
    expect(model?.code).toBe("goldilocks")
    const scenario = buildScenario(model!)
    expect(scenario.sufficient).toBe(true)
    expect(scenario.transitions).toBe(11)
    const shares = Object.fromEntries(scenario.rows.map((row) => [row.code, row.share]))
    expect(shares.overheat).toBe(1)
    expect(shares.goldilocks).toBe(0)
    expect(scenario.rows.reduce((sum, row) => sum + (row.share ?? 0), 0)).toBeCloseTo(1, 10)
    expect(scenario.growthRange).toMatchObject({ low: 5, mid: 5, high: 5, samples: 23 })
    // 通胀变化量 −2 出现 12 次、+2 出现 11 次；最新值 0
    expect(scenario.inflationRange).toMatchObject({ latest: 0, low: -2, mid: -2, high: 2 })
    expect(scenario.quarters).toBe(24)
  })

  it("情景：缺季度时不跨缺口统计转移", () => {
    const { growth, inflation } = alternating(24, [9])
    const model = buildQuadrantModel(growth, inflation, 10)
    const scenario = buildScenario(model!)
    expect(scenario.quarters).toBe(23)
    // 缺第 9 季：第 8 → 10 季相差 6 个月，不算一次转移；良性扩张后的转移共 10 次
    expect(scenario.transitions).toBe(10)
  })

  it("情景：当前象限的历史样本不足时只给次数，不给百分比；变化量样本不足时不给区间", () => {
    const { growth, inflation } = alternating(6)
    const model = buildQuadrantModel(growth, inflation, 10)
    const scenario = buildScenario(model!)
    expect(scenario.transitions).toBeLessThan(MIN_TRANSITIONS)
    expect(scenario.sufficient).toBe(false)
    expect(scenario.rows.every((row) => row.share === null)).toBe(true)
    expect(scenario.rows.reduce((sum, row) => sum + row.count, 0)).toBe(scenario.transitions)
    expect(scenario.growthRange).toBeNull()
    expect(scenario.inflationRange).toBeNull()
  })

  it("配对季度少于 4 个时没有象限模型", () => {
    const { growth, inflation } = alternating(3)
    expect(buildQuadrantModel(growth, inflation, 10)).toBeNull()
    expect(buildQuadrantModel([], [], 10)).toBeNull()
  })
})
