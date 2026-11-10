/**
 * 系统预置游资/派系与关联营业部席位字典配置。
 * 支持在「龙虎榜分析 -> 席位管理」中动态搜索、新增、删除或修改某个人物/派系绑定的常用营业部，
 * 并通过 LocalStorage 持久化。
 *
 * 营业部一律写交易所披露的**全称**（东方财富数据中心龙虎榜 OPERATEDEPT_NAME 的原文）。
 * 以前写的是「国泰海通上海江苏路」这类简称，而真实席位名是
 * 「国泰海通证券股份有限公司上海长宁区江苏路证券营业部」，中间多了「长宁区」，简称对不上，
 * 这个席位的买卖就识别不出来。全称取自 2025-09 ~ 2026-11 近一年龙虎榜的真实披露名称，
 * 同一家营业部改过名（如「上海江苏路营业部」→「上海长宁区江苏路证券营业部」）的几种写法都列上。
 * 近一年没上过榜、查不到全称的少数几家仍保留简称，见 UNVERIFIED_SEAT_BRANCHES。
 */

export type SeatCategory = "知名游资" | "量化打板" | "外资" | "散户大本营" | "机构专用"

export const SEAT_CATEGORIES: readonly SeatCategory[] = [
  "知名游资",
  "量化打板",
  "外资",
  "散户大本营",
  "机构专用",
]

export interface HotMoneySeatGroup {
  id: string
  name: string
  category: SeatCategory
  styleTag: string
  branches: string[]
  note?: string
  builtin?: boolean
  /** 是否在「龙虎榜分析」关注名单里；缺省按已关注处理。 */
  watched?: boolean
}

export function isWatched(group: Pick<HotMoneySeatGroup, "watched">): boolean {
  return group.watched !== false
}

/** 把 localStorage 里读出来的任意值收窄成席位组；不合法的条目丢弃。 */
export function normalizeSeatGroup(raw: unknown): HotMoneySeatGroup | null {
  if (typeof raw !== "object" || raw === null) return null
  const r = raw as Record<string, unknown>
  if (typeof r.id !== "string" || typeof r.name !== "string" || !Array.isArray(r.branches)) {
    return null
  }
  const category = SEAT_CATEGORIES.find((c) => c === r.category) ?? "知名游资"
  const group: HotMoneySeatGroup = {
    id: r.id,
    name: r.name,
    category,
    styleTag: typeof r.styleTag === "string" ? r.styleTag : "",
    branches: r.branches.filter((b): b is string => typeof b === "string" && b.trim() !== ""),
    watched: r.watched !== false,
  }
  if (typeof r.note === "string") group.note = r.note
  if (r.builtin === true) group.builtin = true
  return group
}

export interface SeatMatchResult {
  groupId: string
  name: string
  category: SeatCategory
  styleTag: string
  matchedBranch: string
}

export const HOT_MONEY_STORAGE_KEY = "finscope.hotMoneySeats.v1"

export const DEFAULT_HOT_MONEY_SEATS: HotMoneySeatGroup[] = [
  {
    id: "zhang-mengzhu",
    name: "章盟主",
    category: "知名游资",
    styleTag: "大容量趋势 / 权重主升",
    branches: [
      "国泰海通证券股份有限公司上海长宁区江苏路证券营业部",
      "国泰海通证券股份有限公司长宁区江苏路证券营业部",
      "国泰海通证券股份有限公司上海江苏路营业部",
      "国泰海通证券股份有限公司上海浦东新区海阳西路证券营业部",
      "中信证券股份有限公司杭州延安路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "sun-ge",
    name: "孙哥",
    category: "知名游资",
    styleTag: "主线核心 / 点火接力",
    branches: [
      "中信证券股份有限公司上海溧阳路证券营业部",
      "中信证券股份有限公司上海虹口区溧阳路证券营业部",
      "中信证券股份有限公司上海黄浦区淮海中路证券营业部",
      "中信上海古北路",
    ],
    builtin: true,
  },
  {
    id: "zhao-laoge",
    name: "赵老哥",
    category: "知名游资",
    styleTag: "龙头二波 / 板块共振",
    branches: [
      "中国银河证券股份有限公司绍兴证券营业部",
      "中国银河证券股份有限公司绍兴鲁迅中路证券营业部",
      "浙商证券股份有限公司绍兴分公司",
      "中国银河证券股份有限公司北京阜成路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "chaogu-yangjia",
    name: "炒股养家",
    category: "知名游资",
    styleTag: "情绪周期 / 排板溢价",
    branches: [
      "华鑫证券有限责任公司上海宛平南路证券营业部",
      "华鑫证券有限责任公司上海宛平南路营业部",
      "华鑫证券有限责任公司上海茅台路证券营业部",
      "华鑫证券有限责任公司上海红宝石路证券营业部",
      "华鑫证券有限责任公司上海长宁区红宝石路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "fang-xinxia",
    name: "方新侠",
    category: "知名游资",
    styleTag: "大成交中军 / 格局锁仓",
    branches: [
      "中信证券股份有限公司西安朱雀大街证券营业部",
      "兴业证券股份有限公司陕西分公司",
      "广发证券股份有限公司西安南广济街证券营业部",
    ],
    builtin: true,
  },
  {
    id: "zuoshou-xinyi",
    name: "作手新一",
    category: "知名游资",
    styleTag: "高位连板 / 龙头信仰",
    branches: [
      "国泰海通证券股份有限公司南京太平南路证券营业部",
      "国泰海通证券股份有限公司江苏南京太平南路营业部",
      "国泰海通证券股份有限公司南京金融城证券营业部",
      "国泰海通证券股份有限公司江苏南京金融城营业部",
    ],
    note: "第三席不固定，支持随时添加新营业部",
    builtin: true,
  },
  {
    id: "xiao-eyu",
    name: "小鳄鱼",
    category: "知名游资",
    styleTag: "趋势加速 / 分歧转一致",
    branches: [
      "南京证券股份有限公司南京大钟亭证券营业部",
      "东方证券股份有限公司上海浦东新区源深路证券营业部",
      "中投南京太平南路",
    ],
    builtin: true,
  },
  {
    id: "foshan-wuyingjiao",
    name: "佛山无影脚",
    category: "知名游资",
    styleTag: "独食首板 / 大单封死",
    branches: [
      "光大证券股份有限公司佛山绿景路证券营业部",
      "光大证券股份有限公司佛山季华六路证券营业部",
      "湘财佛山祖庙路",
    ],
    builtin: true,
  },
  {
    id: "huanle-haian",
    name: "欢乐海岸",
    category: "知名游资",
    styleTag: "空间板妖股 / 高位锁仓",
    branches: [
      "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部",
      "中信证券股份有限公司深圳分公司",
      "华泰证券股份有限公司深圳分公司",
      "中泰深圳欢乐海岸",
    ],
    builtin: true,
  },
  {
    id: "hujialou-beijing",
    name: "呼家楼/北京帮",
    category: "知名游资",
    styleTag: "重金主封 / 板块集团军",
    branches: [
      "中信证券股份有限公司北京呼家楼证券营业部",
      "中信证券股份有限公司北京总部证券营业部",
      "中信建投证券股份有限公司北京东城分公司",
    ],
    builtin: true,
  },
  {
    id: "mao-laoban",
    name: "毛老板",
    category: "知名游资",
    styleTag: "主线大容量 / 波段推土机",
    branches: [
      "广发证券股份有限公司上海浦东新区东方路证券营业部",
      "国泰海通证券股份有限公司北京光华路证券营业部",
      "国泰海通证券股份有限公司北京光华路营业部",
      "国泰海通证券股份有限公司北京国贸证券营业部",
      "国泰海通证券股份有限公司北京团结湖证券营业部",
    ],
    builtin: true,
  },
  {
    id: "yezi-azhu",
    name: "叶子阿猪",
    category: "知名游资",
    styleTag: "弹性抱团 / 短线先锋",
    branches: ["国泰海通证券股份有限公司上海松江区中山东路证券营业部"],
    builtin: true,
  },
  {
    id: "ningbo-sangtianlu",
    name: "宁波桑田路",
    category: "知名游资",
    styleTag: "高标接力 / 情绪高潮先锋",
    branches: [
      "国盛证券股份有限公司宁波桑田路证券营业部",
      "国盛证券有限责任公司宁波桑田路证券营业部",
      "国盛证券股份有限公司宁波桑田路营业部",
      "国盛证券有限责任公司宁波桑田路营业部",
    ],
    builtin: true,
  },
  {
    id: "chen-xiaoqun",
    name: "陈小群",
    category: "知名游资",
    styleTag: "绝对人气核心 / 市场总龙头",
    branches: [
      "中国银河证券股份有限公司北京学院南路证券营业部",
      "中国银河证券股份有限公司哈尔滨中山路证券营业部",
      "中国银河证券股份有限公司大连黄河路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "beijing-chaojia",
    name: "北京炒家",
    category: "知名游资",
    styleTag: "首板挖掘 / 高胜率轮动",
    branches: ["长城证券股份有限公司绵阳飞云大道证券营业部"],
    builtin: true,
  },
  {
    id: "yu-ge",
    name: "余哥",
    category: "知名游资",
    styleTag: "20cm弹性 / 科创创业主升",
    branches: [
      "东吴证券股份有限公司苏州相城大道证券营业部",
      "甬兴证券有限公司宁波和源路证券营业部",
      "财通证券股份有限公司普陀山证券营业部",
    ],
    builtin: true,
  },
  {
    id: "xiaoxian-pai",
    name: "消闲派",
    category: "知名游资",
    styleTag: "日内首板排板 / 快速轮动",
    branches: [
      "国泰海通证券股份有限公司宜昌沿江大道证券营业部",
      "国泰海通证券股份有限公司武汉紫阳东路证券营业部",
      "国泰海通证券股份有限公司湖北武汉紫阳东路营业部",
      "国泰海通证券股份有限公司上海静安区新闸路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "shangtang-lu",
    name: "上塘路",
    category: "知名游资",
    styleTag: "低位挖掘 / 次日冲高兑现",
    branches: [
      "财通证券股份有限公司杭州上塘路证券营业部",
      "联储证券股份有限公司浙江分公司",
      "联储证券股份有限公司杭州环城北路证券营业部",
    ],
    builtin: true,
  },
  {
    id: "wenzhou-bang",
    name: "温州帮",
    category: "知名游资",
    styleTag: "协同庄游 / 连续拉升",
    branches: [
      "长江证券股份有限公司深圳红荔路证券营业部",
      "首创证券福州八一七路",
      "首创证券宁波河清北路",
    ],
    builtin: true,
  },
  {
    id: "lasa-tiantuan",
    name: "家人（拉萨天团）",
    category: "散户大本营",
    styleTag: "全市场散户合力风向标",
    branches: [
      "东方财富证券股份有限公司拉萨金融城南环路证券营业部",
      "东方财富证券股份有限公司拉萨团结路第一证券营业部",
      "东方财富证券股份有限公司拉萨团结路第二证券营业部",
      "东方财富证券股份有限公司拉萨东环路第一证券营业部",
      "东方财富证券股份有限公司拉萨东环路第二证券营业部",
      "东方财富证券股份有限公司拉萨东城区江苏大道证券营业部",
      "东方财富证券股份有限公司拉萨江苏东路证券营业部",
      "东方财富证券股份有限公司拉萨达孜区虎峰大道证券营业部",
      "东方财富证券股份有限公司拉萨金珠西路证券营业部",
      "东方财富证券股份有限公司拉萨文创园达瓦路证券营业部",
      "东方财富证券股份有限公司山南香曲东路证券营业部",
      "东方财富证券股份有限公司昌都两江大道证券营业部",
      "东方财富证券股份有限公司林芝广州大道证券营业部",
      "东方财富证券股份有限公司日喀则山东路证券营业部",
    ],
    note: "东方财富证券在西藏的营业部合称拉萨天团；全称取自近一年龙虎榜里出现过的西藏地区营业部",
    builtin: true,
  },
  {
    id: "quant-priority",
    name: "重点观察席位/量化",
    category: "量化打板",
    styleTag: "量化扫板 / 助涨助跌高频",
    branches: [
      "开源证券股份有限公司西安西大街证券营业部",
      "国新证券股份有限公司北京分公司",
      "国泰海通证券股份有限公司湛江万豪世家证券营业部",
      "国泰海通证券股份有限公司武汉紫阳东路证券营业部",
      "国泰海通证券股份有限公司上海静安区新闸路证券营业部",
      "华鑫证券有限责任公司上海分公司",
    ],
    note: "武汉紫阳东路、上海新闸路同时出现在「消闲派」，匹配时排在前面的「消闲派」优先",
    builtin: true,
  },
  {
    id: "foreign-capital",
    name: "外资席位（高盛/瑞银/摩根）",
    category: "外资",
    styleTag: "QFII / 北向陆股通高频套利",
    branches: [
      "高盛(中国)证券有限责任公司上海浦东新区世纪大道证券营业部",
      "瑞银证券有限责任公司上海花园石桥路证券营业部",
      "摩根大通证券(中国)有限公司上海银城中路证券营业部",
      "深股通专用",
      "沪股通专用",
    ],
    builtin: true,
  },
  {
    id: "institution-seat",
    name: "机构专用",
    category: "机构专用",
    styleTag: "公募/私募/险资机构席位",
    branches: ["机构专用"],
    builtin: true,
  },
]

/**
 * 近一年（2025-09 ~ 2026-11）龙虎榜里没出现过、没能查到全称的席位，仍按简称保留，
 * 席位真上榜时按简称模糊匹配；查到全称后再替换。
 */
export const UNVERIFIED_SEAT_BRANCHES: readonly string[] = [
  "中信上海古北路",
  "中投南京太平南路",
  "湘财佛山祖庙路",
  "中泰深圳欢乐海岸",
  "首创证券福州八一七路",
  "首创证券宁波河清北路",
]

/**
 * 旧版内置字典里写的简称。升级本地存储时，内置席位组里属于这些简称的条目会被丢掉（换成全称），
 * 不在这里的（用户自己加的）会保留。
 */
const LEGACY_BUILTIN_BRANCHES: ReadonlySet<string> = new Set([
  "国泰海通上海江苏路",
  "国泰君安上海江苏路",
  "国泰海通上海海阳西路",
  "中信杭州延安路",
  "中信证券杭州延安路",
  "中信上海溧阳路",
  "中信证券上海溧阳路",
  "中信上海淮海中路",
  "中国银河绍兴",
  "银河证券绍兴",
  "浙商绍兴分公司",
  "中国银河北京阜成路",
  "华鑫上海宛平南路",
  "华鑫上海茅台路",
  "华鑫上海红宝石路",
  "华鑫证券上海红宝石路",
  "中信西安朱雀大街",
  "中信证券西安朱雀大街",
  "兴业陕西分公司",
  "广发西安南广济街",
  "国泰海通南京太平南路",
  "国泰君安南京太平南路",
  "国泰海通南京金融城",
  "南京证券南京大钟亭",
  "东方上海浦东源深路",
  "东方证券上海浦东新区源深路",
  "光大佛山绿景路",
  "光大证券佛山绿景路",
  "光大佛山季华六路",
  "中信深圳分公司",
  "华泰深圳分公司",
  "华泰证券深圳益田路荣超商务中心",
  "中信北京呼家楼",
  "中信证券北京呼家楼",
  "中信北京总部",
  "中信建投北京东城分公司",
  "广发上海浦东东方路",
  "国泰海通北京光华路",
  "国泰海通北京国贸",
  "国泰海通北京团结湖",
  "国泰海通上海松江中山东路",
  "国泰君安上海松江中山东路",
  "国盛宁波桑田路",
  "国盛证券宁波桑田路",
  "中国银河北京学院南路",
  "中国银河哈尔滨中山路",
  "中国银河大连黄河路",
  "长城证券绵阳飞云大道",
  "长城绵阳飞云大道",
  "东吴证券苏州相城大道",
  "甬兴证券宁波和源路",
  "财通证券普陀山",
  "国泰海通宜昌沿江大道",
  "国泰君安宜昌沿江大道",
  "国泰海通武汉紫阳东路",
  "国泰海通上海新闸路",
  "财通证券杭州上塘路",
  "财通杭州上塘路",
  "联储证券浙江分公司",
  "联储证券杭州环城北路",
  "长江证券深圳红荔路",
  "东方财富证券拉萨金融城南环路",
  "东方财富证券拉萨团结路第一",
  "东方财富证券拉萨东环路第二",
  "东方财富证券拉萨团结路第二",
  "东方财富证券拉萨东环路第一",
  "东方财富证券拉萨东城区江苏大道",
  "东方财富证券山南香曲东路",
  "拉萨金融城南环路",
  "拉萨团结路",
  "拉萨东环路",
  "开源证券西安西大街",
  "国新证券北京分公司",
  "国泰海通湛江万豪世家",
  "国泰海通上海静安区新闸路",
  "华鑫证券上海分公司",
  "高盛证券上海浦东新区世纪大道",
  "高盛席位",
  "瑞银证券上海花园石桥路",
  "瑞银席位",
  "摩根大通证券上海银城中路",
  "摩根大通席位",
])

/**
 * 升级旧版本地存储里的内置席位组：
 * - 内置组：营业部 = 新字典的全称 + 用户自己加的（不属于旧版内置简称的）条目；名称/风格/关注状态沿用本地的
 * - 自定义组：原样保留
 * - 新字典里有、本地没有的内置组：补上
 */
export function upgradeBuiltinSeatGroups(
  persisted: HotMoneySeatGroup[],
  defaults: HotMoneySeatGroup[] = DEFAULT_HOT_MONEY_SEATS,
): HotMoneySeatGroup[] {
  const defaultById = new Map(defaults.map((g) => [g.id, g]))
  const seen = new Set<string>()
  const out: HotMoneySeatGroup[] = []
  for (const group of persisted) {
    const base = defaultById.get(group.id)
    if (group.builtin && base) {
      seen.add(group.id)
      const userAdded = group.branches.filter(
        (b) => !LEGACY_BUILTIN_BRANCHES.has(b) && !base.branches.includes(b),
      )
      out.push({ ...group, branches: [...base.branches, ...userAdded] })
    } else {
      out.push(group)
    }
  }
  for (const base of defaults) {
    if (!seen.has(base.id) && !out.some((g) => g.id === base.id)) {
      out.push(structuredClone(base))
    }
  }
  return out
}

/**
 * 席位名称归一：全角括号等统一成半角，去掉公司类型和「证券营业部 / 营业部 / 证券」，
 * 国泰君安并入国泰海通后视为同一家。不改地名，所以「上海长宁区江苏路」和「上海江苏路」不会混。
 */
function normalizeSeatText(text: string): string {
  return text
    .normalize("NFKC")
    .replace(/股份有限公司|有限责任公司|有限公司|证券营业部|营业部|证券/g, "")
    // 先匹配较长的「国泰君安/国泰海通」，否则「国泰海通」里的「海通」会被再替换成「国泰国泰海通」
    .replace(/国泰君安|国泰海通|海通/g, "国泰海通")
    .replace(/\s+/g, "")
    .trim()
}

/** 带公司类型（股份有限公司 / 有限责任公司 / 有限公司）的才算全称。 */
function isFullSeatName(text: string): boolean {
  return /(股份有限公司|有限责任公司|有限公司)/.test(text)
}

/**
 * 根据营业部全称或简称自动匹配所属游资/派系标签。
 * 字典里写的是全称时，归一化后必须与上榜名称完全相等才命中：
 * 「中国银河证券股份有限公司绍兴证券营业部」归一化后是「中国银河绍兴」，如果用包含关系，
 * 同城的「绍兴柯桥鉴湖路」「绍兴上虞市民大道」等别的营业部也会被算成赵老哥。
 * 写简称（用户自己添加的）时仍按包含关系模糊匹配。
 */
export function matchHotMoneyByBranch(
  branchName: string,
  groups: HotMoneySeatGroup[] = DEFAULT_HOT_MONEY_SEATS,
): SeatMatchResult | null {
  if (!branchName) {
    return null
  }
  const rawClean = normalizeSeatText(branchName)
  for (const group of groups) {
    for (const pattern of group.branches) {
      const patternClean = normalizeSeatText(pattern)
      if (!patternClean) continue
      const hit = isFullSeatName(pattern)
        ? rawClean === patternClean
        : rawClean.includes(patternClean) ||
          // 反向包含要求上榜名称归一化后至少 3 个字，否则「中信证券股份有限公司」「上海证券有限责任公司」
          // 这种只有公司名的记录会被「中信上海古北路」「上海…」之类的简称吞掉
          (rawClean.length >= 3 && patternClean.includes(rawClean)) ||
          branchName.includes(pattern)
      if (hit) {
        return {
          groupId: group.id,
          name: group.name,
          category: group.category,
          styleTag: group.styleTag,
          matchedBranch: pattern,
        }
      }
    }
  }
  return null
}
