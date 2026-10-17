/**
 * 市场脉搏：板块轮动（RRG）、涨停情绪池、机构调研。
 * 轮动坐标、封板率、连板梯队、调研聚合都由后端算好，这里只做收窄。
 */
import { getData, parseSection, type Section, type SourceRef } from "./industryMarket"
import { asList, asNumber, asString, listOf } from "./json"

export interface RotationPoint {
  date: string
  ratio: number
  momentum: number
}

export interface RotationSeries {
  code: string
  name: string
  quadrant: string
  changePct: number | null
  tail: RotationPoint[]
}

export interface RotationView {
  level: string
  section: Section
  benchmark: string
  window: number
  step: number
  series: RotationSeries[]
  rules: string[]
  sources: SourceRef[]
}

export interface PoolStock {
  code: string
  exchange: string
  name: string
  price: number | null
  changePct: number | null
  amount: number | null
  floatCap: number | null
  turnoverPct: number | null
  streak: number | null
  firstSeal: string
  lastSeal: string
  sealFund: number | null
  breaks: number | null
  industry: string
  limitDays: number | null
  limitTimes: number | null
}

export interface CountItem {
  label: string
  count: number
}

export interface LimitUpView {
  section: Section
  tradeDate: string
  limitUp: number
  broken: number | null
  limitDown: number | null
  sealRate: number | null
  maxStreak: number
  ladder: { streak: number; stocks: PoolStock[] }[]
  industries: CountItem[]
  sealTimes: CountItem[]
  limitUpStocks: PoolStock[]
  brokenStocks: PoolStock[]
  limitDownStocks: PoolStock[]
  rules: string[]
  sources: SourceRef[]
}

export interface SurveyStock {
  code: string
  name: string
  events: number
  institutions: number
  latest: string
  industry: string
  ways: string[]
}

export interface SurveyEvent {
  code: string
  name: string
  noticeDate: string
  receiveDate: string
  institutions: number
  receiveWay: string
}

export interface SurveyView {
  section: Section
  days: number
  since: string
  events: number
  stocks: number
  institutions: number
  daily: CountItem[]
  ways: CountItem[]
  industries: CountItem[]
  industrySection: Section
  topStocks: SurveyStock[]
  recent: SurveyEvent[]
  rules: string[]
  sources: SourceRef[]
}

export const QUADRANTS = ["领先", "转弱", "落后", "改善"] as const

export async function fetchRotation(level: "1" | "2"): Promise<RotationView> {
  return parseRotation(await getData(`/api/v1/industry-market/rotation?level=${level}`))
}

export async function fetchLimitUp(date = ""): Promise<LimitUpView> {
  const suffix = date ? `?date=${encodeURIComponent(date)}` : ""
  return parseLimitUp(await getData(`/api/v1/industry-market/limit-up${suffix}`))
}

export async function fetchSurveys(days: number): Promise<SurveyView> {
  return parseSurveys(await getData(`/api/v1/industry-market/org-surveys?days=${days}`))
}

export function parseRotation(data: Record<string, unknown>): RotationView {
  return {
    level: asString(data.level) ?? "",
    section: parseSection(data.section),
    benchmark: asString(data.benchmark) ?? "沪深300",
    window: asNumber(data.window) ?? 0,
    step: asNumber(data.step) ?? 0,
    series: listOf(data.series).flatMap((row) => {
      const code = asString(row.code)
      const name = asString(row.name)
      const tail = listOf(row.tail).flatMap((point) => {
        const date = asString(point.trade_date)
        const ratio = asNumber(point.rs_ratio)
        const momentum = asNumber(point.rs_momentum)
        return date && ratio !== null && momentum !== null ? [{ date, ratio, momentum }] : []
      })
      return code && name && tail.length
        ? [{ code, name, quadrant: asString(row.quadrant) ?? "", changePct: asNumber(row.change_pct), tail }]
        : []
    }),
    rules: strings(data.rules),
    sources: sources(data.sources),
  }
}

export function parseLimitUp(data: Record<string, unknown>): LimitUpView {
  return {
    section: parseSection(data.section),
    tradeDate: asString(data.trade_date) ?? "",
    limitUp: asNumber(data.limit_up) ?? 0,
    broken: asNumber(data.broken),
    limitDown: asNumber(data.limit_down),
    sealRate: asNumber(data.seal_rate),
    maxStreak: asNumber(data.max_streak) ?? 0,
    ladder: listOf(data.ladder).flatMap((row) => {
      const streak = asNumber(row.streak)
      return streak === null ? [] : [{ streak, stocks: listOf(row.stocks).flatMap(parsePoolStock) }]
    }),
    industries: counts(data.industries),
    sealTimes: counts(data.seal_times),
    limitUpStocks: listOf(data.limit_up_stocks).flatMap(parsePoolStock),
    brokenStocks: listOf(data.broken_stocks).flatMap(parsePoolStock),
    limitDownStocks: listOf(data.limit_down_stocks).flatMap(parsePoolStock),
    rules: strings(data.rules),
    sources: sources(data.sources),
  }
}

export function parseSurveys(data: Record<string, unknown>): SurveyView {
  return {
    section: parseSection(data.section),
    days: asNumber(data.days) ?? 0,
    since: asString(data.since) ?? "",
    events: asNumber(data.events) ?? 0,
    stocks: asNumber(data.stocks) ?? 0,
    institutions: asNumber(data.institutions) ?? 0,
    daily: counts(data.daily),
    ways: counts(data.ways),
    industries: counts(data.industries),
    industrySection: parseSection(data.industry_section),
    topStocks: listOf(data.top_stocks).flatMap((row) => {
      const code = asString(row.code)
      const name = asString(row.name)
      return code && name
        ? [
            {
              code,
              name,
              events: asNumber(row.events) ?? 0,
              institutions: asNumber(row.institutions) ?? 0,
              latest: asString(row.latest) ?? "",
              industry: asString(row.industry) ?? "",
              ways: strings(row.ways),
            },
          ]
        : []
    }),
    recent: listOf(data.recent).flatMap((row) => {
      const code = asString(row.code)
      const name = asString(row.name)
      const noticeDate = asString(row.notice_date)
      return code && name && noticeDate
        ? [
            {
              code,
              name,
              noticeDate,
              receiveDate: asString(row.receive_date) ?? "",
              institutions: asNumber(row.institutions) ?? 0,
              receiveWay: asString(row.receive_way) ?? "",
            },
          ]
        : []
    }),
    rules: strings(data.rules),
    sources: sources(data.sources),
  }
}

function parsePoolStock(row: Record<string, unknown>): PoolStock[] {
  const code = asString(row.code)
  const name = asString(row.name)
  if (!code || !name) {
    return []
  }
  return [
    {
      code,
      exchange: asString(row.exchange) ?? "",
      name,
      price: asNumber(row.price),
      changePct: asNumber(row.change_pct),
      amount: asNumber(row.amount),
      floatCap: asNumber(row.float_cap),
      turnoverPct: asNumber(row.turnover_pct),
      streak: asNumber(row.streak),
      firstSeal: asString(row.first_seal) ?? "",
      lastSeal: asString(row.last_seal) ?? "",
      sealFund: asNumber(row.seal_fund),
      breaks: asNumber(row.breaks),
      industry: asString(row.industry) ?? "",
      limitDays: asNumber(row.limit_days),
      limitTimes: asNumber(row.limit_times),
    },
  ]
}

function counts(value: unknown): CountItem[] {
  return listOf(value).flatMap((row) => {
    const label = asString(row.label)
    return label ? [{ label, count: asNumber(row.count) ?? 0 }] : []
  })
}

function strings(value: unknown): string[] {
  return asList(value).flatMap((item) => (typeof item === "string" ? [item] : []))
}

function sources(value: unknown): SourceRef[] {
  return listOf(value).flatMap((row) => {
    const name = asString(row.name)
    return name ? [{ name, url: asString(row.url) ?? "", fetchedAt: asString(row.fetched_at) ?? "" }] : []
  })
}
