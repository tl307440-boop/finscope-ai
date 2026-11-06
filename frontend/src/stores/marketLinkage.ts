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

/** 右侧面板现在显示的是个股，还是全球指数 / 大宗商品。点哪个就是哪个，互相覆盖。 */
export type DetailFocus = "stock" | "index"

export const useMarketLinkageStore = defineStore("marketLinkage", () => {
  /** 空串 = 尚未选中；由各视图在拿到真实数据后选一只（例如最高板龙头）。 */
  const selectedStockCode = ref<string>("")
  /**
   * 全球核心指数 / 商品的 symbol（如 000001.SH、NDX.US、COMEX黄金），原样保存、不做 6 位截断。
   * 和个股代码分开存：000001.SH（上证指数）与 000001（平安银行）不会混。
   */
  const selectedIndexCode = ref<string>("")
  const detailFocus = ref<DetailFocus>("stock")
  /** 当前选中的指数是不是「默认选中」（用户没点过）；离开行情总览页时默认的会撤掉，用户点的会保留。 */
  const indexIsDefault = ref<boolean>(false)
  /** 右侧详情面板是否展开（收起按钮在 MarketSplitLayout 的工具栏）；点指数会把它展开。 */
  const detailOpen = ref<boolean>(true)
  const activeSeatIdFilter = ref<string>("")
  const seatManagerVisible = ref<boolean>(false)
  const seatGroups = ref<HotMoneySeatGroup[]>(loadPersistedSeats())

  const watchedSeatGroups = computed(() => seatGroups.value.filter((group) => isWatched(group)))

  /** 右侧正在显示的个股代码；当前显示的是指数时为空串（列表里的「选中」高亮跟着它走）。 */
  const activeStockCode = computed(() => (detailFocus.value === "stock" ? selectedStockCode.value : ""))
  /** 右侧正在显示的指数 symbol；当前显示的是个股时为空串。 */
  const activeIndexCode = computed(() => (detailFocus.value === "index" ? selectedIndexCode.value : ""))

  /** 列表里这一行的个股是不是右侧正在显示的那只（接口里的代码可能带 .SZ / .SH 后缀，这里统一成 6 位再比）。 */
  function isStockActive(code: string): boolean {
    const active = activeStockCode.value
    return active !== "" && toPlainCode(code) === active
  }

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
    detailFocus.value = "stock"
  }

  /** 点全球指数 / 商品卡片：右侧改显示它的分时 + 日K；面板被收起时顺手展开。 */
  function selectIndex(code: string): void {
    const symbol = code.trim()
    if (!symbol) return
    selectedIndexCode.value = symbol
    detailFocus.value = "index"
    indexIsDefault.value = false
    detailOpen.value = true
  }

  /**
   * 行情总览页的默认选中：用户还没点过个股 / 指数时先选一个指数，右侧不空着。
   * 不会展开已收起的面板，也不会覆盖已有的选择。
   */
  function ensureDefaultIndex(code: string): void {
    if (selectedStockCode.value || selectedIndexCode.value) return
    const symbol = code.trim()
    if (!symbol) return
    selectedIndexCode.value = symbol
    detailFocus.value = "index"
    indexIsDefault.value = true
  }

  /**
   * 离开行情总览页：撤掉「默认选中」的指数（龙虎榜、连板、热点消息页不该凭空出现一个指数）。
   * 用户自己点过的指数不动，右侧继续显示他最近点的那个。
   */
  function releaseDefaultIndex(): void {
    if (!indexIsDefault.value) return
    indexIsDefault.value = false
    selectedIndexCode.value = ""
    if (detailFocus.value === "index") detailFocus.value = "stock"
  }

  function setDetailOpen(open: boolean): void {
    detailOpen.value = open
  }

  function toggleDetailOpen(): void {
    detailOpen.value = !detailOpen.value
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
    selectedIndexCode,
    detailFocus,
    detailOpen,
    activeStockCode,
    activeIndexCode,
    isStockActive,
    activeSeatIdFilter,
    seatManagerVisible,
    seatGroups,
    watchedSeatGroups,
    selectStock,
    selectIndex,
    ensureDefaultIndex,
    releaseDefaultIndex,
    setDetailOpen,
    toggleDetailOpen,
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
