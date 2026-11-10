import { describe, expect, it } from "vitest"

import type { DragonTigerData, LhbSeat } from "../../src/api/aSharePanorama"
import { groupDragonTiger } from "../../src/api/dragonTigerGrouping"
import {
  DEFAULT_HOT_MONEY_SEATS,
  UNVERIFIED_SEAT_BRANCHES,
  matchHotMoneyByBranch,
  normalizeSeatGroup,
  upgradeBuiltinSeatGroups,
  type HotMoneySeatGroup,
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

  // 以下名称全部取自东方财富数据中心龙虎榜 2025-09 ~ 2026-11 的真实披露（OPERATEDEPT_NAME 原文）。
  it("章盟主的江苏路席位按交易所披露的全称（带「长宁区」）能识别", () => {
    for (const name of [
      "国泰海通证券股份有限公司上海长宁区江苏路证券营业部",
      "国泰海通证券股份有限公司长宁区江苏路证券营业部",
      "国泰海通证券股份有限公司上海江苏路营业部",
    ]) {
      expect(matchHotMoneyByBranch(name)?.groupId, name).toBe("zhang-mengzhu")
    }
  })

  it("同一个人的多个席位都能识别到本人", () => {
    const cases: Array<[string, string]> = [
      ["国泰海通证券股份有限公司上海浦东新区海阳西路证券营业部", "zhang-mengzhu"],
      ["中信证券股份有限公司杭州延安路证券营业部", "zhang-mengzhu"],
      ["中信证券股份有限公司上海虹口区溧阳路证券营业部", "sun-ge"],
      ["中信证券股份有限公司上海黄浦区淮海中路证券营业部", "sun-ge"],
      ["中国银河证券股份有限公司绍兴证券营业部", "zhao-laoge"],
      ["中国银河证券股份有限公司北京阜成路证券营业部", "zhao-laoge"],
      ["华鑫证券有限责任公司上海茅台路证券营业部", "chaogu-yangjia"],
      ["华鑫证券有限责任公司上海长宁区红宝石路证券营业部", "chaogu-yangjia"],
      ["国泰海通证券股份有限公司南京金融城证券营业部", "zuoshou-xinyi"],
      ["东方证券股份有限公司上海浦东新区源深路证券营业部", "xiao-eyu"],
      ["光大证券股份有限公司佛山绿景路证券营业部", "foshan-wuyingjiao"],
      ["华泰证券股份有限公司深圳益田路荣超商务中心证券营业部", "huanle-haian"],
      ["中信建投证券股份有限公司北京东城分公司", "hujialou-beijing"],
      ["广发证券股份有限公司上海浦东新区东方路证券营业部", "mao-laoban"],
      ["国泰海通证券股份有限公司上海松江区中山东路证券营业部", "yezi-azhu"],
      ["国盛证券有限责任公司宁波桑田路证券营业部", "ningbo-sangtianlu"],
      ["中国银河证券股份有限公司大连黄河路证券营业部", "chen-xiaoqun"],
      ["甬兴证券有限公司宁波和源路证券营业部", "yu-ge"],
      ["联储证券股份有限公司杭州环城北路证券营业部", "shangtang-lu"],
      ["开源证券股份有限公司西安西大街证券营业部", "quant-priority"],
      ["东方财富证券股份有限公司山南香曲东路证券营业部", "lasa-tiantuan"],
      ["东方财富证券股份有限公司昌都两江大道证券营业部", "lasa-tiantuan"],
      ["高盛(中国)证券有限责任公司上海浦东新区世纪大道证券营业部", "foreign-capital"],
      ["摩根大通证券(中国)有限公司上海银城中路证券营业部", "foreign-capital"],
      ["沪股通专用", "foreign-capital"],
    ]
    for (const [name, groupId] of cases) {
      expect(matchHotMoneyByBranch(name)?.groupId, name).toBe(groupId)
    }
  })

  it("地名不同的同一家券商营业部不会被误认成游资席位", () => {
    for (const name of [
      "国泰海通证券股份有限公司上海徐汇区宛平南路证券营业部",
      "中国银河证券股份有限公司绍兴柯桥鉴湖路证券营业部",
      "中国银河证券股份有限公司杭州绍兴路证券营业部",
      "中信证券股份有限公司深圳前海分公司",
      "联储证券股份有限公司陕西分公司",
      "华鑫证券有限责任公司上海自贸试验区分公司",
      "招商证券股份有限公司上海长宁区江苏路证券营业部",
    ]) {
      expect(matchHotMoneyByBranch(name), name).toBeNull()
    }
  })

  it("内置字典里的每个全称都命中自己所在的席位组（重复写在两个组里的由排在前面的组认领）", () => {
    const shared = new Set([
      "国泰海通证券股份有限公司武汉紫阳东路证券营业部",
      "国泰海通证券股份有限公司上海静安区新闸路证券营业部",
    ])
    for (const group of DEFAULT_HOT_MONEY_SEATS) {
      for (const branch of group.branches) {
        if (UNVERIFIED_SEAT_BRANCHES.includes(branch) || shared.has(branch)) continue
        expect(matchHotMoneyByBranch(branch)?.groupId, `${group.name}:${branch}`).toBe(group.id)
      }
    }
  })

  it("还没查到全称的席位仍按简称保留，数量不多", () => {
    const all = new Set(DEFAULT_HOT_MONEY_SEATS.flatMap((g) => g.branches))
    for (const short of UNVERIFIED_SEAT_BRANCHES) expect(all.has(short), short).toBe(true)
    expect(UNVERIFIED_SEAT_BRANCHES.length).toBeLessThanOrEqual(8)
  })

  it("除没查到全称的席位和机构/股通专用外，内置营业部都写全称", () => {
    const special = new Set(["机构专用", "深股通专用", "沪股通专用", ...UNVERIFIED_SEAT_BRANCHES])
    for (const group of DEFAULT_HOT_MONEY_SEATS) {
      for (const branch of group.branches) {
        if (special.has(branch)) continue
        expect(/(股份有限公司|有限责任公司|有限公司)/.test(branch), `${group.name}:${branch}`).toBe(true)
      }
    }
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

describe("旧版本地席位字典升级", () => {
  const legacyZhang: HotMoneySeatGroup = {
    id: "zhang-mengzhu",
    name: "章盟主",
    category: "知名游资",
    styleTag: "我改过的风格",
    branches: ["国泰海通上海江苏路", "中信杭州延安路", "我自己加的营业部"],
    builtin: true,
    watched: false,
  }
  const custom: HotMoneySeatGroup = {
    id: "custom_1",
    name: "我的席位",
    category: "知名游资",
    styleTag: "自定义",
    branches: ["国泰海通上海江苏路"],
    watched: true,
  }

  it("内置组换成全称，保留用户加的营业部、风格和关注状态", () => {
    const out = upgradeBuiltinSeatGroups([legacyZhang, custom])
    const zhang = out.find((g) => g.id === "zhang-mengzhu")
    expect(zhang?.branches).toContain("国泰海通证券股份有限公司上海长宁区江苏路证券营业部")
    expect(zhang?.branches).toContain("我自己加的营业部")
    expect(zhang?.branches).not.toContain("国泰海通上海江苏路")
    expect(zhang?.branches).not.toContain("中信杭州延安路")
    expect(zhang?.styleTag).toBe("我改过的风格")
    expect(zhang?.watched).toBe(false)
  })

  it("自定义组原样保留，新字典里的内置组缺了就补上", () => {
    const out = upgradeBuiltinSeatGroups([legacyZhang, custom])
    expect(out.find((g) => g.id === "custom_1")?.branches).toEqual(["国泰海通上海江苏路"])
    expect(out.length).toBe(DEFAULT_HOT_MONEY_SEATS.length + 1)
    expect(out.some((g) => g.id === "institution-seat")).toBe(true)
  })

  it("升级后旧数据里的章盟主也能识别带「长宁区」的真实席位", () => {
    const out = upgradeBuiltinSeatGroups([legacyZhang])
    expect(
      matchHotMoneyByBranch("国泰海通证券股份有限公司上海长宁区江苏路证券营业部", out)?.groupId,
    ).toBe("zhang-mengzhu")
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
