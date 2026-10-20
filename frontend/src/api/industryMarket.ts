/**
 * 产业研究 · 板块行情接口（/api/v1/industry-market/*）的解析层。
 * 后端已经算好所有派生数字（区间涨幅、超额、估值分位、子行业加权），这里只做收窄与展示格式。
 */
import { apiUrl, authorizedFetch, isTimeout, readJson } from "./client"
import { asList, asNumber, asRecord, asString, dataOf, listOf } from "./json"

export type BoardLevel = "1" | "2" | "3" | "concept"
export type SectionStatus = "ok" | "stale" | "unavailable"

export const LEVEL_OPTIONS: { key: BoardLevel; label: string }[] = [
  { key: "1", label: "一级行业" },
  { key: "2", label: "二级行业" },
  { key: "3", label: "三级行业" },
  { key: "concept", label: "概念板块" },
]

export interface Section {
  status: SectionStatus
  reason: string
  fetchedAt: string
  /** stored = 读自收盘后入库的快照/日线，不是实时行情。 */
  origin: "live" | "stored"
  dataDate: string
}

export interface LinkedThesis {
  id: string
  title: string
  hypothesis: string
  status: string
  rationale: string
  support: number
  challenge: number
}

export interface LinkedIndustry {
  id: string
  name: string
  match: string
  theses: LinkedThesis[]
}

export interface BoardTheses {
  boardName: string
  industries: LinkedIndustry[]
  rules: string[]
}

export interface SourceRef {
  name: string
  url: string
  fetchedAt: string
}

export interface BoardQuote {
  code: string
  name: string
  kind: string
  level: number
  last: number | null
  changePct: number | null
  change5dPct: number | null
  change10dPct: number | null
  change60dPct: number | null
  changeYtdPct: number | null
  amount: number | null
  turnoverPct: number | null
  peTtm: number | null
  marketCap: number | null
  mainNetInflow: number | null
  mainNetInflow5d: number | null
  advancers: number | null
  decliners: number | null
  unchanged: number | null
  leaderCode: string
  leaderName: string
  leaderChangePct: number | null
}

export interface Breadth {
  boards: number
  boardsUp: number
  boardsDown: number
  stocksUp: number
  stocksDown: number
  stocksFlat: number
  totalMainNetInflow: number | null
}

export interface BoardOverview {
  level: string
  section: Section
  breadth: Breadth | null
  boards: BoardQuote[]
  sources: SourceRef[]
  rules: string[]
}

export interface CatalogItem {
  code: string
  name: string
  kind: string
  level: number
}

export interface Bar {
  date: string
  open: number
  close: number
  high: number
  low: number
  volume: number
  amount: number
  ma20: number | null
  ma60: number | null
}

export interface CurvePoint {
  date: string
  boardIndex: number
  benchmarkIndex: number
  excessPct: number
}

export interface WindowReturns {
  d5: number | null
  d20: number | null
  d60: number | null
  d120: number | null
  d250: number | null
  excessD20: number | null
  excessD60: number | null
  excessD250: number | null
}

export interface Member {
  code: string
  exchange: string
  name: string
  last: number | null
  changePct: number | null
  change5dPct: number | null
  change10dPct: number | null
  change60dPct: number | null
  changeYtdPct: number | null
  amount: number | null
  turnoverPct: number | null
  peDynamic: number | null
  pb: number | null
  marketCap: number | null
  mainNetInflow: number | null
  subIndustry: string
}

export interface Bucket {
  label: string
  count: number
  tone: string
}

export interface SubIndustry {
  name: string
  code: string
  members: number
  marketCap: number | null
  weightedChangePct: number | null
  mainNetInflow: number | null
}

export interface Percentile {
  current: number
  percentile: number
  minimum: number
  p20: number
  median: number
  p80: number
  maximum: number
  samples: number
  start: string
  end: string
}

export interface ValuationPoint {
  date: string
  peTtm: number | null
  pbMrq: number | null
}

export interface BoardDetail {
  board: BoardQuote
  quoteSection: Section
  trend: { section: Section; bars: Bar[]; relative: CurvePoint[]; returns: WindowReturns; benchmark: string }
  members: {
    section: Section
    total: number
    members: Member[]
    distribution: Bucket[]
    suspended: number
    subIndustries: SubIndustry[]
  }
  valuation: {
    section: Section
    peTtm: Percentile | null
    pbMrq: Percentile | null
    lossMembers: number | null
    members: number | null
    series: ValuationPoint[]
    windowYears: number
  }
  sources: SourceRef[]
  rules: string[]
  analyticsVersion: string
}

export interface Report {
  infoCode: string
  title: string
  orgName: string
  publishedOn: string
  rating: string
  researcher: string
  url: string
}

export interface HotStock {
  rank: number
  code: string
  name: string
  exchange: string
  changePct: number | null
  heat: number | null
  rankChange: number | null
  industry: string
  tags: string[]
  note: string
}

export interface HotPlate {
  rank: number
  code: string
  name: string
  changePct: number | null
  heat: number | null
  rankChange: number | null
  tag: string
}

export interface HotList {
  eastmoney: HotStock[]
  eastmoneySection: Section
  thsStocks: HotStock[]
  thsSection: Section
  thsIndustries: HotPlate[]
  thsConcepts: HotPlate[]
  platesSection: Section
  sources: SourceRef[]
}

export class FetchFailure extends Error {
  constructor(
    message: string,
    readonly status = 0,
  ) {
    super(message)
  }
}

export async function getData(path: string, init?: RequestInit): Promise<Record<string, unknown>> {
  let response: Response
  try {
    response = await fetch(apiUrl(path), init)
  } catch (error) {
    throw new FetchFailure(isTimeout(error) ? "请求超时，请稍后重试" : "网络连接失败，请稍后重试")
  }
  return unwrap(response, await readJson(response))
}

export function unwrap(response: Response, body: unknown): Record<string, unknown> {
  if (!response.ok) {
    throw new FetchFailure(
      asString(asRecord(asRecord(body)?.error)?.message) ?? "上游行情暂时不可用",
      response.status,
    )
  }
  const data = dataOf(body)
  if (!data) {
    throw new FetchFailure("接口返回了无法识别的内容")
  }
  return data
}

/** asOf 为空时取实时（实时失败后端会回退到最近入库快照）；给定日期则只读该日收盘快照。 */
export async function fetchOverview(level: BoardLevel, asOf = ""): Promise<BoardOverview> {
  const suffix = asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""
  return parseOverview(await getData(`/api/v1/industry-market/boards?level=${level}${suffix}`))
}

export async function fetchSnapshotDates(): Promise<string[]> {
  const data = await getData("/api/v1/industry-market/snapshot-dates")
  return asList(data.dates).flatMap((item) => (typeof item === "string" ? [item] : []))
}

export async function fetchBoardTheses(
  code: string,
  token: string | null,
  refresh: () => Promise<string | null>,
): Promise<BoardTheses> {
  let response: Response
  try {
    response = await authorizedFetch(
      `/api/v1/industry-market/boards/${encodeURIComponent(code)}/theses`,
      { method: "GET" },
      token,
      refresh,
    )
  } catch {
    throw new FetchFailure("网络连接失败，请稍后重试")
  }
  return parseBoardTheses(unwrap(response, await readJson(response)))
}

export function parseBoardTheses(data: Record<string, unknown>): BoardTheses {
  return {
    boardName: asString(data.board_name) ?? "",
    industries: listOf(data.industries).flatMap((row) => {
      const id = asString(row.id)
      const name = asString(row.name)
      if (!id || !name) {
        return []
      }
      return [
        {
          id,
          name,
          match: asString(row.match) ?? "",
          theses: listOf(row.theses).flatMap((item) => {
            const thesisId = asString(item.id)
            const title = asString(item.title)
            return thesisId && title
              ? [
                  {
                    id: thesisId,
                    title,
                    hypothesis: asString(item.hypothesis) ?? "",
                    status: asString(item.status) ?? "open",
                    rationale: asString(item.rationale) ?? "",
                    support: asNumber(item.support) ?? 0,
                    challenge: asNumber(item.challenge) ?? 0,
                  },
                ]
              : []
          }),
        },
      ]
    }),
    rules: asList(data.rules).flatMap((item) => (typeof item === "string" ? [item] : [])),
  }
}

export async function fetchCatalog(): Promise<CatalogItem[]> {
  const data = await getData("/api/v1/industry-market/catalog")
  return listOf(data.boards).flatMap((row) => {
    const code = asString(row.code)
    const name = asString(row.name)
    return code && name
      ? [{ code, name, kind: asString(row.kind) ?? "", level: asNumber(row.level) ?? 0 }]
      : []
  })
}

export async function fetchDetail(code: string): Promise<BoardDetail> {
  return parseDetail(await getData(`/api/v1/industry-market/boards/${encodeURIComponent(code)}`))
}

export async function fetchReports(code: string): Promise<{ section: Section; reports: Report[] }> {
  const data = await getData(`/api/v1/industry-market/boards/${encodeURIComponent(code)}/reports`)
  return {
    section: parseSection(data.section),
    reports: listOf(data.reports).flatMap((row) => {
      const infoCode = asString(row.info_code)
      const title = asString(row.title)
      return infoCode && title
        ? [
            {
              infoCode,
              title,
              orgName: asString(row.org_name) ?? "—",
              publishedOn: asString(row.published_on) ?? "",
              rating: asString(row.rating) ?? "",
              researcher: asString(row.researcher) ?? "",
              url: asString(row.url) ?? "",
            },
          ]
        : []
    }),
  }
}

export async function fetchHot(): Promise<HotList> {
  const data = await getData("/api/v1/industry-market/hot")
  return {
    eastmoney: listOf(data.eastmoney).flatMap(parseHotStock),
    eastmoneySection: parseSection(data.eastmoney_section),
    thsStocks: listOf(data.ths_stocks).flatMap(parseHotStock),
    thsSection: parseSection(data.ths_section),
    thsIndustries: listOf(data.ths_industries).flatMap(parseHotPlate),
    thsConcepts: listOf(data.ths_concepts).flatMap(parseHotPlate),
    platesSection: parseSection(data.plates_section),
    sources: listOf(data.sources).flatMap(parseSource),
  }
}

export function parseSection(value: unknown): Section {
  const row = asRecord(value)
  const status = asString(row?.status)
  return {
    status: status === "ok" || status === "stale" ? status : "unavailable",
    reason: asString(row?.reason) ?? "",
    fetchedAt: asString(row?.fetched_at) ?? "",
    origin: asString(row?.origin) === "stored" ? "stored" : "live",
    dataDate: asString(row?.data_date) ?? "",
  }
}

/** 页面角标：实时 / 收盘快照（某日）/ 过期 / 不可用。 */
export function sectionBadge(section: Section): { text: string; tone: "ok" | "warn" | "muted" | "danger" } {
  if (section.status === "unavailable") {
    return { text: "暂不可用", tone: "danger" }
  }
  if (section.origin === "stored") {
    const day = section.dataDate ? ` ${section.dataDate}` : ""
    return { text: `收盘快照${day}`, tone: section.status === "stale" ? "warn" : "muted" }
  }
  return section.status === "stale" ? { text: "缓存数据", tone: "warn" } : { text: "实时", tone: "ok" }
}

export function parseOverview(data: Record<string, unknown>): BoardOverview {
  const breadth = asRecord(data.breadth)
  return {
    level: asString(data.level) ?? "",
    section: parseSection(data.section),
    breadth: breadth
      ? {
          boards: asNumber(breadth.boards) ?? 0,
          boardsUp: asNumber(breadth.boards_up) ?? 0,
          boardsDown: asNumber(breadth.boards_down) ?? 0,
          stocksUp: asNumber(breadth.stocks_up) ?? 0,
          stocksDown: asNumber(breadth.stocks_down) ?? 0,
          stocksFlat: asNumber(breadth.stocks_flat) ?? 0,
          totalMainNetInflow: asNumber(breadth.total_main_net_inflow),
        }
      : null,
    boards: listOf(data.boards).flatMap(parseQuote),
    sources: listOf(data.sources).flatMap(parseSource),
    rules: asList(data.rules).flatMap((item) => (typeof item === "string" ? [item] : [])),
  }
}

export function parseDetail(data: Record<string, unknown>): BoardDetail {
  const board = parseQuote(asRecord(data.board) ?? {})[0]
  if (!board) {
    throw new FetchFailure("接口没有返回板块信息")
  }
  const trend = asRecord(data.trend) ?? {}
  const members = asRecord(data.members) ?? {}
  const valuation = asRecord(data.valuation) ?? {}
  const returns = asRecord(trend.returns) ?? {}
  return {
    board,
    quoteSection: parseSection(data.quote_section),
    trend: {
      section: parseSection(trend.section),
      bars: listOf(trend.bars).flatMap((row) => {
        const date = asString(row.trade_date)
        const open = asNumber(row.open)
        const close = asNumber(row.close)
        const high = asNumber(row.high)
        const low = asNumber(row.low)
        if (!date || open === null || close === null || high === null || low === null) {
          return []
        }
        return [
          {
            date,
            open,
            close,
            high,
            low,
            volume: asNumber(row.volume) ?? 0,
            amount: asNumber(row.amount) ?? 0,
            ma20: asNumber(row.ma20),
            ma60: asNumber(row.ma60),
          },
        ]
      }),
      relative: listOf(trend.relative).flatMap((row) => {
        const date = asString(row.trade_date)
        const boardIndex = asNumber(row.board_index)
        const benchmarkIndex = asNumber(row.benchmark_index)
        const excessPct = asNumber(row.excess_pct)
        return date && boardIndex !== null && benchmarkIndex !== null && excessPct !== null
          ? [{ date, boardIndex, benchmarkIndex, excessPct }]
          : []
      }),
      returns: {
        d5: asNumber(returns.d5),
        d20: asNumber(returns.d20),
        d60: asNumber(returns.d60),
        d120: asNumber(returns.d120),
        d250: asNumber(returns.d250),
        excessD20: asNumber(returns.excess_d20),
        excessD60: asNumber(returns.excess_d60),
        excessD250: asNumber(returns.excess_d250),
      },
      benchmark: asString(trend.benchmark) ?? "沪深300",
    },
    members: {
      section: parseSection(members.section),
      total: asNumber(members.total) ?? 0,
      members: listOf(members.members).flatMap(parseMember),
      distribution: listOf(members.distribution).flatMap((row) => {
        const label = asString(row.label)
        return label ? [{ label, count: asNumber(row.count) ?? 0, tone: asString(row.tone) ?? "flat" }] : []
      }),
      suspended: asNumber(members.suspended) ?? 0,
      subIndustries: listOf(members.sub_industries).flatMap((row) => {
        const name = asString(row.name)
        return name
          ? [
              {
                name,
                code: asString(row.code) ?? "",
                members: asNumber(row.members) ?? 0,
                marketCap: asNumber(row.market_cap),
                weightedChangePct: asNumber(row.weighted_change_pct),
                mainNetInflow: asNumber(row.main_net_inflow),
              },
            ]
          : []
      }),
    },
    valuation: {
      section: parseSection(valuation.section),
      peTtm: parsePercentile(valuation.pe_ttm),
      pbMrq: parsePercentile(valuation.pb_mrq),
      lossMembers: asNumber(valuation.loss_members),
      members: asNumber(valuation.members),
      series: listOf(valuation.series).flatMap((row) => {
        const date = asString(row.trade_date)
        return date ? [{ date, peTtm: asNumber(row.pe_ttm), pbMrq: asNumber(row.pb_mrq) }] : []
      }),
      windowYears: asNumber(valuation.window_years) ?? 5,
    },
    sources: listOf(data.sources).flatMap(parseSource),
    rules: asList(data.rules).flatMap((item) => (typeof item === "string" ? [item] : [])),
    analyticsVersion: asString(data.analytics_version) ?? "",
  }
}

function parseQuote(row: Record<string, unknown>): BoardQuote[] {
  const code = asString(row.code)
  const name = asString(row.name)
  if (!code || !name) {
    return []
  }
  return [
    {
      code,
      name,
      kind: asString(row.kind) ?? "industry",
      level: asNumber(row.level) ?? 0,
      last: asNumber(row.last),
      changePct: asNumber(row.change_pct),
      change5dPct: asNumber(row.change_5d_pct),
      change10dPct: asNumber(row.change_10d_pct),
      change60dPct: asNumber(row.change_60d_pct),
      changeYtdPct: asNumber(row.change_ytd_pct),
      amount: asNumber(row.amount),
      turnoverPct: asNumber(row.turnover_pct),
      peTtm: asNumber(row.pe_ttm),
      marketCap: asNumber(row.market_cap),
      mainNetInflow: asNumber(row.main_net_inflow),
      mainNetInflow5d: asNumber(row.main_net_inflow_5d),
      advancers: asNumber(row.advancers),
      decliners: asNumber(row.decliners),
      unchanged: asNumber(row.unchanged),
      leaderCode: asString(row.leader_code) ?? "",
      leaderName: asString(row.leader_name) ?? "",
      leaderChangePct: asNumber(row.leader_change_pct),
    },
  ]
}

function parseMember(row: Record<string, unknown>): Member[] {
  const code = asString(row.code)
  const name = asString(row.name)
  if (!code || !name) {
    return []
  }
  return [
    {
      code,
      name,
      exchange: asString(row.exchange) ?? "",
      last: asNumber(row.last),
      changePct: asNumber(row.change_pct),
      change5dPct: asNumber(row.change_5d_pct),
      change10dPct: asNumber(row.change_10d_pct),
      change60dPct: asNumber(row.change_60d_pct),
      changeYtdPct: asNumber(row.change_ytd_pct),
      amount: asNumber(row.amount),
      turnoverPct: asNumber(row.turnover_pct),
      peDynamic: asNumber(row.pe_dynamic),
      pb: asNumber(row.pb),
      marketCap: asNumber(row.market_cap),
      mainNetInflow: asNumber(row.main_net_inflow),
      subIndustry: asString(row.sub_industry) ?? "",
    },
  ]
}

function parsePercentile(value: unknown): Percentile | null {
  const row = asRecord(value)
  const current = asNumber(row?.current)
  const percentile = asNumber(row?.percentile)
  if (!row || current === null || percentile === null) {
    return null
  }
  return {
    current,
    percentile,
    minimum: asNumber(row.minimum) ?? current,
    p20: asNumber(row.p20) ?? current,
    median: asNumber(row.median) ?? current,
    p80: asNumber(row.p80) ?? current,
    maximum: asNumber(row.maximum) ?? current,
    samples: asNumber(row.samples) ?? 0,
    start: asString(row.start) ?? "",
    end: asString(row.end) ?? "",
  }
}

function parseSource(row: Record<string, unknown>): SourceRef[] {
  const name = asString(row.name)
  return name ? [{ name, url: asString(row.url) ?? "", fetchedAt: asString(row.fetched_at) ?? "" }] : []
}

function parseHotStock(row: Record<string, unknown>): HotStock[] {
  const code = asString(row.code)
  const name = asString(row.name)
  const rank = asNumber(row.rank)
  if (!code || !name || rank === null) {
    return []
  }
  return [
    {
      rank,
      code,
      name,
      exchange: asString(row.exchange) ?? "",
      changePct: asNumber(row.change_pct),
      heat: asNumber(row.heat),
      rankChange: asNumber(row.rank_change),
      industry: asString(row.industry) ?? "",
      tags: asList(row.tags).flatMap((tag) => (typeof tag === "string" ? [tag] : [])),
      note: asString(row.note) ?? "",
    },
  ]
}

function parseHotPlate(row: Record<string, unknown>): HotPlate[] {
  const code = asString(row.code)
  const name = asString(row.name)
  const rank = asNumber(row.rank)
  return code && name && rank !== null
    ? [
        {
          rank,
          code,
          name,
          changePct: asNumber(row.change_pct),
          heat: asNumber(row.heat),
          rankChange: asNumber(row.rank_change),
          tag: asString(row.tag) ?? "",
        },
      ]
    : []
}

/** 股票代码 → 本系统公司页使用的 600519.SH 形式；北交所暂不支持公司页时返回空串。 */
export function companySymbol(code: string, exchange: string): string {
  if (exchange === "SSE") {
    return `${code}.SH`
  }
  if (exchange === "SZSE") {
    return `${code}.SZ`
  }
  return ""
}

export function levelLabel(kind: string, level: number): string {
  if (kind === "concept") {
    return "概念"
  }
  return ["", "一级", "二级", "三级"][level] ?? "行业"
}

/** 象限只按两个已给定的东财区间涨幅分区，不做额外拟合。 */
export function momentumQuadrant(short: number | null, long: number | null): string {
  if (short === null || long === null) {
    return "数据不足"
  }
  if (long >= 0) {
    return short >= 0 ? "强势延续" : "高位回调"
  }
  return short >= 0 ? "低位反弹" : "弱势下行"
}
