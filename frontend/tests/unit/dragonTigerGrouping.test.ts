import { describe, expect, it } from "vitest"

import type { DragonTigerData, LhbSeat } from "../../src/api/aSharePanorama"
import { groupDragonTiger } from "../../src/api/dragonTigerGrouping"
import {
  DEFAULT_HOT_MONEY_SEATS,
  matchHotMoneyByBranch,
  normalizeSeatGroup,
} from "../../src/config/hotMoneySeatsConfig"

function seat(partial: Partial<LhbSeat> & Pick<LhbSeat, "code" | "branchName">): LhbSeat {
  return {
    name: partial.code,
    side: "buy",
    buyWan: 0,
    sellWan: 0,
    netWan: 0,
    changePct: 10,
    reason: "",
    ...partial,
  }
}

function payload(seats: LhbSeat[]): DragonTigerData {
  return {
    tradeDate: "2026-11-05",
    stocks: [],
    seats,
    meta: {
      tradeDate: "2026-11-05",
      session: "close",
      fetchedAt: null,
      stale: false,
      sources: [],
      notes: [],
      unavailable: {},
    },
  }
}

describe("席位匹配", () => {
  it("国泰君安与国泰海通视为同一营业部", () => {
    const hit = matchHotMoneyByBranch("国泰君安证券股份有限公司南京太平南路证券营业部")
    expect(hit?.groupId).toBe("zuoshou-xinyi")
  })

  it("拉萨东方财富席位归入拉萨天团", () => {
    const hit = matchHotMoneyByBranch("东方财富证券股份有限公司拉萨团结路第二证券营业部")
    expect(hit?.groupId).toBe("lasa-tiantuan")
  })

  it("不在字典里的营业部不匹配", () => {
    expect(matchHotMoneyByBranch("某某证券股份有限公司不存在的营业部")).toBeNull()
  })

  it("非法的本地存储内容被丢弃或收窄", () => {
    expect(normalizeSeatGroup("abc")).toBeNull()
    expect(normalizeSeatGroup({ id: "x", name: "y", branches: "z" })).toBeNull()
    const ok = normalizeSeatGroup({ id: "x", name: "y", branches: ["a", 1, " "], category: "乱写" })
    expect(ok?.branches).toEqual(["a"])
    expect(ok?.category).toBe("知名游资")
    expect(ok?.watched).toBe(true)
  })
})

describe("龙虎榜席位聚合", () => {
  it("只展示关注名单中当天上榜的席位，并按净买入排序", () => {
    const data = payload([
      seat({ code: "000001", branchName: "国泰君安证券股份有限公司南京太平南路证券营业部", buyWan: 5000, netWan: 4000, sellWan: 1000 }),
      seat({ code: "000002", branchName: "中国银河证券股份有限公司绍兴证券营业部", buyWan: 9000, netWan: 9000 }),
      seat({ code: "000003", branchName: "某某证券股份有限公司不存在的营业部", buyWan: 100, netWan: 100 }),
    ])
    const result = groupDragonTiger(data, DEFAULT_HOT_MONEY_SEATS)
    expect(result.rows.map((r) => r.groupId)).toEqual(["zhao-laoge", "zuoshou-xinyi"])
    expect(result.matchedSeatCount).toBe(2)
    expect(result.totalSeatCount).toBe(3)
  })

  it("取消关注的席位不出现在主列表，但外资/机构汇总仍统计", () => {
    const groups = DEFAULT_HOT_MONEY_SEATS.map((g) =>
      g.id === "institution-seat" ? { ...g, watched: false } : g,
    )
    const data = payload([
      seat({ code: "000001", branchName: "机构专用", buyWan: 3000, netWan: 3000 }),
      seat({ code: "000001", branchName: "机构专用", side: "sell", sellWan: 800, netWan: -800 }),
    ])
    // 同一只票上多个「机构专用」是不同机构，金额不同，不能被去重掉
    const result = groupDragonTiger(data, groups)
    expect(result.rows).toHaveLength(0)
    const inst = result.summary.find((s) => s.key === "institution")
    expect(inst?.seatCount).toBe(2)
    expect(inst?.buyWan).toBe(3000)
    expect(inst?.sellWan).toBe(800)
    expect(inst?.netWan).toBe(2200)
  })

  it("同一营业部同时出现在买卖榜时不重复累计", () => {
    const branch = "华鑫证券股份有限公司上海宛平南路证券营业部"
    const data = payload([
      seat({ code: "000001", branchName: branch, side: "buy", buyWan: 2000, sellWan: 500, netWan: 1500 }),
      seat({ code: "000001", branchName: branch, side: "sell", buyWan: 2000, sellWan: 500, netWan: 1500 }),
    ])
    const result = groupDragonTiger(data, DEFAULT_HOT_MONEY_SEATS)
    expect(result.rows).toHaveLength(1)
    expect(result.rows[0]?.netWan).toBe(1500)
    expect(result.rows[0]?.stocks[0]?.branches).toHaveLength(1)
  })

  it("同一游资在同一只票上的多个营业部合并成一行", () => {
    const data = payload([
      seat({ code: "000001", branchName: "国泰君安证券股份有限公司南京太平南路证券营业部", buyWan: 1000, netWan: 1000 }),
      seat({ code: "000001", branchName: "国泰海通证券股份有限公司南京金融城证券营业部", buyWan: 500, netWan: 500 }),
    ])
    const result = groupDragonTiger(data, DEFAULT_HOT_MONEY_SEATS)
    const row = result.rows[0]
    expect(row?.groupId).toBe("zuoshou-xinyi")
    expect(row?.stocks).toHaveLength(1)
    expect(row?.stocks[0]?.buyWan).toBe(1500)
    expect(row?.stocks[0]?.branches).toHaveLength(2)
  })

  it("没有任何席位时返回空结果而不是编造", () => {
    const result = groupDragonTiger(payload([]), DEFAULT_HOT_MONEY_SEATS)
    expect(result.rows).toEqual([])
    expect(result.summary.every((s) => s.seatCount === 0 && s.netWan === 0)).toBe(true)
  })
})
