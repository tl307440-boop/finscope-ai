/**
 * 中国宏观 · 人民银行统计表的月度手工导入提醒。
 *
 * 这三项来自中国人民银行调查统计司的统计表。对方 robots.txt 不允许抓取，
 * 所以系统不做自动同步，只提醒管理员在发布后导入 CSV。
 * 人民银行一般在每月 12–15 日前后发布上月数据（2025-02 至 2026-09 的实际发布日在 12–15 日之间）。
 */

export interface PbocSeries {
  code: string
  label: string
  table: string
}

export const PBOC_SERIES: readonly PbocSeries[] = [
  { code: "SOCIAL_FINANCING_STOCK_CN", label: "社融存量同比增速", table: "社会融资规模存量统计表" },
  { code: "GOV_BOND_NET_CN", label: "政府债券净融资", table: "社会融资规模增量统计表" },
  { code: "ENT_MLT_LOAN_CN", label: "企业中长期贷款增量", table: "金融机构人民币信贷收支表（企事业单位中长期贷款余额逐月差分）" },
]

export interface PbocLink {
  label: string
  url: string
}

export const PBOC_LINKS: readonly PbocLink[] = [
  {
    label: "数据解读：月度金融统计数据报告（含发布日期）",
    url: "https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html",
  },
  {
    label: "统计数据：按年份进入「社会融资规模」「金融机构信贷收支统计」",
    url: "https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html",
  },
]

export const CSV_COLUMNS = "series_code,observation_date,value,unit,source,published_at"

export type ImportState = "missing" | "due" | "current"

export interface ImportStatus {
  state: ImportState
  /** 当前应该已经有的最新观测月（YYYY-MM） */
  expected: string
  /** 库里最新观测月（YYYY-MM），没有数据为 null */
  latest: string | null
  text: string
}

function shanghaiParts(now: Date): { year: number; month: number; day: number } {
  const shifted = new Date(now.getTime() + 8 * 3600 * 1000)
  return { year: shifted.getUTCFullYear(), month: shifted.getUTCMonth() + 1, day: shifted.getUTCDate() }
}

function monthText(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}`
}

/**
 * 按「每月 15 日前发布上月数据」推算该有的最新观测月：
 * 16 日及以后应有上月数据；15 日及以前上月数据可能还没发布，应有上上月。
 */
export function expectedMonth(now: Date): string {
  const { year, month, day } = shanghaiParts(now)
  const back = day >= 16 ? 1 : 2
  const index = year * 12 + (month - 1) - back
  return monthText(Math.floor(index / 12), (index % 12) + 1)
}

/** latestDate 是接口返回的观测日（YYYY-MM-DD）；格式不对按没有数据处理，不猜测。 */
export function importStatus(latestDate: string | null | undefined, now: Date): ImportStatus {
  const expected = expectedMonth(now)
  const matched = typeof latestDate === "string" ? /^(\d{4})-(\d{2})-\d{2}/.exec(latestDate) : null
  if (!matched) {
    return { state: "missing", expected, latest: null, text: "暂无数据，待导入" }
  }
  const latest = `${matched[1]}-${matched[2]}`
  if (latest < expected) {
    return { state: "due", expected, latest, text: `待导入：最新 ${latest}，应有 ${expected}` }
  }
  return { state: "current", expected, latest, text: `已是最新（${latest}）` }
}
