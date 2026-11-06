/**
 * A 股全景看板数据层。
 *
 * 全部数据来自后端 `/api/v1/ashare/*`，这里不保留任何本地模拟/兜底数据：
 * 后端抓不到的字段是 null，并在 `meta.unavailable` 里写明原因，界面应显示「—」或「暂无数据」。
 */

import { FetchFailure, getData } from "./industryMarket"
import { asList, asNumber, asRecord, asString } from "./json"

// ───────────── 通用 ─────────────

export type PanoramaSession = "intraday" | "close" | "unknown"

export interface PanoramaMeta {
  tradeDate: string | null
  session: PanoramaSession
  fetchedAt: string | null
  stale: boolean
  sources: string[]
  notes: string[]
  unavailable: Record<string, string>
}

type Rec = Record<string, unknown>

function str(value: unknown): string {
  return asString(value) ?? ""
}

function strList(value: unknown): string[] {
  return asList(value).flatMap((v) => {
    const s = asString(v)
    return s ? [s] : []
  })
}

function numList(value: unknown): number[] {
  return asList(value).flatMap((v) => {
    const n = asNumber(v)
    return n === null ? [] : [n]
  })
}

function recList<T>(value: unknown, parse: (r: Rec) => T | null): T[] {
  return asList(value).flatMap((v) => {
    const r = asRecord(v)
    const out = r ? parse(r) : null
    return out === null ? [] : [out]
  })
}

function parseMeta(value: unknown): PanoramaMeta {
  const r = asRecord(value) ?? {}
  const rawSession = asString(r.session)
  const unavailable: Record<string, string> = {}
  for (const [k, v] of Object.entries(asRecord(r.unavailable) ?? {})) {
    const reason = asString(v)
    if (reason) unavailable[k] = reason
  }
  return {
    tradeDate: asString(r.tradeDate),
    session: rawSession === "intraday" || rawSession === "close" ? rawSession : "unknown",
    fetchedAt: asString(r.fetchedAt),
    stale: r.stale === true,
    sources: strList(r.sources),
    notes: strList(r.notes),
    unavailable,
  }
}

async function load<T>(path: string, parse: (data: Rec) => T): Promise<T> {
  const data = await getData(path)
  return parse(data)
}

export function errorMessage(error: unknown): string {
  if (error instanceof FetchFailure) return error.message
  return error instanceof Error ? error.message : "加载失败，请稍后重试"
}

// ───────────── 全球核心指数 ─────────────

export interface GlobalQuoteItem {
  code: string
  symbol: string
  name: string
  category: "A股宽基" | "海外指数" | "大宗商品"
  price: number | null
  change: number | null
  changePct: number | null
  amountYuan: number | null
  spark: number[]
}

export interface GlobalQuotesData {
  quotes: GlobalQuoteItem[]
  meta: PanoramaMeta
}

function parseCategory(value: unknown): GlobalQuoteItem["category"] {
  return value === "海外指数" || value === "大宗商品" ? value : "A股宽基"
}

export function fetchGlobalQuotes(): Promise<GlobalQuotesData> {
  return load("/api/v1/ashare/global-quotes", (d) => ({
    quotes: recList(d.quotes, (r) => ({
      code: str(r.code),
      symbol: str(r.symbol),
      name: str(r.name),
      category: parseCategory(r.category),
      price: asNumber(r.price),
      change: asNumber(r.change),
      changePct: asNumber(r.changePct),
      amountYuan: asNumber(r.amountYuan),
      spark: numList(r.spark),
    })),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 短线情绪 ─────────────

export interface SwingStock {
  code: string
  name: string
  price: number | null
  changePct: number
  swingPct: number
  peakOrTroughPct: number
  sector: string
  reason: string
}

export interface StreakLeader {
  code: string
  name: string
  changePct: number | null
  sector: string
  reason: string
}

export interface PremiumHistory {
  dates: string[]
  limitUpPremium: number[]
  streakPremium: number[]
}

export interface SentimentData {
  tradeDate: string | null
  upCount: number | null
  downCount: number | null
  flatCount: number | null
  medianChangePct: number | null
  limitUpTotal: number | null
  limitDownTotal: number | null
  naturalLimitUp: number | null
  naturalLimitDown: number | null
  brokenCount: number | null
  brokenRatePct: number | null
  streakCount: number | null
  promotionRatePct: number | null
  maxStreakHeight: number | null
  maxStreakLeader: StreakLeader | null
  pullbackOver10Count: number | null
  reboundOver10Count: number | null
  pullbackStocks: SwingStock[]
  reboundStocks: SwingStock[]
  yesterdayLimitUpPremiumPct: number | null
  yesterdayStreakPremiumPct: number | null
  yesterdayBrokenPremiumPct: number | null
  premiumHistory: PremiumHistory
  meta: PanoramaMeta
}

function parseSwing(r: Rec): SwingStock | null {
  const changePct = asNumber(r.changePct)
  const swingPct = asNumber(r.swingPct)
  const peak = asNumber(r.peakOrTroughPct)
  if (changePct === null || swingPct === null || peak === null) return null
  return {
    code: str(r.code),
    name: str(r.name),
    price: asNumber(r.price),
    changePct,
    swingPct,
    peakOrTroughPct: peak,
    sector: str(r.sector),
    reason: str(r.reason),
  }
}

export function fetchSentiment(): Promise<SentimentData> {
  return load("/api/v1/ashare/sentiment", (d) => {
    const leader = asRecord(d.maxStreakLeader)
    const hist = asRecord(d.premiumHistory) ?? {}
    return {
      tradeDate: asString(d.tradeDate),
      upCount: asNumber(d.upCount),
      downCount: asNumber(d.downCount),
      flatCount: asNumber(d.flatCount),
      medianChangePct: asNumber(d.medianChangePct),
      limitUpTotal: asNumber(d.limitUpTotal),
      limitDownTotal: asNumber(d.limitDownTotal),
      naturalLimitUp: asNumber(d.naturalLimitUp),
      naturalLimitDown: asNumber(d.naturalLimitDown),
      brokenCount: asNumber(d.brokenCount),
      brokenRatePct: asNumber(d.brokenRatePct),
      streakCount: asNumber(d.streakCount),
      promotionRatePct: asNumber(d.promotionRatePct),
      maxStreakHeight: asNumber(d.maxStreakHeight),
      maxStreakLeader: leader
        ? {
            code: str(leader.code),
            name: str(leader.name),
            changePct: asNumber(leader.changePct),
            sector: str(leader.sector),
            reason: str(leader.reason),
          }
        : null,
      pullbackOver10Count: asNumber(d.pullbackOver10Count),
      reboundOver10Count: asNumber(d.reboundOver10Count),
      pullbackStocks: recList(d.pullbackStocks, parseSwing),
      reboundStocks: recList(d.reboundStocks, parseSwing),
      yesterdayLimitUpPremiumPct: asNumber(d.yesterdayLimitUpPremiumPct),
      yesterdayStreakPremiumPct: asNumber(d.yesterdayStreakPremiumPct),
      yesterdayBrokenPremiumPct: asNumber(d.yesterdayBrokenPremiumPct),
      premiumHistory: {
        dates: strList(hist.dates),
        limitUpPremium: numList(hist.limitUpPremium),
        streakPremium: numList(hist.streakPremium),
      },
      meta: parseMeta(d.meta),
    }
  })
}

// ───────────── 成交量能 / 两融 ─────────────

export type VolumeKey = "all" | "sh" | "sz" | "bj" | "chinext" | "star"
export type MarginKey = "total" | "sh" | "sz" | "bj"

export interface VolumeSeries {
  key: VolumeKey
  label: string
  todayAmountYi: number | null
  noonAmountYi: number | null
  noonDeltaYi: number | null
  noonDeltaPct: number | null
  historyDates: string[]
  historyAmountYi: number[]
}

export interface MarginSeries {
  key: MarginKey
  label: string
  tradeDate: string | null
  totalBalanceYi: number | null
  dailyChangeYi: number | null
  rzyeYi: number | null
  rqyeYi: number | null
  rzmreYi: number | null
  historyDates: string[]
  historyTotalYi: number[]
  historyChangeYi: number[]
}

export interface VolumeMarginData {
  volumes: VolumeSeries[]
  margins: MarginSeries[]
  meta: PanoramaMeta
}

const VOLUME_KEYS: readonly VolumeKey[] = ["all", "sh", "sz", "bj", "chinext", "star"]
const MARGIN_KEYS: readonly MarginKey[] = ["total", "sh", "sz", "bj"]

export function fetchVolumeMargin(): Promise<VolumeMarginData> {
  return load("/api/v1/ashare/volume-margin", (d) => ({
    volumes: recList(d.volumes, (r) => {
      const key = VOLUME_KEYS.find((k) => k === r.key)
      if (!key) return null
      return {
        key,
        label: str(r.label),
        todayAmountYi: asNumber(r.todayAmountYi),
        noonAmountYi: asNumber(r.noonAmountYi),
        noonDeltaYi: asNumber(r.noonDeltaYi),
        noonDeltaPct: asNumber(r.noonDeltaPct),
        historyDates: strList(r.historyDates),
        historyAmountYi: numList(r.historyAmountYi),
      }
    }),
    margins: recList(d.margins, (r) => {
      const key = MARGIN_KEYS.find((k) => k === r.key)
      if (!key) return null
      return {
        key,
        label: str(r.label),
        tradeDate: asString(r.tradeDate),
        totalBalanceYi: asNumber(r.totalBalanceYi),
        dailyChangeYi: asNumber(r.dailyChangeYi),
        rzyeYi: asNumber(r.rzyeYi),
        rqyeYi: asNumber(r.rqyeYi),
        rzmreYi: asNumber(r.rzmreYi),
        historyDates: strList(r.historyDates),
        historyTotalYi: numList(r.historyTotalYi),
        historyChangeYi: numList(r.historyChangeYi),
      }
    }),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 股指期货机构持仓 ─────────────

export type FuturesContract = "ALL" | "IF" | "IC" | "IM" | "IH"

export interface FuturesRow {
  contractType: FuturesContract
  contractName: string
  institution: string
  isKeyInstitution: boolean
  longPos: number
  longChange: number
  shortPos: number
  shortChange: number
  netPos: number
  netChange: number
}

export interface FuturesData {
  rows: FuturesRow[]
  meta: PanoramaMeta
}

const FUTURES_CONTRACTS: readonly FuturesContract[] = ["ALL", "IF", "IC", "IM", "IH"]

export function fetchFutures(): Promise<FuturesData> {
  return load("/api/v1/ashare/futures", (d) => ({
    rows: recList(d.rows, (r) => {
      const contractType = FUTURES_CONTRACTS.find((k) => k === r.contractType)
      if (!contractType) return null
      return {
        contractType,
        contractName: str(r.contractName),
        institution: str(r.institution),
        isKeyInstitution: r.isKeyInstitution === true,
        longPos: asNumber(r.longPos) ?? 0,
        longChange: asNumber(r.longChange) ?? 0,
        shortPos: asNumber(r.shortPos) ?? 0,
        shortChange: asNumber(r.shortChange) ?? 0,
        netPos: asNumber(r.netPos) ?? 0,
        netChange: asNumber(r.netChange) ?? 0,
      }
    }),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 领涨板块与人气股 ─────────────

export interface LeadingSector {
  code: string
  name: string
  changePct: number | null
  limitUpCount: number | null
  netInflowYi: number | null
  leaderCode: string
  leaderName: string
  leaderChangePct: number | null
  leaderStreak: string
}

export interface PopularStock {
  rank: number
  code: string
  name: string
  price: number | null
  changePct: number | null
  turnoverPct: number | null
  floatCapYi: number | null
  sealAmountWan: number | null
  streakText: string
  sector: string
  catalyst: string
}

export interface LeadingData {
  leadingSectors: LeadingSector[]
  popularStocks: PopularStock[]
  meta: PanoramaMeta
}

export function fetchLeading(): Promise<LeadingData> {
  return load("/api/v1/ashare/leading", (d) => ({
    leadingSectors: recList(d.leadingSectors, (r) => ({
      code: str(r.code),
      name: str(r.name),
      changePct: asNumber(r.changePct),
      limitUpCount: asNumber(r.limitUpCount),
      netInflowYi: asNumber(r.netInflowYi),
      leaderCode: str(r.leaderCode),
      leaderName: str(r.leaderName),
      leaderChangePct: asNumber(r.leaderChangePct),
      leaderStreak: str(r.leaderStreak),
    })),
    popularStocks: recList(d.popularStocks, (r) => ({
      rank: asNumber(r.rank) ?? 0,
      code: str(r.code),
      name: str(r.name),
      price: asNumber(r.price),
      changePct: asNumber(r.changePct),
      turnoverPct: asNumber(r.turnoverPct),
      floatCapYi: asNumber(r.floatCapYi),
      sealAmountWan: asNumber(r.sealAmountWan),
      streakText: str(r.streakText),
      sector: str(r.sector),
      catalyst: str(r.catalyst),
    })),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 龙虎榜 ─────────────

export interface LhbStock {
  code: string
  name: string
  closePrice: number | null
  changePct: number | null
  turnoverPct: number | null
  floatCapYi: number | null
  buyWan: number
  sellWan: number
  netWan: number
  reason: string
}

export interface LhbSeat {
  code: string
  name: string
  branchName: string
  side: "buy" | "sell"
  buyWan: number
  sellWan: number
  netWan: number
  changePct: number | null
  reason: string
}

export interface DragonTigerData {
  tradeDate: string | null
  stocks: LhbStock[]
  seats: LhbSeat[]
  meta: PanoramaMeta
}

export function fetchDragonTiger(date?: string): Promise<DragonTigerData> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ""
  return load(`/api/v1/ashare/dragon-tiger${query}`, (d) => ({
    tradeDate: asString(d.tradeDate),
    stocks: recList(d.stocks, (r) => ({
      code: str(r.code),
      name: str(r.name),
      closePrice: asNumber(r.closePrice),
      changePct: asNumber(r.changePct),
      turnoverPct: asNumber(r.turnoverPct),
      floatCapYi: asNumber(r.floatCapYi),
      buyWan: asNumber(r.buyWan) ?? 0,
      sellWan: asNumber(r.sellWan) ?? 0,
      netWan: asNumber(r.netWan) ?? 0,
      reason: str(r.reason),
    })),
    seats: recList(d.seats, (r) => ({
      code: str(r.code),
      name: str(r.name),
      branchName: str(r.branchName),
      side: r.side === "sell" ? "sell" : "buy",
      buyWan: asNumber(r.buyWan) ?? 0,
      sellWan: asNumber(r.sellWan) ?? 0,
      netWan: asNumber(r.netWan) ?? 0,
      changePct: asNumber(r.changePct),
      reason: str(r.reason),
    })),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 连板梯队 ─────────────

export interface LadderStock {
  code: string
  name: string
  streak: number
  boardLabel: string
  price: number | null
  changePct: number | null
  reason: string
  sector: string
  firstSealTime: string
  lastSealTime: string
  breakCount: number
  sealAmountWan: number | null
  turnoverPct: number | null
  floatCapYi: number | null
  /** 韭研公社归类的板块；没接入或当日未收录时为空串。 */
  actionSector: string
}

export interface LadderTier {
  streak: number
  label: string
  stocks: LadderStock[]
}

export interface BrokenStreak {
  code: string
  name: string
  yesterdayStreak: number
  price: number | null
  changePct: number | null
  drawdownFromHighPct: number | null
  turnoverPct: number | null
  sector: string
  reason: string
}

export interface LadderData {
  tradeDate: string | null
  overallPromotionRatePct: number | null
  overallBrokenRatePct: number | null
  rate1To2Pct: number | null
  count1To2Text: string
  rate2To3Pct: number | null
  count2To3Text: string
  rate3To4PlusPct: number | null
  count3To4PlusText: string
  tiers: LadderTier[]
  brokenStreaks: BrokenStreak[]
  meta: PanoramaMeta
}

function parseLadderStock(r: Rec): LadderStock {
  return {
    code: str(r.code),
    name: str(r.name),
    streak: asNumber(r.streak) ?? 1,
    boardLabel: str(r.boardLabel),
    price: asNumber(r.price),
    changePct: asNumber(r.changePct),
    reason: str(r.reason),
    sector: str(r.sector),
    firstSealTime: str(r.firstSealTime),
    lastSealTime: str(r.lastSealTime),
    breakCount: asNumber(r.breakCount) ?? 0,
    sealAmountWan: asNumber(r.sealAmountWan),
    turnoverPct: asNumber(r.turnoverPct),
    floatCapYi: asNumber(r.floatCapYi),
    actionSector: str(r.actionSector),
  }
}

export function fetchLadder(): Promise<LadderData> {
  return load("/api/v1/ashare/ladder", (d) => ({
    tradeDate: asString(d.tradeDate),
    overallPromotionRatePct: asNumber(d.overallPromotionRatePct),
    overallBrokenRatePct: asNumber(d.overallBrokenRatePct),
    rate1To2Pct: asNumber(d.rate1To2Pct),
    count1To2Text: str(d.count1To2Text),
    rate2To3Pct: asNumber(d.rate2To3Pct),
    count2To3Text: str(d.count2To3Text),
    rate3To4PlusPct: asNumber(d.rate3To4PlusPct),
    count3To4PlusText: str(d.count3To4PlusText),
    tiers: recList(d.tiers, (r) => ({
      streak: asNumber(r.streak) ?? 1,
      label: str(r.label),
      stocks: recList(r.stocks, parseLadderStock),
    })),
    brokenStreaks: recList(d.brokenStreaks, (r) => ({
      code: str(r.code),
      name: str(r.name),
      yesterdayStreak: asNumber(r.yesterdayStreak) ?? 0,
      price: asNumber(r.price),
      changePct: asNumber(r.changePct),
      drawdownFromHighPct: asNumber(r.drawdownFromHighPct),
      turnoverPct: asNumber(r.turnoverPct),
      sector: str(r.sector),
      reason: str(r.reason),
    })),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 韭研公社异动解析 ─────────────

export interface JygsStock {
  code: string
  name: string
  boardLabel: string
  price: number | null
  changePct: number | null
  firstSealTime: string
  explain: string
}

export interface JygsSector {
  name: string
  reason: string
  stocks: JygsStock[]
}

export interface JygsActionsData {
  tradeDate: string | null
  totalStocks: number
  sectors: JygsSector[]
  meta: PanoramaMeta
}

export function fetchJygsActions(date?: string): Promise<JygsActionsData> {
  const query = date ? `?date=${encodeURIComponent(date)}` : ""
  return load(`/api/v1/ashare/jygs-actions${query}`, (d) => ({
    tradeDate: asString(d.tradeDate),
    totalStocks: asNumber(d.totalStocks) ?? 0,
    sectors: recList(d.sectors, (r) => ({
      name: str(r.name),
      reason: str(r.reason),
      stocks: recList(r.stocks, (s) => ({
        code: str(s.code),
        name: str(s.name),
        boardLabel: str(s.boardLabel),
        price: asNumber(s.price),
        changePct: asNumber(s.changePct),
        firstSealTime: str(s.firstSealTime),
        explain: str(s.explain),
      })),
    })),
    meta: parseMeta(d.meta),
  }))
}

export type JygsState = "not_configured" | "ok" | "expiring" | "expired" | "unknown"

export interface JygsStatus {
  state: JygsState
  message: string
  expiresAt: string | null
  daysLeft: number | null
}

export function fetchJygsStatus(): Promise<JygsStatus> {
  return load("/api/v1/ashare/jygs-status", (d) => {
    const raw = asString(d.state)
    const state: JygsState =
      raw === "ok" || raw === "expiring" || raw === "expired" || raw === "not_configured"
        ? raw
        : "unknown"
    return {
      state,
      message: str(d.message),
      expiresAt: asString(d.expiresAt),
      daysLeft: asNumber(d.daysLeft),
    }
  })
}

// ───────────── 热点消息 ─────────────

export type NewsSource = "财联社" | "东方财富" | "同花顺" | "金十数据"
export type NewsLevel = "S" | "A" | "B"
export type NewsSentiment = "利好" | "利空" | "催化" | "中性"

export interface NewsStock {
  code: string
  name: string
  changePct: number | null
}

export interface NewsItem {
  id: string
  time: string
  date: string
  timestamp: number
  source: NewsSource
  region: "domestic" | "overseas"
  impactLevel: NewsLevel
  sentiment: NewsSentiment
  title: string
  summary: string
  isHighSnr: boolean
  sectors: string[]
  stocks: NewsStock[]
}

export interface NewsData {
  items: NewsItem[]
  sourceStatus: Record<string, string>
  meta: PanoramaMeta
}

const NEWS_SOURCES: readonly NewsSource[] = ["财联社", "东方财富", "同花顺", "金十数据"]
const NEWS_LEVELS: readonly NewsLevel[] = ["S", "A", "B"]
const NEWS_SENTIMENTS: readonly NewsSentiment[] = ["利好", "利空", "催化", "中性"]

export function fetchNews(): Promise<NewsData> {
  return load("/api/v1/ashare/news", (d) => {
    const status: Record<string, string> = {}
    for (const [k, v] of Object.entries(asRecord(d.sourceStatus) ?? {})) {
      status[k] = str(v)
    }
    return {
      items: recList(d.items, (r) => {
        const source = NEWS_SOURCES.find((s) => s === r.source)
        if (!source) return null
        return {
          id: str(r.id),
          time: str(r.time),
          date: str(r.date),
          timestamp: asNumber(r.timestamp) ?? 0,
          source,
          region: r.region === "overseas" ? "overseas" : "domestic",
          impactLevel: NEWS_LEVELS.find((l) => l === r.impactLevel) ?? "B",
          sentiment: NEWS_SENTIMENTS.find((s) => s === r.sentiment) ?? "中性",
          title: str(r.title),
          summary: str(r.summary),
          isHighSnr: r.isHighSnr === true,
          sectors: strList(r.sectors),
          stocks: recList(r.stocks, (s) => ({
            code: str(s.code),
            name: str(s.name),
            changePct: asNumber(s.changePct),
          })),
        }
      }),
      sourceStatus: status,
      meta: parseMeta(d.meta),
    }
  })
}

// ───────────── 个股详情 ─────────────

export interface IntradayPoint {
  time: string
  price: number
  avgPrice: number
  volume: number
}

export interface DailyBar {
  date: string
  open: number
  close: number
  low: number
  high: number
  volume: number
  changePct: number
}

export interface LhbSeatLine {
  branchName: string
  buyWan: number
  sellWan: number
  netWan: number
}

export interface StockDetailData {
  code: string
  name: string
  price: number | null
  preClose: number | null
  changePct: number | null
  turnoverPct: number | null
  floatCapYi: number | null
  sealAmountWan: number | null
  sealFloatRatioPct: number | null
  streakLabel: string
  firstSealTime: string
  sectors: string[]
  catalystReason: string
  /** 韭研公社异动解析（需后端配置 JYGS_SESSION）；没收录该股时为空串。 */
  actionSector: string
  actionSectorReason: string
  actionExplain: string
  intraday: IntradayPoint[]
  dailyBars: DailyBar[]
  lhbDate: string | null
  lhbReason: string
  buySeats: LhbSeatLine[]
  sellSeats: LhbSeatLine[]
  meta: PanoramaMeta
}

function parseSeatLine(r: Rec): LhbSeatLine {
  return {
    branchName: str(r.branchName),
    buyWan: asNumber(r.buyWan) ?? 0,
    sellWan: asNumber(r.sellWan) ?? 0,
    netWan: asNumber(r.netWan) ?? 0,
  }
}

export function fetchStockDetail(code: string): Promise<StockDetailData> {
  return load(`/api/v1/ashare/stock-detail/${encodeURIComponent(code)}`, (d) => ({
    code: str(d.code),
    name: str(d.name),
    price: asNumber(d.price),
    preClose: asNumber(d.preClose),
    changePct: asNumber(d.changePct),
    turnoverPct: asNumber(d.turnoverPct),
    floatCapYi: asNumber(d.floatCapYi),
    sealAmountWan: asNumber(d.sealAmountWan),
    sealFloatRatioPct: asNumber(d.sealFloatRatioPct),
    streakLabel: str(d.streakLabel),
    firstSealTime: str(d.firstSealTime),
    sectors: strList(d.sectors),
    catalystReason: str(d.catalystReason),
    actionSector: str(d.actionSector),
    actionSectorReason: str(d.actionSectorReason),
    actionExplain: str(d.actionExplain),
    intraday: recList(d.intraday, (r) => {
      const price = asNumber(r.price)
      return price === null
        ? null
        : {
            time: str(r.time),
            price,
            avgPrice: asNumber(r.avgPrice) ?? price,
            volume: asNumber(r.volume) ?? 0,
          }
    }),
    dailyBars: recList(d.dailyBars, (r) => {
      const open = asNumber(r.open)
      const close = asNumber(r.close)
      const low = asNumber(r.low)
      const high = asNumber(r.high)
      if (open === null || close === null || low === null || high === null) return null
      return {
        date: str(r.date),
        open,
        close,
        low,
        high,
        volume: asNumber(r.volume) ?? 0,
        changePct: asNumber(r.changePct) ?? 0,
      }
    }),
    lhbDate: asString(d.lhbDate),
    lhbReason: str(d.lhbReason),
    buySeats: recList(d.buySeats, parseSeatLine),
    sellSeats: recList(d.sellSeats, parseSeatLine),
    meta: parseMeta(d.meta),
  }))
}

// ───────────── 全球指数 / 商品详情（分时 + 日K） ─────────────

/** 指数 / 商品分时点：均价、成交量上游没给时为 null（不要当 0 画）。 */
export interface IndexIntradayPoint {
  time: string
  price: number
  avgPrice: number | null
  volume: number | null
}

/** 指数 / 商品日 K：成交量（黄金、原油日 K 没有）、涨跌幅（最早一根可能没有）可能为 null。 */
export interface IndexDailyBar {
  date: string
  open: number
  close: number
  low: number
  high: number
  volume: number | null
  changePct: number | null
}

export interface IndexDetailData {
  /** 全球行情里的 symbol，如 000001.SH、HSI.HK、NDX.US、COMEX黄金。 */
  code: string
  name: string
  category: GlobalQuoteItem["category"]
  price: number | null
  change: number | null
  changePct: number | null
  open: number | null
  high: number | null
  low: number | null
  preClose: number | null
  amountYuan: number | null
  /** 分时（海外品种为北京时间，美股跨午夜）；商品在腾讯兜底时为空，原因见 meta.unavailable["指数分时"]。 */
  intraday: IndexIntradayPoint[]
  /** 近 90 根日 K，日期升序。 */
  dailyBars: IndexDailyBar[]
  meta: PanoramaMeta
}

function parseIndexBar(r: Rec): IndexDailyBar | null {
  const open = asNumber(r.open)
  const close = asNumber(r.close)
  const low = asNumber(r.low)
  const high = asNumber(r.high)
  if (open === null || close === null || low === null || high === null) return null
  return {
    date: str(r.date),
    open,
    close,
    low,
    high,
    volume: asNumber(r.volume),
    changePct: asNumber(r.changePct),
  }
}

export function parseIndexDetail(d: Rec): IndexDetailData {
  return {
    code: str(d.code),
    name: str(d.name),
    category: parseCategory(d.category),
    price: asNumber(d.price),
    change: asNumber(d.change),
    changePct: asNumber(d.changePct),
    open: asNumber(d.open),
    high: asNumber(d.high),
    low: asNumber(d.low),
    preClose: asNumber(d.preClose),
    amountYuan: asNumber(d.amountYuan),
    intraday: recList(d.intraday, (r) => {
      const price = asNumber(r.price)
      return price === null
        ? null
        : {
            time: str(r.time),
            price,
            avgPrice: asNumber(r.avgPrice),
            volume: asNumber(r.volume),
          }
    }),
    dailyBars: recList(d.dailyBars, parseIndexBar),
    meta: parseMeta(d.meta),
  }
}

export function fetchIndexDetail(code: string): Promise<IndexDetailData> {
  return load(`/api/v1/ashare/index-detail?code=${encodeURIComponent(code)}`, parseIndexDetail)
}
