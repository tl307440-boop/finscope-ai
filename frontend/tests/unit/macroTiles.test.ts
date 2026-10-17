import { describe, expect, it } from "vitest"
import {
  alignedRange,
  buildTile,
  diffSeries,
  periodText,
  saarSeries,
  TILE_SPECS,
  tileCodes,
  type TileCatalogItem,
  type TileSpec,
} from "../../src/api/macroTiles"

function spec(key: string): TileSpec {
  const found = TILE_SPECS.find((entry) => entry.key === key)
  if (!found) {
    throw new Error(`缺少卡片 ${key}`)
  }
  return found
}

function item(code: string, frequency: string, unit = "Percent", provider = "fred", sourceKey = ""): TileCatalogItem {
  return { code, name: code, nameEn: code, frequency, unit, adjustment: "Seasonally Adjusted", provider, sourceKey }
}

describe("指标带口径", () => {
  it("15 格顺序固定，所需代码不超过批量接口上限", () => {
    expect(TILE_SPECS.map((entry) => entry.key)).toEqual([
      "core-pce",
      "cpi",
      "nfp",
      "unrate",
      "ahe",
      "real-gdp",
      "real-pce",
      "ism-svc",
      "ism-mfg",
      "policy",
      "dgs5",
      "dgs10",
      "dgs20",
      "spread",
      "claims",
    ])
    expect(tileCodes()).toHaveLength(16)
    expect(tileCodes().length).toBeLessThanOrEqual(20)
  })

  it("非农当月新增只算相邻月份，断档不出值", () => {
    expect(
      diffSeries([
        { date: "2026-06-01", value: 100 },
        { date: "2026-07-01", value: 150 },
        { date: "2026-09-01", value: 170 },
      ]),
    ).toEqual([{ date: "2026-07-01", value: 50 }])
  })

  it("实际 GDP 折年率按相邻季度四次方，跨季断档不出值", () => {
    const [point] = saarSeries([
      { date: "2026-01-01", value: 100 },
      { date: "2026-04-01", value: 101 },
    ])
    expect(point?.date).toBe("2026-04-01")
    expect(point?.value).toBeCloseTo(4.060401, 5)
    expect(
      saarSeries([
        { date: "2025-10-01", value: 100 },
        { date: "2026-04-01", value: 101 },
      ]),
    ).toEqual([])
  })

  it("政策利率上下限按日期对齐，缺一边的日期丢弃", () => {
    expect(
      alignedRange(
        [
          { date: "2026-09-16", value: 4.25 },
          { date: "2026-09-17", value: 4.0 },
        ],
        [
          { date: "2026-09-17", value: 4.25 },
          { date: "2026-09-18", value: 4.25 },
        ],
      ),
    ).toEqual([{ date: "2026-09-17", low: 4.0, high: 4.25 }])
  })

  it("CPI 显示同比与百分点变化，上升记为 up", () => {
    const tile = buildTile(
      spec("cpi"),
      {
        CPIAUCSL: [
          { date: "2025-08-01", value: 100 },
          { date: "2025-09-01", value: 100 },
          { date: "2026-08-01", value: 103 },
          { date: "2026-09-01", value: 103.72 },
        ],
      },
      [item("CPIAUCSL", "Monthly", "Index 1982-1984=100")],
    )
    expect(tile.value).toBe("3.72")
    expect(tile.unit).toBe("%")
    expect(tile.date).toBe("2026-09")
    expect(tile.deltaText).toBe("+0.72 pct")
    expect(tile.direction).toBe("up")
    expect(tile.spark).toHaveLength(2)
    expect(tile.info).toContain("FRED 代码 CPIAUCSL")
    expect(tile.info).toContain("同比")
  })

  it("政策利率显示目标区间，较上期按上一次调整计", () => {
    const tile = buildTile(
      spec("policy"),
      {
        DFEDTARL: [
          { date: "2026-09-16", value: 4.25 },
          { date: "2026-09-17", value: 4.0 },
          { date: "2026-09-18", value: 4.0 },
        ],
        DFEDTARU: [
          { date: "2026-09-16", value: 4.5 },
          { date: "2026-09-17", value: 4.25 },
          { date: "2026-09-18", value: 4.25 },
        ],
      },
      [item("DFEDTARL", "Daily"), item("DFEDTARU", "Daily")],
    )
    expect(tile.value).toBe("4.00–4.25")
    expect(tile.date).toBe("2026-09-18")
    expect(tile.deltaText).toBe("-0.25 pct")
    expect(tile.direction).toBe("down")
  })

  it("初请换算成千人；ISM 来源写明东方财富与最新修订", () => {
    const claims = buildTile(
      spec("claims"),
      {
        ICSA: [
          { date: "2026-10-04", value: 225000 },
          { date: "2026-10-11", value: 231000 },
        ],
      },
      [item("ICSA", "Weekly", "Number")],
    )
    expect(claims.value).toBe("231")
    expect(claims.unit).toBe("千人")
    expect(claims.deltaText).toBe("+6 千人")
    expect(claims.date).toBe("2026-10-11")

    const ism = buildTile(
      spec("ism-mfg"),
      {
        ISM_MFG: [
          { date: "2026-08-01", value: 54.6 },
          { date: "2026-09-01", value: 54.5 },
        ],
      },
      [item("ISM_MFG", "Monthly", "Index", "eastmoney", "EMG00002790")],
    )
    expect(ism.value).toBe("54.5")
    expect(ism.deltaText).toBe("-0.1 点")
    expect(ism.info).toContain("EMG00002790")
    expect(ism.info).toContain("最新修订")
  })

  it("没有数据时不编造", () => {
    const tile = buildTile(spec("ism-mfg"), {}, [])
    expect(tile.value).toBe("—")
    expect(tile.date).toBe("暂无数据")
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
    expect(tile.spark).toEqual([])
    expect(tile.info).toContain("暂未接入 ISM_MFG")
  })

  it("持平记为 flat，变化文案不出现负零", () => {
    const tile = buildTile(
      spec("unrate"),
      {
        UNRATE: [
          { date: "2026-08-01", value: 4.2 },
          { date: "2026-09-01", value: 4.2000001 },
        ],
      },
      [item("UNRATE", "Monthly")],
    )
    expect(tile.direction).toBe("flat")
    expect(tile.deltaText).toBe("0.00 pct")
  })

  it("较上期只和紧邻上一期比：月度缺一个月时不显示", () => {
    const tile = buildTile(
      spec("unrate"),
      {
        UNRATE: [
          { date: "2026-07-01", value: 4.1 },
          { date: "2026-09-01", value: 4.3 },
        ],
      },
      [item("UNRATE", "Monthly")],
    )
    expect(tile.value).toBe("4.30")
    expect(tile.delta).toBeNull()
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
  })

  it("目录里没有该序列时频率未知，不显示较上期", () => {
    const tile = buildTile(
      spec("unrate"),
      {
        UNRATE: [
          { date: "2026-08-01", value: 4.2 },
          { date: "2026-09-01", value: 4.3 },
        ],
      },
      [],
    )
    expect(tile.value).toBe("4.30")
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
  })

  it("同比因上年缺月丢点后，最新一期的上一期不相邻则不显示较上期", () => {
    const tile = buildTile(
      spec("cpi"),
      {
        CPIAUCSL: [
          { date: "2025-08-01", value: 100 },
          { date: "2025-10-01", value: 100 },
          { date: "2026-08-01", value: 103 },
          { date: "2026-09-01", value: 104 },
          { date: "2026-10-01", value: 105 },
        ],
      },
      [item("CPIAUCSL", "Monthly", "Index 1982-1984=100")],
    )
    expect(tile.value).toBe("5.00")
    expect(tile.date).toBe("2026-10")
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
  })

  it("周度相隔 14 天不显示较上期", () => {
    const tile = buildTile(
      spec("claims"),
      {
        ICSA: [
          { date: "2026-09-27", value: 225000 },
          { date: "2026-10-11", value: 231000 },
        ],
      },
      [item("ICSA", "Weekly", "Number")],
    )
    expect(tile.value).toBe("231")
    expect(tile.deltaText).toBe("—")
    expect(tile.direction).toBe("none")
  })

  it("季度相邻时照常显示较上期", () => {
    const tile = buildTile(
      spec("real-gdp"),
      {
        GDPC1: [
          { date: "2025-10-01", value: 100 },
          { date: "2026-01-01", value: 101 },
          { date: "2026-04-01", value: 102 },
        ],
      },
      [item("GDPC1", "Quarterly", "Billions of Chained 2017 Dollars")],
    )
    expect(tile.date).toBe("2026 Q2")
    expect(tile.deltaText).toBe("-0.04 pct")
    expect(tile.direction).toBe("down")
  })

  it("政策利率走势点数按日度上限封顶", () => {
    const start = Date.UTC(2026, 0, 1)
    const days = Array.from({ length: 130 }, (_, index) =>
      new Date(start + index * 86_400_000).toISOString().slice(0, 10),
    )
    const tile = buildTile(
      spec("policy"),
      {
        DFEDTARL: days.map((date) => ({ date, value: 4.0 })),
        DFEDTARU: days.map((date) => ({ date, value: 4.25 })),
      },
      [item("DFEDTARL", "Daily"), item("DFEDTARU", "Daily")],
    )
    expect(tile.spark).toHaveLength(120)
  })

  it("有换算倍数时说明展示值与原值的关系", () => {
    const tile = buildTile(spec("claims"), {}, [item("ICSA", "Weekly", "Number")])
    expect(tile.info).toContain("单位 人")
    expect(tile.info).toContain("展示值 = 原值 × 0.001（千人）")
    expect(buildTile(spec("cpi"), {}, [item("CPIAUCSL", "Monthly")]).info).not.toContain("展示值")
  })

  it("季度观测日显示为 Qn，月度显示年月，日度原样", () => {
    expect(periodText("2026-04-01", "Quarterly")).toBe("2026 Q2")
    expect(periodText("2026-09-01", "Monthly")).toBe("2026-09")
    expect(periodText("2026-10-15", "Daily")).toBe("2026-10-15")
  })
})
