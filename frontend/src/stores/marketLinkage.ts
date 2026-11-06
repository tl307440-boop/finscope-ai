import { computed, ref } from "vue"
import { defineStore } from "pinia"

import {
  DEFAULT_HOT_MONEY_SEATS,
  isWatched,
  matchHotMoneyByBranch,
  normalizeSeatGroup,
  type HotMoneySeatGroup,
  type SeatCategory,
  type SeatMatchResult,
} from "../config/hotMoneySeatsConfig"

/** v2：席位模型统一为 category/styleTag/watched；旧 v1 数据不再读取。 */
const STORAGE_KEY = "finscope.hotMoneySeats.v2"

function loadPersistedSeats(): HotMoneySeatGroup[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        const groups = parsed.flatMap((item) => {
          const g = normalizeSeatGroup(item)
          return g ? [g] : []
        })
        if (groups.length > 0) return groups
      }
    }
  } catch {
    // 存储不可用或内容损坏时退回内置字典
  }
  return structuredClone(DEFAULT_HOT_MONEY_SEATS)
}

/** 统一成 6 位代码，面板联动与后端取数都用它。 */
function toPlainCode(code: string): string {
  return code.trim().toUpperCase().replace(/\.(SH|SZ|BJ)$/, "")
}

export const useMarketLinkageStore = defineStore("marketLinkage", () => {
  /** 空串 = 尚未选中；由各视图在拿到真实数据后选一只（例如最高板龙头）。 */
  const selectedStockCode = ref<string>("")
  const activeSeatIdFilter = ref<string>("")
  const seatManagerVisible = ref<boolean>(false)
  const seatGroups = ref<HotMoneySeatGroup[]>(loadPersistedSeats())

  const watchedSeatGroups = computed(() => seatGroups.value.filter((group) => isWatched(group)))

  function persistSeats(): void {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(seatGroups.value))
    } catch {
      // 存储配额不足时忽略，本次会话内仍然生效
    }
  }

  function selectStock(code: string): void {
    const plain = toPlainCode(code)
    if (!plain) return
    selectedStockCode.value = plain
  }

  function setSeatFilter(seatId: string): void {
    activeSeatIdFilter.value = activeSeatIdFilter.value === seatId ? "" : seatId
  }

  /** 从个股详情等处跳转到龙虎榜时，直接定位到某个席位组（不做取消切换）。 */
  function focusSeat(seatId: string): void {
    activeSeatIdFilter.value = seatId
  }

  function openSeatManager(): void {
    seatManagerVisible.value = true
  }

  function closeSeatManager(): void {
    seatManagerVisible.value = false
  }

  function toggleSeatWatched(id: string): void {
    const target = seatGroups.value.find((g) => g.id === id)
    if (target) {
      target.watched = !isWatched(target)
      persistSeats()
    }
  }

  function addOrUpdateSeatGroup(payload: {
    id?: string
    name: string
    category: SeatCategory
    styleTag: string
    branches: string[]
    watched?: boolean
  }): void {
    const cleanBranches = payload.branches.map((b) => b.trim()).filter(Boolean)
    const name = payload.name.trim()
    if (!name || cleanBranches.length === 0) return

    const existing = payload.id
      ? seatGroups.value.find((g) => g.id === payload.id)
      : seatGroups.value.find((g) => g.name === name)

    if (existing) {
      existing.name = name
      existing.category = payload.category
      existing.styleTag = payload.styleTag.trim() || existing.styleTag
      existing.branches = cleanBranches
      if (payload.watched !== undefined) existing.watched = payload.watched
    } else {
      seatGroups.value.unshift({
        id: payload.id || `custom_${Date.now()}`,
        name,
        category: payload.category,
        styleTag: payload.styleTag.trim() || "自定义跟踪席位",
        branches: cleanBranches,
        watched: payload.watched ?? true,
      })
    }
    persistSeats()
  }

  function addBranchToSeat(seatId: string, branchKeyword: string): void {
    const clean = branchKeyword.trim()
    if (!clean) return
    const target = seatGroups.value.find((g) => g.id === seatId)
    if (target && !target.branches.includes(clean)) {
      target.branches.push(clean)
      persistSeats()
    }
  }

  function removeBranchFromSeat(seatId: string, branchKeyword: string): void {
    const target = seatGroups.value.find((g) => g.id === seatId)
    if (target) {
      target.branches = target.branches.filter((b) => b !== branchKeyword)
      persistSeats()
    }
  }

  function deleteSeatGroup(seatId: string): void {
    seatGroups.value = seatGroups.value.filter((g) => g.id !== seatId)
    persistSeats()
  }

  function resetSeatsToDefault(): void {
    seatGroups.value = structuredClone(DEFAULT_HOT_MONEY_SEATS)
    persistSeats()
  }

  function matchBranchTag(branchName: string): SeatMatchResult | null {
    return matchHotMoneyByBranch(branchName, seatGroups.value)
  }

  return {
    selectedStockCode,
    activeSeatIdFilter,
    seatManagerVisible,
    seatGroups,
    watchedSeatGroups,
    selectStock,
    setSeatFilter,
    focusSeat,
    openSeatManager,
    closeSeatManager,
    toggleSeatWatched,
    addOrUpdateSeatGroup,
    addBranchToSeat,
    removeBranchFromSeat,
    deleteSeatGroup,
    resetSeatsToDefault,
    matchBranchTag,
  }
})
