/**
 * 总览驾驶舱（/api/v1/dashboard/*）的解析层。
 * 温度、流动性、热度指数、强弱分组等所有派生数字都由后端规则函数算好，这里只做收窄。
 * 百分数单位 %，金额单位元（外盘资产为各自报价单位），利率变化单位 bp。
 */
import { authorizedFetch, readJson } from "./client"
import { FetchFailure, getData, parseSection, unwrap, type Section, type SourceRef } from "./industryMarket"
import { asBool, asList, asNumber, asRecord, asString, listOf } from "./json"

export type IndexPeriod = "min" | "day" | "week" | "month"

export const PERIOD_OPTIONS: { key: IndexPeriod; label: string }[] = [
  { key: "min", label: "分时" },
  { key: "day", label: "日K" },
  { key: "week", label: "周K" },
  { key: "month", label: "月K" },
]

export interface Factor {
  code: string
  label: string
  weight: number
  score: number
  raw: string
  formula: string
  value: number | null
  /** 对总分的贡献 = 得分 × 权重 / 可用权重 */
  contribution: number | null
}

export interface ScoreBlock {
  method: string
  score: number | null
  label: string
  coverage: number
  factors: Factor[]
  missing: string[]
  previous: number | null
  previousDate: string
  /** 较上一交易日入库值的变化；没有入库记录时为 null */
  change: number | null
}

export interface SeriesPoint {
  day: string
  value: number
}

export interface IndexQuote {
  symbol: string
  name: string
  last: number | null
  prevClose: number | null
  open: number | null
  high: number | null
  low: number | null
  change: number | null
  changePct: number | null
  amplitudePct: number | null
  amount: number | null
  quoteTime: string
}

export interface Bucket {
  bucket: number
  label: string
  count: number
}

export interface Breadth {
  up: number
  down: number
  flat: number
  total: number
  strong: number
  weak: number
  limitUp: number
  limitDown: number
  buckets: Bucket[]
}

export interface LimitStats {
  limitUp: number
  broken: number | null
  limitDown: number | null
  sealRate: number | null
  maxStreak: number
}

export interface AmountPoint {
  day: string
  amount: number
  ma20: number | null
}

export interface MarketOverview {
  tradeDate: string
  temperature: ScoreBlock
  quotesSection: Section
  breadthSection: Section
  limitsSection: Section
  historySection: Section
  indices: IndexQuote[]
  breadth: Breadth | null
  limits: LimitStats | null
  marketAmount: number | null
  amountBase: number | null
  amountVsBasePct: number | null
  amounts: AmountPoint[]
  rules: string[]
  sources: SourceRef[]
}

export interface Bar {
  day: string
  open: number
  close: number
  high: number
  low: number
  amount: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
}

export interface MinutePoint {
  clock: string
  price: number
  /** 该分钟的成交额（元），后端已由累计额差分得到。 */
  amount: number
}

export interface IndexBars {
  symbol: string
  name: string
  period: IndexPeriod
  section: Section
  prevClose: number | null
  amountVs5dPct: number | null
  bars: Bar[]
  minutes: MinutePoint[]
  sources: SourceRef[]
}

export interface MacroTile {
  code: string
  label: string
  value: number | null
  unit: string
  change: number | null
  changeUnit: string
  changeLabel: string
  period: string
}

export interface GlobalAsset {
  code: string
  name: string
  last: number | null
  changePct: number | null
  quoteTime: string
}

export interface MacroOverview {
  liquidity: ScoreBlock
  growth: ScoreBlock
  regime: string
  cpiYoy: number | null
  inflationMomentum: number | null
  usSection: Section
  chinaSection: Section
  assetsSection: Section
  tiles: MacroTile[]
  assets: GlobalAsset[]
  netLiquidity: SeriesPoint[]
  dollar: SeriesPoint[]
  realYield: SeriesPoint[]
  shiborOn: SeriesPoint[]
  cnUsSpread: SeriesPoint[]
  rules: string[]
  sources: SourceRef[]
}

export interface HotBoard {
  code: string
  name: string
  kind: string
  heat: number | null
  changePct: number | null
  limitUps: number
  heatScore: number
  changeScore: number
  limitScore: number
  index: number
  hot: boolean
  tag: string
  hotTag: string
  leaderCode: string
  leaderName: string
  leaderChangePct: number | null
}

export interface GainBoard {
  code: string
  name: string
  kind: string
  members: number
  changePct: number | null
  amount: number | null
  leaderCode: string
  leaderName: string
  leaderChangePct: number | null
}

export interface BoardsOverview {
  tradeDate: string
  heatSection: Section
  gainSection: Section
  hotCount: number | null
  previousHotCount: number | null
  hotCountChange: number | null
  previousDate: string
  boards: HotBoard[]
  gainers: GainBoard[]
  rules: string[]
  sources: SourceRef[]
}

export interface HotStock {
  code: string
  name: string
  rank: number | null
  last: number | null
  changePct: number | null
  peTtm: number | null
  amount: number | null
  turnoverPct: number | null
  tag: string
  concepts: string[]
}

export interface StocksOverview {
  hotSection: Section
  gainSection: Section
  hot: HotStock[]
  gainers: HotStock[]
  rules: string[]
  sources: SourceRef[]
}

export interface Announcement {
  id: string
  code: string
  name: string
  title: string
  url: string
  publishedAt: string
  kind: string
  level: number
  tone: string
  heatLevel: number | null
  heatReason: string
}

export interface AnnouncementsOverview {
  section: Section
  windowStart: string
  windowEnd: string
  scanned: number
  important: Announcement[]
  hot: Announcement[]
  counts: { name: string; count: number }[]
  rules: string[]
  sources: SourceRef[]
}

export interface WatchSignal {
  tone: string
  text: string
}

export interface WatchRow {
  code: string
  name: string
  last: number | null
  changePct: number | null
  change5dPct: number | null
  state: string
  closes: number[]
  signals: WatchSignal[]
}

export interface WatchOverview {
  groupId: string
  groupName: string
  groups: { id: string; name: string; count: number }[]
  quoteSection: Section
  sparkSection: Section
  total: number
  strong: number
  neutral: number
  weak: number
  unknown: number
  rows: WatchRow[]
  rules: string[]
  sources: SourceRef[]
}

type Refresh = () => Promise<string | null>

export async function fetchMarket(): Promise<MarketOverview> {
  return parseMarket(await getData("/api/v1/dashboard/market"))
}

export async function fetchIndexBars(symbol: string, period: IndexPeriod): Promise<IndexBars> {
  const query = `symbol=${encodeURIComponent(symbol)}&period=${period}`
  return parseIndexBars(await getData(`/api/v1/dashboard/index-bars?${query}`))
}

export async function fetchMacro(): Promise<MacroOverview> {
  return parseMacro(await getData("/api/v1/dashboard/macro"))
}

export async function fetchBoards(): Promise<BoardsOverview> {
  return parseBoards(await getData("/api/v1/dashboard/boards"))
}

export async function fetchStocks(): Promise<StocksOverview> {
  return parseStocks(await getData("/api/v1/dashboard/stocks"))
}

export async function fetchAnnouncements(): Promise<AnnouncementsOverview> {
  return parseAnnouncements(await getData("/api/v1/dashboard/announcements"))
}

export async function fetchWatch(groupId: string, token: string | null, refresh: Refresh): Promise<WatchOverview> {
  const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : ""
  let response: Response
  try {
    response = await authorizedFetch(`/api/v1/dashboard/watch${query}`, { method: "GET" }, token, refresh)
  } catch {
    throw new FetchFailure("网络连接失败，请稍后重试")
  }
  return parseWatch(unwrap(response, await readJson(response)))
}

/** 6 位代码 → 公司页路由；北交所（4/8/9 开头）暂无公司页，返回空串。 */
export function companyLink(code: string): string {
  if (/^6\d{5}$/.test(code)) {
    return `/company/${code}.SH`
  }
  if (/^[03]\d{5}$/.test(code)) {
    return `/company/${code}.SZ`
  }
  return ""
}

export function parseScore(value: unknown): ScoreBlock {
  const row = asRecord(value)
  return {
    method: asString(row?.method) ?? "",
    score: asNumber(row?.score),
    label: asString(row?.label) ?? "无法判断",
    coverage: asNumber(row?.coverage) ?? 0,
    factors: listOf(row?.factors).flatMap((item) => {
      const code = asString(item.code)
      const weight = asNumber(item.weight)
      const score = asNumber(item.score)
      return code && weight !== null && score !== null
        ? [
            {
              code,
              label: asString(item.label) ?? code,
              weight,
              score,
              raw: asString(item.raw) ?? "",
              formula: asString(item.formula) ?? "",
              value: asNumber(item.value),
              contribution: asNumber(item.contribution),
            },
          ]
        : []
    }),
    missing: strings(row?.missing),
    previous: asNumber(row?.previous),
    previousDate: asString(row?.previous_date) ?? "",
    change: asNumber(row?.change),
  }
}

export function parseMarket(data: Record<string, unknown>): MarketOverview {
  const breadth = asRecord(data.breadth)
  const limits = asRecord(data.limits)
  return {
    tradeDate: asString(data.trade_date) ?? "",
    temperature: parseScore(data.temperature),
    quotesSection: parseSection(data.quotes_section),
    breadthSection: parseSection(data.breadth_section),
    limitsSection: parseSection(data.limits_section),
    historySection: parseSection(data.history_section),
    indices: listOf(data.indices).flatMap(parseQuote),
    breadth: breadth
      ? {
          up: count(breadth.up),
          down: count(breadth.down),
          flat: count(breadth.flat),
          total: count(breadth.total),
          strong: count(breadth.strong),
          weak: count(breadth.weak),
          limitUp: count(breadth.limit_up),
          limitDown: count(breadth.limit_down),
          buckets: listOf(breadth.buckets).flatMap((item) => {
            const bucket = asNumber(item.bucket)
            return bucket === null ? [] : [{ bucket, label: asString(item.label) ?? String(bucket), count: count(item.count) }]
          }),
        }
      : null,
    limits: limits
      ? {
          limitUp: count(limits.limit_up),
          broken: asNumber(limits.broken),
          limitDown: asNumber(limits.limit_down),
          sealRate: asNumber(limits.seal_rate),
          maxStreak: count(limits.max_streak),
        }
      : null,
    marketAmount: asNumber(data.market_amount),
    amountBase: asNumber(data.amount_base),
    amountVsBasePct: asNumber(data.amount_vs_base_pct),
    amounts: listOf(data.amounts).flatMap((item) => {
      const day = asString(item.day)
      const amount = asNumber(item.amount)
      return day && amount !== null ? [{ day, amount, ma20: asNumber(item.ma20) }] : []
    }),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

export function parseIndexBars(data: Record<string, unknown>): IndexBars {
  const period = asString(data.period)
  return {
    symbol: asString(data.symbol) ?? "",
    name: asString(data.name) ?? "",
    period: period === "min" || period === "week" || period === "month" ? period : "day",
    section: parseSection(data.section),
    prevClose: asNumber(data.prev_close),
    amountVs5dPct: asNumber(data.amount_vs_5d_pct),
    bars: listOf(data.bars).flatMap((item) => {
      const day = asString(item.day)
      const open = asNumber(item.open)
      const close = asNumber(item.close)
      const high = asNumber(item.high)
      const low = asNumber(item.low)
      return day && open !== null && close !== null && high !== null && low !== null
        ? [
            {
              day,
              open,
              close,
              high,
              low,
              amount: asNumber(item.amount),
              ma5: asNumber(item.ma5),
              ma10: asNumber(item.ma10),
              ma20: asNumber(item.ma20),
            },
          ]
        : []
    }),
    minutes: listOf(data.minutes).flatMap((item) => {
      const clock = asString(item.clock)
      const price = asNumber(item.price)
      return clock && price !== null ? [{ clock, price, amount: asNumber(item.amount) ?? 0 }] : []
    }),
    sources: parseSources(data.sources),
  }
}

export function parseMacro(data: Record<string, unknown>): MacroOverview {
  return {
    liquidity: parseScore(data.liquidity),
    growth: parseScore(data.growth),
    regime: asString(data.regime) ?? "无法判断",
    cpiYoy: asNumber(data.cpi_yoy),
    inflationMomentum: asNumber(data.inflation_momentum),
    usSection: parseSection(data.us_section),
    chinaSection: parseSection(data.china_section),
    assetsSection: parseSection(data.assets_section),
    tiles: listOf(data.tiles).flatMap((item) => {
      const code = asString(item.code)
      return code
        ? [
            {
              code,
              label: asString(item.label) ?? code,
              value: asNumber(item.value),
              unit: asString(item.unit) ?? "",
              change: asNumber(item.change),
              changeUnit: asString(item.change_unit) ?? "",
              changeLabel: asString(item.change_label) ?? "",
              period: asString(item.period) ?? "",
            },
          ]
        : []
    }),
    assets: listOf(data.assets).flatMap((item) => {
      const code = asString(item.code)
      return code
        ? [
            {
              code,
              name: asString(item.name) ?? code,
              last: asNumber(item.last),
              changePct: asNumber(item.change_pct),
              quoteTime: asString(item.quote_time) ?? "",
            },
          ]
        : []
    }),
    netLiquidity: series(data.net_liquidity),
    dollar: series(data.dollar),
    realYield: series(data.real_yield),
    shiborOn: series(data.shibor_on),
    cnUsSpread: series(data.cn_us_spread),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

export function parseBoards(data: Record<string, unknown>): BoardsOverview {
  return {
    tradeDate: asString(data.trade_date) ?? "",
    heatSection: parseSection(data.heat_section),
    gainSection: parseSection(data.gain_section),
    hotCount: asNumber(data.hot_count),
    previousHotCount: asNumber(data.previous_hot_count),
    hotCountChange: asNumber(data.hot_count_change),
    previousDate: asString(data.previous_date) ?? "",
    boards: listOf(data.boards).flatMap((item) => {
      const code = asString(item.code)
      const name = asString(item.name)
      const index = asNumber(item.index)
      return code && name && index !== null
        ? [
            {
              code,
              name,
              kind: asString(item.kind) ?? "",
              heat: asNumber(item.heat),
              changePct: asNumber(item.change_pct),
              limitUps: count(item.limit_ups),
              heatScore: asNumber(item.heat_score) ?? 0,
              changeScore: asNumber(item.change_score) ?? 0,
              limitScore: asNumber(item.limit_score) ?? 0,
              index,
              hot: asBool(item.hot) ?? false,
              tag: asString(item.tag) ?? "",
              hotTag: asString(item.hot_tag) ?? "",
              leaderCode: asString(item.leader_code) ?? "",
              leaderName: asString(item.leader_name) ?? "",
              leaderChangePct: asNumber(item.leader_change_pct),
            },
          ]
        : []
    }),
    gainers: listOf(data.gainers).flatMap((item) => {
      const code = asString(item.code)
      const name = asString(item.name)
      return code && name
        ? [
            {
              code,
              name,
              kind: asString(item.kind) ?? "",
              members: count(item.members),
              changePct: asNumber(item.change_pct),
              amount: asNumber(item.amount),
              leaderCode: asString(item.leader_code) ?? "",
              leaderName: asString(item.leader_name) ?? "",
              leaderChangePct: asNumber(item.leader_change_pct),
            },
          ]
        : []
    }),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

export function parseStocks(data: Record<string, unknown>): StocksOverview {
  return {
    hotSection: parseSection(data.hot_section),
    gainSection: parseSection(data.gain_section),
    hot: listOf(data.hot).flatMap(parseHotStock),
    gainers: listOf(data.gainers).flatMap(parseHotStock),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

export function parseAnnouncements(data: Record<string, unknown>): AnnouncementsOverview {
  return {
    section: parseSection(data.section),
    windowStart: asString(data.window_start) ?? "",
    windowEnd: asString(data.window_end) ?? "",
    scanned: count(data.scanned),
    important: listOf(data.important).flatMap(parseAnnouncement),
    hot: listOf(data.hot).flatMap(parseAnnouncement),
    counts: listOf(data.counts).flatMap((item) => {
      const name = asString(item.name)
      return name ? [{ name, count: count(item.count) }] : []
    }),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

export function parseWatch(data: Record<string, unknown>): WatchOverview {
  return {
    groupId: asString(data.group_id) ?? "",
    groupName: asString(data.group_name) ?? "",
    groups: listOf(data.groups).flatMap((item) => {
      const id = asString(item.id)
      return id ? [{ id, name: asString(item.name) ?? "未命名分组", count: count(item.count) }] : []
    }),
    quoteSection: parseSection(data.quote_section),
    sparkSection: parseSection(data.spark_section),
    total: count(data.total),
    strong: count(data.strong),
    neutral: count(data.neutral),
    weak: count(data.weak),
    unknown: count(data.unknown),
    rows: listOf(data.rows).flatMap((item) => {
      const code = asString(item.code)
      return code
        ? [
            {
              code,
              name: asString(item.name) ?? code,
              last: asNumber(item.last),
              changePct: asNumber(item.change_pct),
              change5dPct: asNumber(item.change_5d_pct),
              state: asString(item.state) ?? "",
              closes: asList(item.closes).flatMap((point) => {
                const value = asNumber(point)
                return value === null ? [] : [value]
              }),
              signals: listOf(item.signals).flatMap((signal) => {
                const text = asString(signal.text)
                return text ? [{ tone: asString(signal.tone) ?? "muted", text }] : []
              }),
            },
          ]
        : []
    }),
    rules: strings(data.rules),
    sources: parseSources(data.sources),
  }
}

function parseQuote(row: Record<string, unknown>): IndexQuote[] {
  const symbol = asString(row.symbol)
  if (!symbol) {
    return []
  }
  return [
    {
      symbol,
      name: asString(row.name) ?? symbol,
      last: asNumber(row.last),
      prevClose: asNumber(row.prev_close),
      open: asNumber(row.open),
      high: asNumber(row.high),
      low: asNumber(row.low),
      change: asNumber(row.change),
      changePct: asNumber(row.change_pct),
      amplitudePct: asNumber(row.amplitude_pct),
      amount: asNumber(row.amount),
      quoteTime: asString(row.quote_time) ?? "",
    },
  ]
}

function parseHotStock(row: Record<string, unknown>): HotStock[] {
  const code = asString(row.code)
  if (!code) {
    return []
  }
  return [
    {
      code,
      name: asString(row.name) ?? code,
      rank: asNumber(row.rank),
      last: asNumber(row.last),
      changePct: asNumber(row.change_pct),
      peTtm: asNumber(row.pe_ttm),
      amount: asNumber(row.amount),
      turnoverPct: asNumber(row.turnover_pct),
      tag: asString(row.tag) ?? "",
      concepts: strings(row.concepts),
    },
  ]
}

function parseAnnouncement(row: Record<string, unknown>): Announcement[] {
  const id = asString(row.id)
  const title = asString(row.title)
  if (!id || !title) {
    return []
  }
  return [
    {
      id,
      code: asString(row.code) ?? "",
      name: asString(row.name) ?? "",
      title,
      url: safeUrl(asString(row.url)),
      publishedAt: asString(row.published_at) ?? "",
      kind: asString(row.kind) ?? "其他",
      level: count(row.level),
      tone: asString(row.tone) ?? "muted",
      heatLevel: asNumber(row.heat_level),
      heatReason: asString(row.heat_reason) ?? "",
    },
  ]
}

function series(value: unknown): SeriesPoint[] {
  return listOf(value).flatMap((item) => {
    const day = asString(item.day)
    const point = asNumber(item.value)
    return day && point !== null ? [{ day, value: point }] : []
  })
}

function parseSources(value: unknown): SourceRef[] {
  return listOf(value).flatMap((row) => {
    const name = asString(row.name)
    return name ? [{ name, url: asString(row.url) ?? "", fetchedAt: asString(row.fetched_at) ?? "" }] : []
  })
}

/** 公告链接只放行 http(s)，避免把 javascript: 之类的地址渲染进 href。 */
function safeUrl(value: string | null): string {
  return value && /^https?:\/\//i.test(value) ? value : ""
}

function count(value: unknown): number {
  return asNumber(value) ?? 0
}

function strings(value: unknown): string[] {
  return asList(value).flatMap((item) => (typeof item === "string" ? [item] : []))
}
