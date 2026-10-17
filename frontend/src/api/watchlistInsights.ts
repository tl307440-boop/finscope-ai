/**
 * 自选看板（GET /api/v1/watchlists/{id}/insights）与批量导入（POST .../items/batch）。
 * 自选股代码保存在本系统账号下；行情、所属板块、涨停、调研、人气都由后端从公开数据源查询。
 */
import { authorizedFetch, readJson } from "./client"
import { FetchFailure, parseSection, unwrap, type Section, type SourceRef } from "./industryMarket"
import { asList, asNumber, asString, listOf } from "./json"

export interface WatchStock {
  instrumentId: string
  displaySymbol: string
  code: string
  name: string
  exchange: string
  origin: string
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
  industry: string
  boardCode: string
  limitStatus: string
  streak: number | null
  surveys: number
  surveyedInstitutions: number
  thsHotRank: number | null
  emHotRank: number | null
}

export interface Exposure {
  name: string
  boardCode: string
  count: number
  marketCap: number | null
  weightPct: number | null
  weightedChangePct: number | null
}

export interface WatchSummary {
  count: number
  quoted: number
  up: number
  down: number
  flat: number
  averageChangePct: number | null
  weightedChangePct: number | null
  totalMainNetInflow: number | null
  limitUp: number
  surveyed: number
  hot: number
}

export interface WatchlistInsights {
  groupName: string
  quoteSection: Section
  signalSection: Section
  summary: WatchSummary
  stocks: WatchStock[]
  industries: Exposure[]
  unsupported: string[]
  rules: string[]
  sources: SourceRef[]
}

export interface ImportResult {
  added: string[]
  existing: string[]
  missing: string[]
  invalid: string[]
}

type Refresh = () => Promise<string | null>

async function authed(path: string, init: RequestInit, token: string | null, refresh: Refresh) {
  let response: Response
  try {
    response = await authorizedFetch(path, init, token, refresh)
  } catch {
    throw new FetchFailure("网络连接失败，请稍后重试")
  }
  return unwrap(response, await readJson(response))
}

export async function fetchInsights(groupId: string, token: string | null, refresh: Refresh): Promise<WatchlistInsights> {
  const path = `/api/v1/watchlists/${encodeURIComponent(groupId)}/insights`
  return parseInsights(await authed(path, { method: "GET" }, token, refresh))
}

export async function importCodes(
  groupId: string,
  text: string,
  token: string | null,
  refresh: Refresh,
): Promise<ImportResult> {
  const data = await authed(
    `/api/v1/watchlists/${encodeURIComponent(groupId)}/items/batch`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) },
    token,
    refresh,
  )
  return {
    added: strings(data.added),
    existing: strings(data.existing),
    missing: strings(data.missing),
    invalid: strings(data.invalid),
  }
}

export function parseInsights(data: Record<string, unknown>): WatchlistInsights {
  const summary = listOf([data.summary])[0] ?? {}
  return {
    groupName: asString(data.group_name) ?? "",
    quoteSection: parseSection(data.quote_section),
    signalSection: parseSection(data.signal_section),
    summary: {
      count: asNumber(summary.count) ?? 0,
      quoted: asNumber(summary.quoted) ?? 0,
      up: asNumber(summary.up) ?? 0,
      down: asNumber(summary.down) ?? 0,
      flat: asNumber(summary.flat) ?? 0,
      averageChangePct: asNumber(summary.average_change_pct),
      weightedChangePct: asNumber(summary.weighted_change_pct),
      totalMainNetInflow: asNumber(summary.total_main_net_inflow),
      limitUp: asNumber(summary.limit_up) ?? 0,
      surveyed: asNumber(summary.surveyed) ?? 0,
      hot: asNumber(summary.hot) ?? 0,
    },
    stocks: listOf(data.stocks).flatMap(parseStock),
    industries: listOf(data.industries).flatMap((row) => {
      const name = asString(row.name)
      return name
        ? [
            {
              name,
              boardCode: asString(row.board_code) ?? "",
              count: asNumber(row.count) ?? 0,
              marketCap: asNumber(row.market_cap),
              weightPct: asNumber(row.weight_pct),
              weightedChangePct: asNumber(row.weighted_change_pct),
            },
          ]
        : []
    }),
    unsupported: strings(data.unsupported),
    rules: strings(data.rules),
    sources: listOf(data.sources).flatMap((row) => {
      const name = asString(row.name)
      return name ? [{ name, url: asString(row.url) ?? "", fetchedAt: asString(row.fetched_at) ?? "" }] : []
    }),
  }
}

function parseStock(row: Record<string, unknown>): WatchStock[] {
  const instrumentId = asString(row.instrument_id)
  const code = asString(row.code)
  if (!instrumentId || !code) {
    return []
  }
  return [
    {
      instrumentId,
      displaySymbol: asString(row.display_symbol) ?? code,
      code,
      name: asString(row.name) ?? code,
      exchange: asString(row.exchange) ?? "",
      origin: asString(row.origin) ?? "",
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
      industry: asString(row.industry) ?? "",
      boardCode: asString(row.board_code) ?? "",
      limitStatus: asString(row.limit_status) ?? "",
      streak: asNumber(row.streak),
      surveys: asNumber(row.surveys) ?? 0,
      surveyedInstitutions: asNumber(row.surveyed_institutions) ?? 0,
      thsHotRank: asNumber(row.ths_hot_rank),
      emHotRank: asNumber(row.em_hot_rank),
    },
  ]
}

function strings(value: unknown): string[] {
  return asList(value).flatMap((item) => (typeof item === "string" ? [item] : []))
}
