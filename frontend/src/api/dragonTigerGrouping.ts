/**
 * 龙虎榜席位聚合（纯函数，便于单测）。
 *
 * 后端只给「个股 × 营业部」的原始明细；哪些营业部属于哪位游资由前端席位字典决定，
 * 因为字典可由用户在席位管理里增删并持久化在浏览器。
 */

import type { DragonTigerData, LhbSeat } from "./aSharePanorama"
import {
  isWatched,
  matchHotMoneyByBranch,
  type HotMoneySeatGroup,
  type SeatCategory,
} from "../config/hotMoneySeatsConfig"

export interface SeatStockAction {
  code: string
  name: string
  changePct: number | null
  buyWan: number
  sellWan: number
  netWan: number
  reason: string
  /** 命中该席位组的营业部（同一只票可能多个营业部同时出现） */
  branches: string[]
}

export interface SeatGroupRow {
  groupId: string
  name: string
  category: SeatCategory
  styleTag: string
  buyWan: number
  sellWan: number
  netWan: number
  stocks: SeatStockAction[]
}

export interface SeatSummaryBucket {
  key: "foreign" | "institution" | "lasa"
  label: string
  buyWan: number
  sellWan: number
  netWan: number
  seatCount: number
  stockCount: number
}

export interface GroupedDragonTiger {
  /** 关注名单里今天出现在龙虎榜上的席位组，按净买入从大到小。 */
  rows: SeatGroupRow[]
  /** 外资 / 机构 / 拉萨天团 汇总（不受关注名单限制，覆盖全部上榜席位）。 */
  summary: SeatSummaryBucket[]
  /** 能匹配到字典的席位数 / 全部席位数，用来提示字典覆盖率。 */
  matchedSeatCount: number
  totalSeatCount: number
}

const LASA_GROUP_ID = "lasa-tiantuan"

function dedupe(seats: LhbSeat[]): LhbSeat[] {
  // 同一营业部同时在买入榜和卖出榜出现时，交易所给两条买卖金额完全相同的记录，只取一条避免重复累计。
  // 「机构专用」在同一只票上会有多个互不相同的席位，所以键里必须带上金额，不能只按名称去重。
  const seen = new Set<string>()
  const out: LhbSeat[] = []
  for (const seat of seats) {
    const key = `${seat.code}|${seat.branchName}|${seat.buyWan}|${seat.sellWan}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(seat)
  }
  return out
}

export function groupDragonTiger(
  data: DragonTigerData,
  groups: HotMoneySeatGroup[],
): GroupedDragonTiger {
  const seats = dedupe(data.seats)
  const groupById = new Map(groups.map((g) => [g.id, g]))
  const rowMap = new Map<string, SeatGroupRow>()
  const buckets: Record<SeatSummaryBucket["key"], SeatSummaryBucket> = {
    foreign: { key: "foreign", label: "外资", buyWan: 0, sellWan: 0, netWan: 0, seatCount: 0, stockCount: 0 },
    institution: { key: "institution", label: "机构专用", buyWan: 0, sellWan: 0, netWan: 0, seatCount: 0, stockCount: 0 },
    lasa: { key: "lasa", label: "拉萨天团", buyWan: 0, sellWan: 0, netWan: 0, seatCount: 0, stockCount: 0 },
  }
  const bucketStocks: Record<SeatSummaryBucket["key"], Set<string>> = {
    foreign: new Set(),
    institution: new Set(),
    lasa: new Set(),
  }
  let matched = 0

  for (const seat of seats) {
    const hit = matchHotMoneyByBranch(seat.branchName, groups)
    if (!hit) continue
    matched += 1
    const group = groupById.get(hit.groupId)
    if (!group) continue

    const bucketKey: SeatSummaryBucket["key"] | null =
      group.category === "外资"
        ? "foreign"
        : group.category === "机构专用"
          ? "institution"
          : group.id === LASA_GROUP_ID
            ? "lasa"
            : null
    if (bucketKey) {
      const b = buckets[bucketKey]
      b.buyWan += seat.buyWan
      b.sellWan += seat.sellWan
      b.netWan += seat.netWan
      b.seatCount += 1
      bucketStocks[bucketKey].add(seat.code)
    }

    if (!isWatched(group)) continue
    let row = rowMap.get(group.id)
    if (!row) {
      row = {
        groupId: group.id,
        name: group.name,
        category: group.category,
        styleTag: group.styleTag,
        buyWan: 0,
        sellWan: 0,
        netWan: 0,
        stocks: [],
      }
      rowMap.set(group.id, row)
    }
    let action = row.stocks.find((s) => s.code === seat.code)
    if (!action) {
      action = {
        code: seat.code,
        name: seat.name,
        changePct: seat.changePct,
        buyWan: 0,
        sellWan: 0,
        netWan: 0,
        reason: seat.reason,
        branches: [],
      }
      row.stocks.push(action)
    }
    action.buyWan += seat.buyWan
    action.sellWan += seat.sellWan
    action.netWan += seat.netWan
    action.branches.push(seat.branchName)
    row.buyWan += seat.buyWan
    row.sellWan += seat.sellWan
    row.netWan += seat.netWan
  }

  for (const key of Object.keys(buckets) as SeatSummaryBucket["key"][]) {
    buckets[key].stockCount = bucketStocks[key].size
  }

  const rows = [...rowMap.values()]
  for (const row of rows) row.stocks.sort((a, b) => b.netWan - a.netWan)
  rows.sort((a, b) => b.netWan - a.netWan)

  return {
    rows,
    summary: [buckets.foreign, buckets.institution, buckets.lasa],
    matchedSeatCount: matched,
    totalSeatCount: seats.length,
  }
}
