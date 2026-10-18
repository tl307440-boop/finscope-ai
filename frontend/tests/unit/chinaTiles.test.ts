import { describe, expect, it } from "vitest"
import {
  CHINA_TILE_SPECS,
  buildChinaTiles,
  chinaTileCodes,
  type ChinaSeriesInfo,
} from "../../src/api/chinaTiles"

const ORDER = [
  "social-stock",
  "pmi-new-orders",
  "ppi",
  "profit",
  "m1",
  "pmi",
  "retail",
  "house-sales",
  "core-cpi",
  "private-invest",
  "indus-value",
  "ent-mlt-loan",
  "gdp",
  "export",
  "gov-bond",
  "fiscal",
  "pmi-export-orders",
  "mfg-invest",
]

const MANUAL = ["SOCIAL_FINANCING_STOCK_CN", "ENT_MLT_LOAN_CN", "GOV_BOND_NET_CN"]

function info(code: string, frequency = "Monthly"): ChinaSeriesInfo {
  return {
    code,
    name: code,
    unit: "Percent",
    frequency,
    source: "国家统计局",
    sourceAgency: "国家统计局",
    manualOnly: MANUAL.includes(code),
  }
}

function allInfo(): ChinaSeriesInfo[] {
  return chinaTileCodes().map((code) => info(code, code === "GDP_CN" ? "Quarterly" : "Monthly"))
}

describe("中国宏观指标带", () => {
  it("18 格按用户给定顺序排列（DR007 与逆回购利率没有可取得的数据，已去掉），序列代码不重复且不超过批量上限", () => {
    expect(CHINA_TILE_SPECS.map((spec) => spec.key)).toEqual(ORDER)
    const codes = chinaTileCodes()
    expect(codes).toHaveLength(18)
    expect(codes.length).toBeLessThanOrEqual(20)
  })

  it("有观测值时显示数值、日期与较上期变化", () => {
    const tiles = buildChinaTiles(
      {
        PPI_CN: [
          { date: "2026-08-01", value: 4.6 },
          { date: "2026-09-01", value: 5.0 },
        ],
      },
      allInfo(),
    )
    const ppi = tiles.find((tile) => tile.key === "ppi")
    expect(ppi?.value).toBe("5.0")
    expect(ppi?.date).toBe("2026-09")
    expect(ppi?.deltaText).toContain("+0.4")
  })

  it("GDP 按季度显示为「2026 Q2」", () => {
    const tiles = buildChinaTiles({ GDP_CN: [{ date: "2026-04-01", value: 4.3 }] }, allInfo())
    const gdp = tiles.find((tile) => tile.key === "gdp")
    expect(gdp?.value).toBe("4.3")
    expect(gdp?.date).toBe("2026 Q2")
  })

  it("仅限手工导入的序列没有数据时显示「—」，提示里说明不做自动抓取，不编造数值", () => {
    const tiles = buildChinaTiles({}, allInfo())
    for (const key of ["social-stock", "ent-mlt-loan", "gov-bond"]) {
      const tile = tiles.find((item) => item.key === key)
      expect(tile?.value).toBe("—")
      expect(tile?.delta).toBeNull()
      expect(tile?.info).toContain("不做自动抓取")
    }
  })

  it("已被去掉的两个指标不再出现在指标带里", () => {
    const keys = CHINA_TILE_SPECS.map((spec) => spec.key)
    expect(keys).not.toContain("dr007")
    expect(keys).not.toContain("omo-7d")
    expect(chinaTileCodes()).not.toContain("DR007_CN")
    expect(chinaTileCodes()).not.toContain("OMO_7D_CN")
  })

  it("自动来源的序列尚未同步时提示等待同步，而不是手工导入", () => {
    const tiles = buildChinaTiles({}, allInfo())
    const ppi = tiles.find((tile) => tile.key === "ppi")
    expect(ppi?.value).toBe("—")
    expect(ppi?.info).toContain("首次同步完成前为空")
    expect(ppi?.info).not.toContain("不做自动抓取")
  })

  it("累计同比缺 1 月时不跨月计算较上期变化（只有一期时 delta 为空）", () => {
    const tiles = buildChinaTiles({ PRIVATE_INVEST_CN: [{ date: "2026-02-01", value: -3.0 }] }, allInfo())
    const tile = tiles.find((item) => item.key === "private-invest")
    expect(tile?.value).toBe("-3.0")
    expect(tile?.delta).toBeNull()
    expect(tile?.info).toContain("1—2 月合并公布")
  })

  it("序列目录缺失时 18 格仍然完整，且全部为「—」", () => {
    const tiles = buildChinaTiles({}, [])
    expect(tiles).toHaveLength(18)
    expect(tiles.every((tile) => tile.value === "—")).toBe(true)
  })
})
